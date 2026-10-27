"""Mapping tra righe ERP e risorse FHIR R4 (funzioni pure, facili da testare)."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .config import (SID_DEMO_CHARGE, SID_ERP_ITEM, SID_ERP_SUBINV, SID_GTIN, TAG_SYSTEM,
                     UDI_ISSUER_GS1)

# Giurisdizione UDI europea: uniqueId "http://hl7.org/fhir/NamingSystem/eu-ec-udi"
# (ripubblicato p.es. dalla IG svizzera CH EMR come "UDI jurisdiction for the European Commission")
UDI_JURISDICTION_EU = "http://hl7.org/fhir/NamingSystem/eu-ec-udi"


def demo_meta(tag: str) -> Dict[str, Any]:
    return {"tag": [{"system": TAG_SYSTEM, "code": tag}]}


def ref_id(reference: Optional[str]) -> Optional[str]:
    """'Device/123', 'Device/123/_history/2' o URL assoluto -> '123'."""
    if not reference:
        return None
    parts = reference.rstrip("/").split("/")
    if "_history" in parts:
        parts = parts[: parts.index("_history")]
    return parts[-1]


# --------------------------------------------------------------------------- 1. anagrafiche
def item_to_device_definition(item: Mapping[str, Any], tag: str) -> Dict[str, Any]:
    """Articolo ERP (MTL_SYSTEM_ITEMS_B) -> DeviceDefinition (catalogo dispositivi)."""
    resource: Dict[str, Any] = {
        "resourceType": "DeviceDefinition",
        "meta": demo_meta(tag),
        "identifier": [{"system": SID_ERP_ITEM, "value": item["segment1"]}],
        "deviceName": [{"name": item["description"], "type": "user-friendly-name"}],
    }
    if item.get("gtin"):
        resource["identifier"].append({"system": SID_GTIN, "value": item["gtin"]})
        resource["udiDeviceIdentifier"] = [{
            "deviceIdentifier": item["gtin"],
            "issuer": UDI_ISSUER_GS1,
            "jurisdiction": UDI_JURISDICTION_EU,
        }]
    if item.get("manufacturer_name"):
        resource["manufacturerString"] = item["manufacturer_name"]
    return resource


def subinventory_to_location(sub: Mapping[str, Any], tag: str) -> Dict[str, Any]:
    """Sottoinventario ERP -> Location FHIR."""
    return {
        "resourceType": "Location",
        "meta": demo_meta(tag),
        "identifier": [{"system": SID_ERP_SUBINV, "value": sub["subinventory_code"]}],
        "status": "active",
        "name": sub["description"] or sub["subinventory_code"],
        "mode": "instance",
    }


# ------------------------------------------------------------ 2. documentazione clinica (EHR)
def gs1_hrf(gtin: str, lot: str) -> str:
    """Stringa UDI leggibile GS1: (01)GTIN(10)LOTTO."""
    return f"(01){gtin}(10){lot}"


def build_surgery_bundle(
    tag: str,
    location_id: str,
    procedure_text: str,
    performed: str,
    consumptions: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[str]]:
    """Simula cio' che il sistema clinico registra in sala operatoria.

    consumptions: [{device_definition_id, gtin, lot, quantity, description}]
    Restituisce (Bundle transaction, lista identificativi dei ChargeItem creati).
    """
    meta = demo_meta(tag)

    def urn() -> str:
        return f"urn:uuid:{uuid.uuid4()}"

    patient_u, encounter_u, procedure_u = urn(), urn(), urn()
    entries: List[Dict[str, Any]] = []

    def add(full_url: str, resource: Dict[str, Any]) -> None:
        entries.append({"fullUrl": full_url, "resource": resource,
                        "request": {"method": "POST", "url": resource["resourceType"]}})

    add(patient_u, {
        "resourceType": "Patient", "meta": meta,
        "name": [{"family": "Paziente", "given": ["Demo"], "text": "Paziente Demo (fittizio)"}],
        "gender": "unknown",
    })
    add(encounter_u, {
        "resourceType": "Encounter", "meta": meta, "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "IMP",
                  "display": "inpatient encounter"},
        "subject": {"reference": patient_u},
        "period": {"start": performed},
    })

    device_urns: List[str] = []
    charge_ids: List[str] = []
    charge_entries: List[Tuple[str, Dict[str, Any]]] = []
    for c in consumptions:
        dev_u = urn()
        device_urns.append(dev_u)
        add(dev_u, {
            "resourceType": "Device", "meta": meta, "status": "active",
            "definition": {"reference": f"DeviceDefinition/{c['device_definition_id']}"},
            "udiCarrier": [{
                "deviceIdentifier": c["gtin"], "issuer": UDI_ISSUER_GS1,
                "jurisdiction": UDI_JURISDICTION_EU, "carrierHRF": gs1_hrf(c["gtin"], c["lot"]),
            }],
            "lotNumber": c["lot"],
            "deviceName": [{"name": c["description"], "type": "user-friendly-name"}],
            "location": {"reference": f"Location/{location_id}"},
        })
        charge_id = str(uuid.uuid4())
        charge_ids.append(charge_id)
        charge_entries.append((urn(), {
            "resourceType": "ChargeItem", "meta": meta,
            "identifier": [{"system": SID_DEMO_CHARGE, "value": charge_id}],
            "status": "billable",
            "code": {"text": "Consumo dispositivo medico in sala operatoria"},
            "subject": {"reference": patient_u},
            "context": {"reference": encounter_u},
            "occurrenceDateTime": performed,
            "quantity": {"value": c["quantity"]},
            "productReference": {"reference": dev_u},
            "service": [{"reference": procedure_u}],
            "enteredDate": performed,
        }))

    add(procedure_u, {
        "resourceType": "Procedure", "meta": meta, "status": "completed",
        "code": {"text": procedure_text},
        "subject": {"reference": patient_u},
        "encounter": {"reference": encounter_u},
        "performedDateTime": performed,
        "location": {"reference": f"Location/{location_id}"},
        "usedReference": [{"reference": u} for u in device_urns],
    })
    for full_url, res in charge_entries:
        add(full_url, res)

    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}, charge_ids


# -------------------------------------------------------------- 3. consumi -> staging ERP
def charge_item_to_staging(
    charge: Mapping[str, Any],
    devices: Mapping[str, Mapping[str, Any]],
    procedures: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """ChargeItem (+ Device e Procedure collegati) -> riga di XX_FHIR_CONSUMPTION_STG."""
    device = devices.get(ref_id(charge.get("productReference", {}).get("reference")) or "", {})
    services = charge.get("service") or [{}]
    procedure = procedures.get(ref_id(services[0].get("reference")) or "", {})
    udi = (device.get("udiCarrier") or [{}])[0]
    location_ref = procedure.get("location", {}).get("reference") or device.get("location", {}).get("reference")
    return {
        "charge_item_id": charge["id"],
        "gtin": udi.get("deviceIdentifier"),
        "lot_number": device.get("lotNumber"),
        "quantity": charge.get("quantity", {}).get("value"),
        "location_id": ref_id(location_ref),
        "encounter_ref": charge.get("context", {}).get("reference"),
        "procedure_ref": services[0].get("reference"),
        "occurrence_date": charge.get("occurrenceDateTime"),
    }
