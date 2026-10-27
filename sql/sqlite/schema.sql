-- =============================================================================
-- Schema ERP semplificato (SQLite) ispirato alle tabelle Oracle EBS R12
-- Inventory / Purchasing. Solo le colonne necessarie alla demo.
-- La versione Oracle (con package PL/SQL) e' in sql/oracle/.
-- =============================================================================

-- Fornitori (cfr. PO_VENDORS / AP_SUPPLIERS)
CREATE TABLE IF NOT EXISTS po_vendors (
    vendor_id        INTEGER PRIMARY KEY,
    vendor_name      TEXT    NOT NULL,
    vat_number       TEXT
);

-- Sottoinventari: magazzini e depositi di reparto (cfr. MTL_SECONDARY_INVENTORIES)
CREATE TABLE IF NOT EXISTS mtl_secondary_inventories (
    organization_id    INTEGER NOT NULL,
    subinventory_code  TEXT    NOT NULL,
    description        TEXT,
    fhir_location_id   TEXT,               -- id della Location sul server FHIR
    PRIMARY KEY (organization_id, subinventory_code)
);

-- Anagrafica articoli (cfr. MTL_SYSTEM_ITEMS_B)
-- stock_enabled_flag = 'Y' -> articolo a scorta (consumo = scarico di magazzino)
-- stock_enabled_flag = 'N' -> conto deposito (consumo = richiesta "bill-only")
CREATE TABLE IF NOT EXISTS mtl_system_items_b (
    inventory_item_id        INTEGER PRIMARY KEY,
    organization_id          INTEGER NOT NULL,
    segment1                 TEXT    NOT NULL UNIQUE,   -- codice articolo
    description              TEXT    NOT NULL,
    primary_uom_code         TEXT    NOT NULL DEFAULT 'PZ',
    gtin                     TEXT    UNIQUE,            -- GS1 GTIN / UDI-DI
    manufacturer_name        TEXT,
    stock_enabled_flag       TEXT    NOT NULL CHECK (stock_enabled_flag IN ('Y','N')),
    consigned_vendor_id      INTEGER REFERENCES po_vendors(vendor_id),
    last_contract_price      NUMERIC,
    fhir_device_definition_id TEXT,
    last_fhir_sync_date      TEXT
);

-- Giacenze per lotto (cfr. MTL_ONHAND_QUANTITIES_DETAIL)
CREATE TABLE IF NOT EXISTS mtl_onhand_quantities_detail (
    organization_id      INTEGER NOT NULL,
    inventory_item_id    INTEGER NOT NULL REFERENCES mtl_system_items_b(inventory_item_id),
    subinventory_code    TEXT    NOT NULL,
    lot_number           TEXT    NOT NULL,
    transaction_quantity NUMERIC NOT NULL,
    PRIMARY KEY (organization_id, inventory_item_id, subinventory_code, lot_number)
);

-- Movimenti di magazzino (cfr. MTL_MATERIAL_TRANSACTIONS)
CREATE TABLE IF NOT EXISTS mtl_material_transactions (
    transaction_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id         INTEGER NOT NULL,
    inventory_item_id       INTEGER NOT NULL,
    subinventory_code       TEXT    NOT NULL,
    lot_number              TEXT,
    transaction_quantity    NUMERIC NOT NULL,          -- negativo = scarico
    transaction_type_name   TEXT    NOT NULL,
    transaction_reference   TEXT,                      -- riferimento FHIR
    transaction_date        TEXT    NOT NULL
);

-- Interfaccia richieste d'acquisto (cfr. PO_REQUISITIONS_INTERFACE_ALL)
CREATE TABLE IF NOT EXISTS po_requisitions_interface_all (
    interface_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    interface_source_code    TEXT    NOT NULL,
    requisition_type         TEXT    NOT NULL,         -- 'BILL_ONLY'
    item_id                  INTEGER NOT NULL,
    quantity                 NUMERIC NOT NULL,
    unit_price               NUMERIC,
    suggested_vendor_id      INTEGER,
    destination_organization_id INTEGER NOT NULL,
    destination_subinventory TEXT,
    lot_number               TEXT,
    note_to_buyer            TEXT,
    source_reference         TEXT    NOT NULL UNIQUE,  -- ChargeItem FHIR: idempotenza
    process_flag             TEXT    NOT NULL DEFAULT 'PENDING',
    creation_date            TEXT    NOT NULL
);

-- Staging custom dei consumi letti dal sistema clinico (convenzione XX_ per i custom)
CREATE TABLE IF NOT EXISTS xx_fhir_consumption_stg (
    charge_item_id     TEXT PRIMARY KEY,               -- id FHIR del ChargeItem
    gtin               TEXT,
    lot_number         TEXT,
    quantity           NUMERIC,
    location_id        TEXT,                           -- id FHIR della Location
    encounter_ref      TEXT,
    procedure_ref      TEXT,
    occurrence_date    TEXT,
    status             TEXT NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW','PROCESSED','ERROR')),
    error_message      TEXT,
    load_date          TEXT NOT NULL,
    processed_date     TEXT
);

-- Controllo esecuzioni (watermark per lettura incrementale)
CREATE TABLE IF NOT EXISTS xx_fhir_sync_control (
    flow_name          TEXT PRIMARY KEY,
    last_run_date      TEXT
);
