-- =============================================================================
-- Module 07, exercises 4 and 11: Casa Cucina's purchase spreadsheet, imported
-- as-is into one table. Run on ./project/bin/reset.sh 08.
-- =============================================================================
SET search_path = bazaar, public;

DROP TABLE IF EXISTS purchase_sheet;
CREATE TABLE purchase_sheet (
    po_number         text,
    po_date           date,
    supplier_name     text,
    supplier_email    text,
    supplier_country  text,
    warehouse_code    text,
    warehouse_city    text,
    sku               text,
    product_title     text,
    quantity          int,
    unit_cost         numeric(12,2)
);

INSERT INTO purchase_sheet VALUES
    ('PO-1001', '2025-01-15', 'Ferro Italia S.r.l.',  'orders@ferroitalia.it',  'IT', 'BO1', 'Bologna', 'CC-CW-001', 'Cast Iron Skillet 26cm',      20, 18.50),
    ('PO-1001', '2025-01-15', 'Ferro Italia S.r.l.',  'orders@ferroitalia.it',  'IT', 'BO1', 'Bologna', 'CC-CW-004', 'Dutch Oven 5.5L Enamel',      10, 52.00),
    ('PO-1002', '2025-01-20', 'Lame di Maniago',      'vendite@lamemaniago.it', 'IT', 'BO1', 'Bologna', 'CC-CU-001', 'Chef''s Knife 20cm',          15, 31.00),
    ('PO-1002', '2025-01-20', 'Lame di Maniago',      'vendite@lamemaniago.it', 'IT', 'BO1', 'Bologna', 'CC-CU-002', 'Santoku Knife 18cm',          15, 27.50),
    ('PO-1002', '2025-01-20', 'Lame di Maniago',      'vendite@lamemaniago.it', 'IT', 'BO1', 'Bologna', 'CC-CU-003', 'Paring Knife Set (3 pcs)',    30,  9.80),
    ('PO-1003', '2025-02-03', 'Bambù & Co',           'info@bambuco.de',        'DE', 'MI2', 'Milano',  'CC-CU-005', 'Bamboo Cutting Board',        40,  7.20),
    ('PO-1003', '2025-02-03', 'Bambù & Co',           'info@bambuco.de',        'DE', 'MI2', 'Milano',  'CC-CU-006', 'Knife Sharpening Stone 1000/6000', 25, 11.00),
    ('PO-1004', '2025-03-10', 'Ferro Italia S.r.l.',  'orders@ferroitalia.it',  'IT', 'MI2', 'Milano',  'CC-CW-001', 'Cast Iron Skillet 26cm',      30, 19.00),
    ('PO-1004', '2025-03-10', 'Ferro Italia S.r.l.',  'orders@ferroitalia.it',  'IT', 'MI2', 'Milano',  'CC-CW-002', 'Stainless Steel Saucepan 2L', 20, 14.75),
    ('PO-1004', '2025-03-10', 'Ferro Italia S.r.l.',  'orders@ferroitalia.it',  'IT', 'MI2', 'Milano',  'CC-CW-006', 'Pasta Pot with Strainer 6L',  12, 21.40),
    ('PO-1005', '2025-04-02', 'Ferro Italia',         'orders@ferroitalia.it',  'IT', 'BO1', 'Bologna', 'CC-CW-004', 'Dutch Oven 5.5L Enamel',       8, 54.00),
    ('PO-1005', '2025-04-02', 'Ferro Italia',         'orders@ferroitalia.it',  'IT', 'BO1', 'Bologna', 'CC-KI-001', 'Espresso Moka Pot 6-cup',     24, 15.90),
    ('PO-1006', '2025-05-12', 'Lame di Maniago',      'vendite@lamemaniago.it', 'IT', 'MI2', 'Milano',  'CC-CU-001', 'Chef''s Knife 20cm',          10, 31.00),
    ('PO-1006', '2025-05-12', 'Lame di Maniago',      'vendite@lamemaniago.it', 'IT', 'MI2', 'Milano',  'CC-CU-004', 'Bread Knife 23cm',            12, 12.30);
