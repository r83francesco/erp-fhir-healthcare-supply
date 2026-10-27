"""I quattro flussi dell'integrazione ERP <-> sistema clinico (FHIR R4)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from .config import SID_ERP_ITEM, SID_ERP_SUBINV, TAG_SYSTEM, Settings
from .db import ErpRepository
from .fhir_client import FhirClient
from .mappers import (build_surgery_bundle, charge_item_to_staging, item_to_device_definition, ref_id,
                      subinventory_to_location)

FLOW_CONSUMPTION = "CONSUMPTION_IMPORT"


# --------------------------------------------------------------------------------------------
# Flusso 1 - ERP -> FHIR: anagrafiche (sottoinventari -> Location, articoli -> DeviceDefinition)
# --------------------------------------------------------------------------------------------
def sync_master_data(settings: Settings, erp: ErpRepository, fhir: FhirClient) -> Dict[str, int]:
    org = settings.organization_id
    # il filtro sul tag limita il match ai NOSTRI dati (il server pubblico e' condiviso)
    scope = {"_tag": f"{TAG_SYSTEM}|{settings.demo_tag}"}
    n_loc = n_items = 0
    for sub in erp.subinventories(org):
        res = fhir.conditional_update(subinventory_to_location(sub, settings.demo_tag),
                                      SID_ERP_SUBINV, sub["subinventory_code"], scope)
        erp.set_location_id(org, sub["subinventory_code"], res["id"])
        n_loc += 1
    for item in erp.items(org):
        res = fhir.conditional_update(item_to_device_definition(item, settings.demo_tag),
                                      SID_ERP_ITEM, item["segment1"], scope)
        erp.set_item_fhir_id(item["inventory_item_id"], res["id"])
        n_items += 1
    erp.commit()
    return {"locations": n_loc, "device_definitions": n_items}


# --------------------------------------------------------------------------------------------
# Flusso 2 - Simulazione del sistema clinico: intervento con consumo di dispositivi
# (nella realta' lo farebbe il personale di sala operatoria nell'EHR, es. Oracle Health)
# --------------------------------------------------------------------------------------------
HIP_SURGERY = [  # (codice articolo, lotto, quantita')
    ("ART-0001", "LOT-G-2026A", 4),     # guanti (a scorta)
    ("ART-0002", "LOT-S-2026B", 3),     # suture (a scorta)
    ("ART-0003", "LOT-T-2026C", 2),     # teli (a scorta)
    ("PRT-1001", "OD-ST3-26110", 1),    # stelo femorale (conto deposito)
    ("PRT-1002", "OD-TC32-26087", 1),   # testa femorale (conto deposito)
    ("PRT-1003", "OD-CT52-26102", 1),   # cotile (conto deposito)
]


def simulate_surgery(settings: Settings, erp: ErpRepository, fhir: FhirClient,
                     with_error: bool = False) -> Dict[str, Any]:
    org = settings.organization_id
    items = {i["segment1"]: i for i in erp.items(org)}
    subs = {s["subinventory_code"]: s for s in erp.subinventories(org)}
    location_id = subs.get("BO-SALA1", {}).get("fhir_location_id")
    if not location_id:
        raise RuntimeError("Anagrafiche non sincronizzate: eseguire prima 'sync-master'.")

    lines = list(HIP_SURGERY)
    if with_error:
        lines.append(("ART-0002", "LOT-S-2026B", 999))   # giacenza insufficiente -> errore gestito

    consumptions: List[Dict[str, Any]] = []
    for code, lot, qty in lines:
        item = items[code]
        if not item.get("fhir_device_definition_id"):
            raise RuntimeError(f"Articolo {code} non sincronizzato: eseguire prima 'sync-master'.")
        consumptions.append({"device_definition_id": item["fhir_device_definition_id"], "gtin": item["gtin"],
                             "lot": lot, "quantity": qty, "description": item["description"]})

    performed = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    bundle, charge_ids = build_surgery_bundle(
        settings.demo_tag, location_id, "Artroprotesi d'anca (intervento dimostrativo)", performed, consumptions)
    response = fhir.transaction(bundle)
    created = [e.get("response", {}).get("location", "") for e in response.get("entry", [])]
    return {"charge_items": len(charge_ids), "resources_created": len(created), "locations": created}


# --------------------------------------------------------------------------------------------
# Flusso 3 - FHIR -> ERP: lettura incrementale dei consumi (ChargeItem) nello staging
# --------------------------------------------------------------------------------------------
def import_consumption(settings: Settings, erp: ErpRepository, fhir: FhirClient) -> Dict[str, Any]:
    params = {"_tag": f"{TAG_SYSTEM}|{settings.demo_tag}", "_include": "ChargeItem:service",
              "_count": "50", "_sort": "_lastUpdated"}
    watermark = erp.get_watermark(FLOW_CONSUMPTION)
    if watermark:
        params["_lastUpdated"] = f"gt{watermark}"

    charges: List[Dict[str, Any]] = []
    procedures: Dict[str, Dict[str, Any]] = {}
    for entry in fhir.search("ChargeItem", params):
        res = entry.get("resource", {})
        if res.get("resourceType") == "ChargeItem":
            charges.append(res)
        elif res.get("resourceType") == "Procedure":
            procedures[res["id"]] = res

    # Procedure non restituite tramite _include (fallback) e Device (ChargeItem non ha un
    # parametro di ricerca sul prodotto, quindi i Device si leggono per _id)
    missing_proc = {ref_id((c.get("service") or [{}])[0].get("reference")) for c in charges} - set(procedures)
    missing_proc.discard(None)
    for rid in _chunks(sorted(missing_proc)):
        for p in fhir.search_resources("Procedure", {"_id": ",".join(rid)}):
            procedures[p["id"]] = p
    device_ids = sorted({ref_id(c.get("productReference", {}).get("reference")) for c in charges} - {None})
    devices: Dict[str, Dict[str, Any]] = {}
    for rid in _chunks(device_ids):
        for d in fhir.search_resources("Device", {"_id": ",".join(rid)}):
            devices[d["id"]] = d

    rows = [charge_item_to_staging(c, devices, procedures) for c in charges]
    inserted = erp.insert_staging(rows)
    last_updated = max((c.get("meta", {}).get("lastUpdated", "") for c in charges), default="")
    if last_updated:
        erp.set_watermark(FLOW_CONSUMPTION, last_updated)
    erp.commit()
    return {"charge_items_read": len(charges), "staging_inserted": inserted,
            "watermark": last_updated or watermark}


# --------------------------------------------------------------------------------------------
# Flusso 4 - Elaborazione ERP: scarico di magazzino o richiesta bill-only
# --------------------------------------------------------------------------------------------
def process_consumption(settings: Settings, erp: ErpRepository) -> Dict[str, int]:
    return erp.process_staging(settings.organization_id)


def _chunks(values: List[str], size: int = 50):
    for i in range(0, len(values), size):
        yield values[i:i + size]
