"""Riga di comando:  python -m erpfhir <comando>   (dalla cartella src/ o con PYTHONPATH=src)"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .config import PROJECT_ROOT, Settings


def _load_dotenv() -> None:
    """Carica .env dalla radice del progetto (senza dipendenze esterne)."""
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.split(" #", 1)[0].strip()  # commenti in linea
            if value:
                os.environ.setdefault(key.strip(), value)


def _print_table(title: str, rows) -> None:
    print(f"\n== {title} ({len(rows)})")
    if not rows:
        print("   (nessuna riga)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("   " + "  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("   " + "  ".join(str(r[c]).ljust(widths[c]) for c in cols))


def main(argv=None) -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(prog="erpfhir", description="Demo integrazione ERP <-> FHIR R4 (logistica sanitaria)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="verifica la connessione al server FHIR")
    p_init = sub.add_parser("init-db", help="crea lo schema ERP e carica i dati di esempio")
    p_init.add_argument("--no-seed", action="store_true")
    sub.add_parser("sync-master", help="flusso 1: ERP -> FHIR (Location, DeviceDefinition)")
    p_sim = sub.add_parser("simulate-surgery", help="flusso 2: simula un intervento documentato nel sistema clinico")
    p_sim.add_argument("--with-error", action="store_true", help="aggiunge un consumo che genera un errore gestito")
    sub.add_parser("import", help="flusso 3: FHIR -> staging ERP (ChargeItem)")
    sub.add_parser("process", help="flusso 4: elaborazione ERP (scarico / bill-only)")
    sub.add_parser("report", help="mostra giacenze, movimenti, richieste bill-only, errori")
    p_demo = sub.add_parser("demo", help="esegue in sequenza tutti i flussi")
    p_demo.add_argument("--with-error", action="store_true")
    args = parser.parse_args(argv)

    settings = Settings()
    from .db import connect
    from .fhir_client import FhirClient
    from . import flows

    fhir = FhirClient(settings.fhir_base_url, settings.http_timeout)
    if args.cmd == "check":
        cap = fhir.capability()
        print(f"Server: {settings.fhir_base_url}\nSoftware: {cap.get('software', {}).get('name')} "
              f"{cap.get('software', {}).get('version', '')}\nFHIR: {cap.get('fhirVersion')}")
        return 0

    erp = connect(settings)
    try:
        if args.cmd == "init-db":
            erp.init_schema(seed=not args.no_seed)
            print("Schema ERP creato" + ("" if args.no_seed else " e dati di esempio caricati"))
        elif args.cmd == "sync-master":
            print(json.dumps(flows.sync_master_data(settings, erp, fhir), indent=2))
        elif args.cmd == "simulate-surgery":
            print(json.dumps(flows.simulate_surgery(settings, erp, fhir, args.with_error), indent=2))
        elif args.cmd == "import":
            print(json.dumps(flows.import_consumption(settings, erp, fhir), indent=2))
        elif args.cmd == "process":
            print(json.dumps(flows.process_consumption(settings, erp), indent=2))
        elif args.cmd == "report":
            for title, rows in erp.report(settings.organization_id).items():
                _print_table(title, rows)
        elif args.cmd == "demo":
            if settings.db_backend == "sqlite":
                erp.init_schema(seed=True)  # idempotente su SQLite
            print(f"Server FHIR: {settings.fhir_base_url}  |  tag dati demo: {settings.demo_tag}")
            print("1) Anagrafiche ERP -> FHIR:", flows.sync_master_data(settings, erp, fhir))
            sim = flows.simulate_surgery(settings, erp, fhir, args.with_error)
            print("2) Intervento simulato nel sistema clinico:", {k: sim[k] for k in ("charge_items", "resources_created")})
            print("3) Consumi FHIR -> staging ERP:", flows.import_consumption(settings, erp, fhir))
            print("4) Elaborazione ERP:", flows.process_consumption(settings, erp))
            for title, rows in erp.report(settings.organization_id).items():
                _print_table(title, rows)
    finally:
        erp.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
