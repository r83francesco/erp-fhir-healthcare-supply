-- =============================================================================
-- XX_FHIR_CONS_PKG
-- Elabora i consumi clinici letti dal server FHIR (tabella XX_FHIR_CONSUMPTION_STG):
--   * articolo a scorta (STOCK_ENABLED_FLAG = 'Y')  -> scarico di magazzino per lotto
--   * articolo in conto deposito ('N')              -> richiesta d'acquisto BILL_ONLY
-- Ogni riga e' elaborata in modo indipendente (SAVEPOINT): una riga in errore non
-- blocca le altre e viene ritentata all'esecuzione successiva.
-- =============================================================================
CREATE OR REPLACE PACKAGE xx_fhir_cons_pkg AS
  e_business EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_business, -20001);

  PROCEDURE process_staging (
    p_organization_id IN  NUMBER,
    x_stock_count     OUT NUMBER,
    x_bill_only_count OUT NUMBER,
    x_error_count     OUT NUMBER
  );
END xx_fhir_cons_pkg;
/
CREATE OR REPLACE PACKAGE BODY xx_fhir_cons_pkg AS

  PROCEDURE raise_business (p_msg IN VARCHAR2) IS
  BEGIN
    RAISE_APPLICATION_ERROR(-20001, SUBSTR(p_msg, 1, 230));
  END raise_business;

  -- Elabora una riga di staging; restituisce 'STOCK' o 'BILL_ONLY'
  FUNCTION process_row (
    p_org IN NUMBER,
    p_stg IN xx_fhir_consumption_stg%ROWTYPE
  ) RETURN VARCHAR2 IS
    l_item      mtl_system_items_b%ROWTYPE;
    l_subinv    mtl_secondary_inventories.subinventory_code%TYPE;
    l_available NUMBER;
    l_ref       VARCHAR2(240) := 'ChargeItem/' || p_stg.charge_item_id;
    l_dup       NUMBER;
  BEGIN
    -- 1. Articolo tramite GTIN (UDI-DI)
    BEGIN
      SELECT * INTO l_item
        FROM mtl_system_items_b
       WHERE gtin = p_stg.gtin
         AND organization_id = p_org;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        raise_business('Articolo non trovato per GTIN ' || p_stg.gtin);
    END;

    -- 2. Sottoinventario tramite Location FHIR
    BEGIN
      SELECT subinventory_code INTO l_subinv
        FROM mtl_secondary_inventories
       WHERE fhir_location_id = p_stg.location_id
         AND organization_id = p_org;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        raise_business('Nessun sottoinventario associato alla Location ' || p_stg.location_id);
    END;

    IF p_stg.quantity IS NULL OR p_stg.quantity <= 0 THEN
      raise_business('Quantita'' mancante o non positiva');
    END IF;

    IF l_item.stock_enabled_flag = 'Y' THEN
      -- 3a. Articolo a scorta: scarico per lotto
      IF p_stg.lot_number IS NULL THEN
        raise_business('Lotto obbligatorio per articolo a scorta');
      END IF;

      BEGIN
        SELECT transaction_quantity INTO l_available
          FROM mtl_onhand_quantities_detail
         WHERE organization_id   = p_org
           AND inventory_item_id = l_item.inventory_item_id
           AND subinventory_code = l_subinv
           AND lot_number        = p_stg.lot_number
           FOR UPDATE;
      EXCEPTION
        WHEN NO_DATA_FOUND THEN l_available := 0;
      END;

      IF l_available < p_stg.quantity THEN
        raise_business('Giacenza insufficiente: disponibili ' || l_available || ', richiesti '
                       || p_stg.quantity || ' (' || l_item.segment1 || ' lotto '
                       || p_stg.lot_number || ' in ' || l_subinv || ')');
      END IF;

      UPDATE mtl_onhand_quantities_detail
         SET transaction_quantity = transaction_quantity - p_stg.quantity
       WHERE organization_id   = p_org
         AND inventory_item_id = l_item.inventory_item_id
         AND subinventory_code = l_subinv
         AND lot_number        = p_stg.lot_number;

      INSERT INTO mtl_material_transactions
             (organization_id, inventory_item_id, subinventory_code, lot_number,
              transaction_quantity, transaction_type_name, transaction_reference, transaction_date)
      VALUES (p_org, l_item.inventory_item_id, l_subinv, p_stg.lot_number,
              -p_stg.quantity, 'Scarico a paziente', l_ref, SYSDATE);
      RETURN 'STOCK';
    END IF;

    -- 3b. Conto deposito: richiesta BILL_ONLY (idempotente sul riferimento FHIR)
    SELECT COUNT(*) INTO l_dup
      FROM po_requisitions_interface_all
     WHERE source_reference = l_ref;

    IF l_dup = 0 THEN
      INSERT INTO po_requisitions_interface_all
             (interface_source_code, requisition_type, item_id, quantity, unit_price,
              suggested_vendor_id, destination_organization_id, destination_subinventory,
              lot_number, note_to_buyer, source_reference, process_flag, creation_date)
      VALUES ('FHIR', 'BILL_ONLY', l_item.inventory_item_id, p_stg.quantity, l_item.last_contract_price,
              l_item.consigned_vendor_id, p_org, l_subinv,
              p_stg.lot_number,
              SUBSTR('Bill-only: impianto in conto deposito. ' || p_stg.procedure_ref
                     || ' / ' || p_stg.encounter_ref, 1, 480),
              l_ref, 'PENDING', SYSDATE);
    END IF;
    RETURN 'BILL_ONLY';
  END process_row;

  PROCEDURE process_staging (
    p_organization_id IN  NUMBER,
    x_stock_count     OUT NUMBER,
    x_bill_only_count OUT NUMBER,
    x_error_count     OUT NUMBER
  ) IS
    l_stg  xx_fhir_consumption_stg%ROWTYPE;
    l_kind VARCHAR2(10);
    l_msg  VARCHAR2(240);
  BEGIN
    x_stock_count := 0;
    x_bill_only_count := 0;
    x_error_count := 0;

    FOR c IN (SELECT charge_item_id
                FROM xx_fhir_consumption_stg
               WHERE status IN ('NEW', 'ERROR')
               ORDER BY load_date, charge_item_id)
    LOOP
      SELECT * INTO l_stg
        FROM xx_fhir_consumption_stg
       WHERE charge_item_id = c.charge_item_id;

      SAVEPOINT stg_row;
      BEGIN
        l_kind := process_row(p_organization_id, l_stg);

        UPDATE xx_fhir_consumption_stg
           SET status = 'PROCESSED', error_message = NULL, processed_date = SYSDATE
         WHERE charge_item_id = c.charge_item_id;

        IF l_kind = 'STOCK' THEN
          x_stock_count := x_stock_count + 1;
        ELSE
          x_bill_only_count := x_bill_only_count + 1;
        END IF;
      EXCEPTION
        WHEN e_business THEN
          l_msg := SUBSTR(REGEXP_REPLACE(SQLERRM, '^ORA-20001: '), 1, 240);
          ROLLBACK TO SAVEPOINT stg_row;
          UPDATE xx_fhir_consumption_stg
             SET status = 'ERROR', error_message = l_msg
           WHERE charge_item_id = c.charge_item_id;
          x_error_count := x_error_count + 1;
      END;
    END LOOP;
  END process_staging;

END xx_fhir_cons_pkg;
/
