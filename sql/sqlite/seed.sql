-- Dati di esempio INTERAMENTE FITTIZI (aziende, GTIN, prezzi, lotti).
-- Organizzazione di inventario 101 = "Ospedale Demo".

INSERT OR IGNORE INTO po_vendors (vendor_id, vendor_name, vat_number) VALUES
  (5001, 'OrthoDemo S.r.l. (fittizio)',   'IT00000000001'),
  (5002, 'MedSupply Demo S.p.A. (fittizio)', 'IT00000000002');

INSERT OR IGNORE INTO mtl_secondary_inventories (organization_id, subinventory_code, description) VALUES
  (101, 'FARM',     'Farmacia ospedaliera - magazzino centrale'),
  (101, 'BO-SALA1', 'Blocco operatorio - Sala 1 (deposito di reparto)'),
  (101, 'BO-DEP',   'Blocco operatorio - Conto deposito protesi');

-- Articoli a scorta (stock_enabled_flag = 'Y')
INSERT OR IGNORE INTO mtl_system_items_b
  (inventory_item_id, organization_id, segment1, description, primary_uom_code, gtin,
   manufacturer_name, stock_enabled_flag, consigned_vendor_id, last_contract_price) VALUES
  (1001, 101, 'ART-0001', 'Guanti chirurgici sterili in lattice, taglia 7,5', 'PA', '08009999000017', 'MedSupply Demo S.p.A.', 'Y', NULL, 0.45),
  (1002, 101, 'ART-0002', 'Sutura riassorbibile 2/0 con ago 26 mm',          'PZ', '08009999000024', 'MedSupply Demo S.p.A.', 'Y', NULL, 3.20),
  (1003, 101, 'ART-0003', 'Telo chirurgico monouso 150x200 cm',               'PZ', '08009999000031', 'MedSupply Demo S.p.A.', 'Y', NULL, 2.10);

-- Articoli in conto deposito (stock_enabled_flag = 'N' -> bill-only)
INSERT OR IGNORE INTO mtl_system_items_b
  (inventory_item_id, organization_id, segment1, description, primary_uom_code, gtin,
   manufacturer_name, stock_enabled_flag, consigned_vendor_id, last_contract_price) VALUES
  (2001, 101, 'PRT-1001', 'Protesi d''anca - stelo femorale non cementato, misura 3', 'PZ', '08009999001014', 'OrthoDemo S.r.l.', 'N', 5001, 1450.00),
  (2002, 101, 'PRT-1002', 'Protesi d''anca - testa femorale in ceramica 32 mm',      'PZ', '08009999001021', 'OrthoDemo S.r.l.', 'N', 5001,  380.00),
  (2003, 101, 'PRT-1003', 'Protesi d''anca - cotile press-fit 52 mm',                'PZ', '08009999001038', 'OrthoDemo S.r.l.', 'N', 5001,  620.00);

-- Giacenze iniziali degli articoli a scorta nella sala operatoria
INSERT OR IGNORE INTO mtl_onhand_quantities_detail
  (organization_id, inventory_item_id, subinventory_code, lot_number, transaction_quantity) VALUES
  (101, 1001, 'BO-SALA1', 'LOT-G-2026A', 200),
  (101, 1002, 'BO-SALA1', 'LOT-S-2026B',  60),
  (101, 1003, 'BO-SALA1', 'LOT-T-2026C',  40);
