"""Test offline: mapping FHIR validati + flusso completo su SQLite e server FHIR simulato."""
from __future__ import annotations

import pytest
from fhir.resources.R4B import get_fhir_model_class

from erpfhir import flows
from erpfhir.config import Settings
from erpfhir.db import SqliteErp
from erpfhir.fhir_client import FhirClient, FhirError
from erpfhir.mappers import build_surgery_bundle, item_to_device_definition, ref_id
from fake_fhir import BASE, FakeFhirServer, make_session


@pytest.fixture()
def env(tmp_path):
    settings = Settings(fhir_base_url=BASE, demo_tag="test-run", db_backend="sqlite",
                        sqlite_path=str(tmp_path / "erp.db"), organization_id=101)
    server = FakeFhirServer(page_size=3)  # pagine piccole: verifica anche la paginazione
    fhir = FhirClient(BASE, session=make_session(server))
    erp = SqliteErp(settings.sqlite_path)
    erp.init_schema(seed=True)
    yield settings, server, fhir, erp
    erp.close()


def test_mapping_resources_are_valid_fhir():
    dd = item_to_device_definition({"segment1": "X1", "description": "Prova", "gtin": "08009999000017",
                                    "manufacturer_name": "ACME"}, "t")
    get_fhir_model_class("DeviceDefinition").model_validate(dd)
    bundle, ids = build_surgery_bundle("t", "7", "Intervento", "2026-10-13T08:00:00+00:00",
                                       [{"device_definition_id": "1", "gtin": "08009999000017", "lot": "L1",
                                         "quantity": 2, "description": "Prova"}])
    get_fhir_model_class("Bundle").model_validate(bundle)
    assert len(ids) == 1
    assert [e["resource"]["resourceType"] for e in bundle["entry"]] == \
        ["Patient", "Encounter", "Device", "Procedure", "ChargeItem"]


def test_validator_rejects_invalid_resource():
    """Garantisce che la validazione usata nei test non sia permissiva."""
    with pytest.raises(Exception):
        get_fhir_model_class("ChargeItem").model_validate({"resourceType": "ChargeItem", "status": "billable"})
    with pytest.raises(Exception):
        get_fhir_model_class("Location").model_validate({"resourceType": "Location", "campoInventato": 1})


def test_ref_id():
    assert ref_id("Device/12") == "12"
    assert ref_id("http://srv/fhir/Device/12/_history/3") == "12"
    assert ref_id(None) is None


def test_end_to_end(env):
    settings, server, fhir, erp = env

    # Flusso 1 (eseguito due volte: il conditional update non deve creare duplicati)
    assert flows.sync_master_data(settings, erp, fhir) == {"locations": 3, "device_definitions": 6}
    flows.sync_master_data(settings, erp, fhir)
    assert len(server.store["Location"]) == 3
    assert len(server.store["DeviceDefinition"]) == 6

    # Flusso 2: intervento con 6 consumi + 1 consumo che eccede la giacenza
    sim = flows.simulate_surgery(settings, erp, fhir, with_error=True)
    assert sim["charge_items"] == 7
    assert len(server.store["ChargeItem"]) == 7

    # Flusso 3 (con paginazione a 3 elementi)
    imp = flows.import_consumption(settings, erp, fhir)
    assert imp["charge_items_read"] == 7 and imp["staging_inserted"] == 7
    assert any("_getpagesoffset" in r for r in server.requests)

    # Flusso 4
    assert flows.process_consumption(settings, erp) == {"processed_stock": 3, "processed_bill_only": 3, "errors": 1}

    rep = erp.report(101)
    onhand = {(g["articolo"], g["lotto"]): g["quantita"] for g in rep["giacenze"]}
    assert onhand[("ART-0001", "LOT-G-2026A")] == 196
    assert onhand[("ART-0002", "LOT-S-2026B")] == 57
    assert onhand[("ART-0003", "LOT-T-2026C")] == 38
    assert len(rep["movimenti"]) == 3 and all(m["quantita"] < 0 for m in rep["movimenti"])
    req = {r["articolo"]: r for r in rep["richieste_bill_only"]}
    assert set(req) == {"PRT-1001", "PRT-1002", "PRT-1003"}
    assert req["PRT-1001"]["prezzo"] == 1450 and req["PRT-1001"]["lotto"] == "OD-ST3-26110"
    assert req["PRT-1001"]["fornitore"].startswith("OrthoDemo")
    assert len(rep["staging_errori"]) == 1
    assert "Giacenza insufficiente" in rep["staging_errori"][0]["error_message"]

    # Idempotenza: nuova lettura e nuova elaborazione non duplicano nulla
    assert flows.import_consumption(settings, erp, fhir)["staging_inserted"] == 0
    assert flows.process_consumption(settings, erp) == {"processed_stock": 0, "processed_bill_only": 0, "errors": 1}
    rep2 = erp.report(101)
    assert len(rep2["movimenti"]) == 3 and len(rep2["richieste_bill_only"]) == 3

    # Un secondo intervento viene letto in modo incrementale (solo i nuovi ChargeItem)
    flows.simulate_surgery(settings, erp, fhir)
    assert flows.import_consumption(settings, erp, fhir)["staging_inserted"] == 6
    assert flows.process_consumption(settings, erp)["processed_bill_only"] == 3


def test_other_tag_does_not_overwrite(env):
    """Due copie del progetto sullo stesso server pubblico non si sovrascrivono a vicenda."""
    settings, server, fhir, erp = env
    flows.sync_master_data(settings, erp, fhir)
    other = Settings(fhir_base_url=BASE, demo_tag="altro-utente", db_backend="sqlite",
                     sqlite_path=settings.sqlite_path + ".other", organization_id=101)
    erp2 = SqliteErp(other.sqlite_path)
    erp2.init_schema(seed=True)
    flows.sync_master_data(other, erp2, fhir)
    erp2.close()
    assert len(server.store["DeviceDefinition"]) == 12
    assert len(server.store["Location"]) == 6


def test_server_error_is_reported(env):
    settings, server, fhir, erp = env
    with pytest.raises(FhirError, match="HTTP 400"):
        fhir.conditional_update({"resourceType": "Location", "campoInventato": 1}, "s", "v")


def test_simulation_requires_master_data(env):
    settings, server, fhir, erp = env
    with pytest.raises(RuntimeError, match="sync-master"):
        flows.simulate_surgery(settings, erp, fhir)
