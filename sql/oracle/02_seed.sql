-- Dati di esempio INTERAMENTE FITTIZI (stessi dati di sql/sqlite/seed.sql)
BEGIN
  INSERT INTO po_vendors VALUES (5001, 'OrthoDemo S.r.l. (fittizio)', 'IT00000000001');
  INSERT INTO po_vendors VALUES (5002, 'MedSupply Demo S.p.A. (fittizio)', 'IT00000000002');

  INSERT INTO mtl_secondary_inventories (organization_id, subinventory_code, description)
       VALUES (101, 'FARM', 'Farmacia ospedaliera - magazzino centrale');
  INSERT INTO mtl_secondary_inventories (organization_id, subinventory_code, description)
       VALUES (101, 'BO-SALA1', 'Blocco operatorio - Sala 1 (deposito di reparto)');
  INSERT INTO mtl_secondary_inventories (organization_id, subinventory_code, description)
       VALUES (101, 'BO-DEP', 'Blocco operatorio - Conto deposito protesi');

  INSERT INTO mtl_system_items_b (inventory_item_id, organization_id, segment1, description, primary_uom_code,
         gtin, manufacturer_name, stock_enabled_flag, consigned_vendor_id, last_contract_price)
  VALUES (1001, 101, 'ART-0001', 'Guanti chirurgici sterili in lattice, taglia 7,5', 'PA',
          '08009999000017', 'MedSupply Demo S.p.A.', 'Y', NULL, 0.45);
  INSERT INTO mtl_system_items_b (inventory_item_id, organization_id, segment1, description, primary_uom_code,
         gtin, manufacturer_name, stock_enabled_flag, consigned_vendor_id, last_contract_price)
  VALUES (1002, 101, 'ART-0002', 'Sutura riassorbibile 2/0 con ago 26 mm', 'PZ',
          '08009999000024', 'MedSupply Demo S.p.A.', 'Y', NULL, 3.20);
  INSERT INTO mtl_system_items_b (inventory_item_id, organization_id, segment1, description, primary_uom_code,
         gtin, manufacturer_name, stock_enabled_flag, consigned_vendor_id, last_contract_price)
  VALUES (1003, 101, 'ART-0003', 'Telo chirurgico monouso 150x200 cm', 'PZ',
          '08009999000031', 'MedSupply Demo S.p.A.', 'Y', NULL, 2.10);
  INSERT INTO mtl_system_items_b (inventory_item_id, organization_id, segment1, description, primary_uom_code,
         gtin, manufacturer_name, stock_enabled_flag, consigned_vendor_id, last_contract_price)
  VALUES (2001, 101, 'PRT-1001', 'Protesi d''anca - stelo femorale non cementato, misura 3', 'PZ',
          '08009999001014', 'OrthoDemo S.r.l.', 'N', 5001, 1450.00);
  INSERT INTO mtl_system_items_b (inventory_item_id, organization_id, segment1, description, primary_uom_code,
         gtin, manufacturer_name, stock_enabled_flag, consigned_vendor_id, last_contract_price)
  VALUES (2002, 101, 'PRT-1002', 'Protesi d''anca - testa femorale in ceramica 32 mm', 'PZ',
          '08009999001021', 'OrthoDemo S.r.l.', 'N', 5001, 380.00);
  INSERT INTO mtl_system_items_b (inventory_item_id, organization_id, segment1, description, primary_uom_code,
         gtin, manufacturer_name, stock_enabled_flag, consigned_vendor_id, last_contract_price)
  VALUES (2003, 101, 'PRT-1003', 'Protesi d''anca - cotile press-fit 52 mm', 'PZ',
          '08009999001038', 'OrthoDemo S.r.l.', 'N', 5001, 620.00);

  INSERT INTO mtl_onhand_quantities_detail VALUES (101, 1001, 'BO-SALA1', 'LOT-G-2026A', 200);
  INSERT INTO mtl_onhand_quantities_detail VALUES (101, 1002, 'BO-SALA1', 'LOT-S-2026B', 60);
  INSERT INTO mtl_onhand_quantities_detail VALUES (101, 1003, 'BO-SALA1', 'LOT-T-2026C', 40);
  COMMIT;
END;
/
