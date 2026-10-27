"""Accesso al database ERP: SQLite (demo rapida) oppure Oracle (package PL/SQL).

Le regole di elaborazione dei consumi sono le stesse nei due backend:
  - Oracle: package XX_FHIR_CONS_PKG (sql/oracle/03_xx_fhir_cons_pkg.sql)
  - SQLite: SqliteErp.process_staging(), che replica la stessa logica
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

from .config import PROJECT_ROOT, Settings

SQL_DIR = PROJECT_ROOT / "sql"


class ErpRepository:
    """Metodi comuni (SQL portabile con parametri :nome)."""

    conn: Any

    # ---------------------------------------------------------------- utilita'
    def _rows(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(sql, params or {})
        cols = [d[0].lower() for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _exec(self, sql: str, params: Optional[Dict[str, Any]] = None) -> int:
        cur = self.conn.cursor()
        cur.execute(sql, params or {})
        return cur.rowcount

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -------------------------------------------------------------- anagrafiche
    def items(self, organization_id: int) -> List[Dict[str, Any]]:
        return self._rows(
            "SELECT inventory_item_id, segment1, description, gtin, manufacturer_name, "
            "stock_enabled_flag, fhir_device_definition_id FROM mtl_system_items_b "
            "WHERE organization_id = :org ORDER BY segment1", {"org": organization_id})

    def set_item_fhir_id(self, item_id: int, fhir_id: str) -> None:
        self._exec(f"UPDATE mtl_system_items_b SET fhir_device_definition_id = :fid, "
                   f"last_fhir_sync_date = {self.NOW} WHERE inventory_item_id = :id",
                   {"fid": fhir_id, "id": item_id})

    def subinventories(self, organization_id: int) -> List[Dict[str, Any]]:
        return self._rows(
            "SELECT subinventory_code, description, fhir_location_id FROM mtl_secondary_inventories "
            "WHERE organization_id = :org ORDER BY subinventory_code", {"org": organization_id})

    def set_location_id(self, organization_id: int, code: str, fhir_id: str) -> None:
        self._exec("UPDATE mtl_secondary_inventories SET fhir_location_id = :fid "
                   "WHERE organization_id = :org AND subinventory_code = :code",
                   {"fid": fhir_id, "org": organization_id, "code": code})

    # ---------------------------------------------------------------- watermark
    def get_watermark(self, flow: str) -> Optional[str]:
        rows = self._rows("SELECT last_run_date FROM xx_fhir_sync_control WHERE flow_name = :f", {"f": flow})
        return rows[0]["last_run_date"] if rows else None

    def set_watermark(self, flow: str, value: str) -> None:
        if self._exec("UPDATE xx_fhir_sync_control SET last_run_date = :v WHERE flow_name = :f",
                      {"v": value, "f": flow}) == 0:
            self._exec("INSERT INTO xx_fhir_sync_control (flow_name, last_run_date) VALUES (:f, :v)",
                       {"v": value, "f": flow})

    # ------------------------------------------------------------------ staging
    def insert_staging(self, rows: Iterable[Dict[str, Any]]) -> int:
        """Inserisce solo i ChargeItem non ancora caricati (idempotente)."""
        inserted = 0
        for r in rows:
            exists = self._rows("SELECT 1 AS x FROM xx_fhir_consumption_stg WHERE charge_item_id = :id",
                                {"id": r["charge_item_id"]})
            if exists:
                continue
            self._exec(
                "INSERT INTO xx_fhir_consumption_stg (charge_item_id, gtin, lot_number, quantity, "
                "location_id, encounter_ref, procedure_ref, occurrence_date, status, load_date) VALUES "
                "(:charge_item_id, :gtin, :lot_number, :quantity, :location_id, :encounter_ref, "
                f":procedure_ref, :occurrence_date, 'NEW', {self.NOW})", r)
            inserted += 1
        return inserted

    # ------------------------------------------------------------------- report
    def report(self, organization_id: int) -> Dict[str, List[Dict[str, Any]]]:
        p = {"org": organization_id}
        return {
            "giacenze": self._rows(
                "SELECT i.segment1 AS articolo, o.subinventory_code AS sottoinv, o.lot_number AS lotto, "
                "o.transaction_quantity AS quantita FROM mtl_onhand_quantities_detail o "
                "JOIN mtl_system_items_b i ON i.inventory_item_id = o.inventory_item_id "
                "WHERE o.organization_id = :org ORDER BY 1, 2, 3", p),
            "movimenti": self._rows(
                "SELECT t.transaction_id AS id, i.segment1 AS articolo, t.lot_number AS lotto, "
                "t.transaction_quantity AS quantita, t.transaction_type_name AS tipo, "
                "t.transaction_reference AS riferimento FROM mtl_material_transactions t "
                "JOIN mtl_system_items_b i ON i.inventory_item_id = t.inventory_item_id "
                "WHERE t.organization_id = :org ORDER BY t.transaction_id", p),
            "richieste_bill_only": self._rows(
                "SELECT r.interface_id AS id, i.segment1 AS articolo, r.quantity AS quantita, "
                "r.unit_price AS prezzo, v.vendor_name AS fornitore, r.lot_number AS lotto, "
                "r.source_reference AS riferimento FROM po_requisitions_interface_all r "
                "JOIN mtl_system_items_b i ON i.inventory_item_id = r.item_id "
                "LEFT JOIN po_vendors v ON v.vendor_id = r.suggested_vendor_id "
                "WHERE r.destination_organization_id = :org ORDER BY r.interface_id", p),
            "staging_errori": self._rows(
                "SELECT charge_item_id, gtin, lot_number, quantity, error_message "
                "FROM xx_fhir_consumption_stg WHERE status = 'ERROR' ORDER BY charge_item_id"),
        }


# =============================================================================
class SqliteErp(ErpRepository):
    NOW = "datetime('now')"

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA foreign_keys = ON")

    def init_schema(self, seed: bool = True) -> None:
        self.conn.executescript((SQL_DIR / "sqlite" / "schema.sql").read_text(encoding="utf-8"))
        if seed:
            self.conn.executescript((SQL_DIR / "sqlite" / "seed.sql").read_text(encoding="utf-8"))
        self.conn.commit()

    def process_staging(self, organization_id: int) -> Dict[str, int]:
        """Stessa logica di XX_FHIR_CONS_PKG.process_staging (vedi sql/oracle)."""
        summary = {"processed_stock": 0, "processed_bill_only": 0, "errors": 0}
        rows = self._rows("SELECT * FROM xx_fhir_consumption_stg WHERE status IN ('NEW','ERROR') "
                          "ORDER BY load_date, charge_item_id")
        for r in rows:
            self.conn.execute("SAVEPOINT stg_row")
            try:
                kind = self._process_row(organization_id, r)
                self._exec("UPDATE xx_fhir_consumption_stg SET status = 'PROCESSED', error_message = NULL, "
                           f"processed_date = {self.NOW} WHERE charge_item_id = :id", {"id": r["charge_item_id"]})
                self.conn.execute("RELEASE SAVEPOINT stg_row")
                summary["processed_stock" if kind == "STOCK" else "processed_bill_only"] += 1
            except ValueError as exc:
                self.conn.execute("ROLLBACK TO SAVEPOINT stg_row")
                self.conn.execute("RELEASE SAVEPOINT stg_row")
                self._exec("UPDATE xx_fhir_consumption_stg SET status = 'ERROR', error_message = :m "
                           "WHERE charge_item_id = :id", {"m": str(exc)[:240], "id": r["charge_item_id"]})
                summary["errors"] += 1
        self.conn.commit()
        return summary

    def _process_row(self, org: int, r: Dict[str, Any]) -> str:
        ref = f"ChargeItem/{r['charge_item_id']}"
        items = self._rows("SELECT * FROM mtl_system_items_b WHERE gtin = :g AND organization_id = :o",
                           {"g": r["gtin"], "o": org})
        if not items:
            raise ValueError(f"Articolo non trovato per GTIN {r['gtin']}")
        item = items[0]
        subs = self._rows("SELECT subinventory_code FROM mtl_secondary_inventories "
                          "WHERE fhir_location_id = :l AND organization_id = :o", {"l": r["location_id"], "o": org})
        if not subs:
            raise ValueError(f"Nessun sottoinventario associato alla Location {r['location_id']}")
        sub = subs[0]["subinventory_code"]
        qty = r["quantity"]
        if qty is None or qty <= 0:
            raise ValueError("Quantita' mancante o non positiva")

        if item["stock_enabled_flag"] == "Y":
            if not r["lot_number"]:
                raise ValueError("Lotto obbligatorio per articolo a scorta")
            onhand = self._rows(
                "SELECT transaction_quantity FROM mtl_onhand_quantities_detail WHERE organization_id = :o "
                "AND inventory_item_id = :i AND subinventory_code = :s AND lot_number = :l",
                {"o": org, "i": item["inventory_item_id"], "s": sub, "l": r["lot_number"]})
            available = onhand[0]["transaction_quantity"] if onhand else 0
            if available < qty:
                raise ValueError(f"Giacenza insufficiente: disponibili {available}, richiesti {qty} "
                                 f"({item['segment1']} lotto {r['lot_number']} in {sub})")
            self._exec("UPDATE mtl_onhand_quantities_detail SET transaction_quantity = transaction_quantity - :q "
                       "WHERE organization_id = :o AND inventory_item_id = :i AND subinventory_code = :s "
                       "AND lot_number = :l",
                       {"q": qty, "o": org, "i": item["inventory_item_id"], "s": sub, "l": r["lot_number"]})
            self._exec(
                "INSERT INTO mtl_material_transactions (organization_id, inventory_item_id, subinventory_code, "
                "lot_number, transaction_quantity, transaction_type_name, transaction_reference, transaction_date) "
                f"VALUES (:o, :i, :s, :l, :q, 'Scarico a paziente', :ref, {self.NOW})",
                {"o": org, "i": item["inventory_item_id"], "s": sub, "l": r["lot_number"], "q": -qty, "ref": ref})
            return "STOCK"

        # conto deposito -> richiesta bill-only (con controllo duplicati sul riferimento)
        if self._rows("SELECT 1 AS x FROM po_requisitions_interface_all WHERE source_reference = :ref", {"ref": ref}):
            return "BILL_ONLY"
        self._exec(
            "INSERT INTO po_requisitions_interface_all (interface_source_code, requisition_type, item_id, quantity, "
            "unit_price, suggested_vendor_id, destination_organization_id, destination_subinventory, lot_number, "
            "note_to_buyer, source_reference, process_flag, creation_date) VALUES ('FHIR', 'BILL_ONLY', :i, :q, "
            f":p, :v, :o, :s, :l, :note, :ref, 'PENDING', {self.NOW})",
            {"i": item["inventory_item_id"], "q": qty, "p": item["last_contract_price"],
             "v": item["consigned_vendor_id"], "o": org, "s": sub, "l": r["lot_number"],
             "note": f"Bill-only: impianto in conto deposito. {r['procedure_ref']} / {r['encounter_ref']}",
             "ref": ref})
        return "BILL_ONLY"


# =============================================================================
class OracleErp(ErpRepository):
    NOW = "SYSTIMESTAMP"

    def __init__(self, user: str, password: str, dsn: str):
        import oracledb  # import ritardato: serve solo con DB_BACKEND=oracle

        self.conn = oracledb.connect(user=user, password=password, dsn=dsn)

    def init_schema(self, seed: bool = True) -> None:
        """Esegue gli script Oracle: ogni istruzione termina con una riga contenente solo '/'."""
        files = ["01_schema.sql", "03_xx_fhir_cons_pkg.sql"] + (["02_seed.sql"] if seed else [])
        cur = self.conn.cursor()
        for name in files:
            text = (SQL_DIR / "oracle" / name).read_text(encoding="utf-8")
            for stmt in re.split(r"^\s*/\s*$", text, flags=re.MULTILINE):
                body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
                if body:
                    cur.execute(body)
        self.conn.commit()

    def process_staging(self, organization_id: int) -> Dict[str, int]:
        cur = self.conn.cursor()
        n_stock, n_bill, n_err = cur.var(int), cur.var(int), cur.var(int)
        cur.callproc("xx_fhir_cons_pkg.process_staging", [organization_id, n_stock, n_bill, n_err])
        self.conn.commit()
        return {"processed_stock": n_stock.getvalue(), "processed_bill_only": n_bill.getvalue(),
                "errors": n_err.getvalue()}


def connect(settings: Settings) -> ErpRepository:
    if settings.db_backend == "oracle":
        return OracleErp(settings.oracle_user, settings.oracle_password, settings.oracle_dsn)
    if settings.db_backend == "sqlite":
        return SqliteErp(settings.sqlite_path)
    raise ValueError(f"DB_BACKEND non supportato: {settings.db_backend}")
