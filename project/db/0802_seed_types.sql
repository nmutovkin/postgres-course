-- =============================================================================
-- 0802_seed_types.sql  --  Module 08: Beyond Scalars
-- -----------------------------------------------------------------------------
-- Fills the columns/tables added by 0801_schema_types.sql for the small dataset:
--   * products.attributes: category-dependent JSON documents. Laptops have
--     brand/ram_gb/cpu/screen_in and a nested "storage" object; books have
--     author/pages/isbn/language (product 28 lacks pages/isbn); clothing has a
--     "size" ARRAY and a color; product 33 has an array of objects; product 62
--     has an empty object {}.
--   * products.tags: a few lowercase tags per product ('bestseller', 'gaming', ...).
--   * promotions: time-boxed discounts as half-open tstzrange [from, to).
--     Products 47 and 48 have an open-ended promotion (upper bound NULL = infinity)
--     that is still running; product 10 has two non-overlapping promotions.
--
-- GENERATED FILE: do not edit by hand. Edit project/db/tools/gen_small_seed.py
-- and re-run it (output is deterministic).
-- =============================================================================

SET search_path = bazaar, public;

BEGIN;

-- One UPDATE per product keeps the file easy to read and diff.
UPDATE products SET attributes = '{"brand": "AeroTech", "ram_gb": 16, "cpu": "M-Core 8", "screen_in": 13.3, "storage": {"type": "ssd", "gb": 512}}', tags = '{ultralight,laptop,bestseller}' WHERE id = 1;
UPDATE products SET attributes = '{"brand": "AeroTech", "ram_gb": 32, "cpu": "M-Core 12", "screen_in": 15.6, "storage": {"type": "ssd", "gb": 1024}}', tags = '{laptop,creator}' WHERE id = 2;
UPDATE products SET attributes = '{"brand": "Titan", "ram_gb": 32, "cpu": "Ryzon 9", "screen_in": 17.3, "gpu": "RTX-class", "storage": {"type": "ssd", "gb": 2048}}', tags = '{laptop,gaming}' WHERE id = 3;
UPDATE products SET attributes = '{"brand": "StudyMate", "ram_gb": 4, "cpu": "Celera N", "screen_in": 11.6, "storage": {"type": "emmc", "gb": 64}}', tags = '{laptop,student,budget}' WHERE id = 4;
UPDATE products SET attributes = '{"brand": "AeroTech", "ram_gb": 8, "cpu": "M-Core 6", "screen_in": 13.3}', tags = '{laptop,discontinued}' WHERE id = 5;
UPDATE products SET attributes = '{"brand": "ClearView", "size_in": 24, "resolution": "1920x1080", "panel": "IPS", "refresh_hz": 75}', tags = '{monitor}' WHERE id = 6;
UPDATE products SET attributes = '{"brand": "ClearView", "size_in": 27, "resolution": "3840x2160", "panel": "IPS", "refresh_hz": 60, "ports": ["HDMI", "DisplayPort", "USB-C"]}', tags = '{monitor,4k,usb-c}' WHERE id = 7;
UPDATE products SET attributes = '{"brand": "UltraWide", "size_in": 34, "resolution": "3440x1440", "panel": "VA", "refresh_hz": 144, "curved": true}', tags = '{monitor,gaming}' WHERE id = 8;
UPDATE products SET attributes = '{"brand": "PortaScreen", "size_in": 15.6, "resolution": "1920x1080", "panel": "IPS", "ports": ["USB-C"]}', tags = '{monitor,portable,usb-c}' WHERE id = 9;
UPDATE products SET attributes = '{"brand": "QuietWave", "wireless": true, "anc": true, "battery_h": 30, "colors": ["black", "silver"]}', tags = '{headphones,wireless,anc,bestseller}' WHERE id = 10;
UPDATE products SET attributes = '{"brand": "QuietWave", "wireless": true, "anc": true, "battery_h": 8}', tags = '{earbuds,wireless,anc}' WHERE id = 11;
UPDATE products SET attributes = '{"brand": "StudioPro", "wireless": false, "impedance_ohm": 32}', tags = '{headphones,studio}' WHERE id = 12;
UPDATE products SET attributes = '{"brand": "KidSafe", "wireless": false, "max_db": 85, "colors": ["pink", "blue", "green"]}', tags = '{headphones,kids}' WHERE id = 13;
UPDATE products SET attributes = '{"brand": "BassBoom", "wireless": true}', tags = '{headphones}' WHERE id = 14;
UPDATE products SET attributes = '{"brand": "Titan", "ram_gb": 16, "cpu": "Ryzon 7", "screen_in": 15.6, "gpu": "RTX-class"}', tags = '{laptop,gaming}' WHERE id = 15;
UPDATE products SET attributes = '{"material": "neoprene", "fits_in": 13, "colors": ["grey", "black"]}', tags = '{accessory,laptop}' WHERE id = 16;
UPDATE products SET attributes = '{"ports": ["HDMI", "HDMI", "RJ45", "USB-A", "USB-A", "USB-C"], "power_w": 100}', tags = '{accessory,usb-c}' WHERE id = 17;
UPDATE products SET attributes = '{"brand": "ErgoGrip", "wireless": true, "dpi": 1600}', tags = '{accessory,ergonomic}' WHERE id = 18;
UPDATE products SET attributes = '{"author": "Mara Quinn", "language": "en", "pages": 412, "isbn": "9780000000019"}', tags = '{book,sci-fi}' WHERE id = 19;
UPDATE products SET attributes = '{"author": "Ade Okonkwo", "language": "en", "pages": 356, "isbn": "9780000000026"}', tags = '{book,sci-fi}' WHERE id = 20;
UPDATE products SET attributes = '{"author": "Lena Vogt", "language": "en", "pages": 498, "isbn": "9780000000033"}', tags = '{book,sci-fi}' WHERE id = 21;
UPDATE products SET attributes = '{"author": "Tom Reyes", "language": "en", "pages": 289, "isbn": "9780000000040"}', tags = '{book,sci-fi}' WHERE id = 22;
UPDATE products SET attributes = '{"author": "E. H. Blackwood", "language": "en", "pages": 520, "isbn": "9780000000057"}', tags = '{book,sci-fi}' WHERE id = 23;
UPDATE products SET attributes = '{"author": "Agatha Pembroke", "language": "en", "pages": 301, "isbn": "9780000000064"}', tags = '{book,mystery}' WHERE id = 24;
UPDATE products SET attributes = '{"author": "Rosalind Hart", "language": "en", "pages": 334, "isbn": "9780000000071"}', tags = '{book,mystery,bestseller}' WHERE id = 25;
UPDATE products SET attributes = '{"author": "Agatha Pembroke", "language": "en", "pages": 288, "isbn": "9780000000088"}', tags = '{book,mystery}' WHERE id = 26;
UPDATE products SET attributes = '{"author": "Jonas Weber", "language": "de", "pages": 365, "isbn": "9780000000095"}', tags = '{book,mystery}' WHERE id = 27;
UPDATE products SET attributes = '{"author": "Agatha Pembroke", "language": "en"}', tags = '{book,mystery}' WHERE id = 28;
UPDATE products SET attributes = '{"author": "Nadia Farouk", "language": "en", "pages": 480, "isbn": "9780000000101"}', tags = '{book,cooking}' WHERE id = 29;
UPDATE products SET attributes = '{"author": "Marco Bellini", "language": "it", "pages": 224, "isbn": "9780000000118"}', tags = '{book,cooking}' WHERE id = 30;
UPDATE products SET attributes = '{"author": "Sandor Katz-Smith", "language": "en", "pages": 512, "isbn": "9780000000125"}', tags = '{book,cooking}' WHERE id = 31;
UPDATE products SET attributes = '{"author": "Rose Whitfield", "language": "en", "pages": 196, "isbn": "9780000000132"}', tags = '{book,cooking}' WHERE id = 32;
UPDATE products SET attributes = '{"format": "box set", "volumes": 5, "language": "en", "contents": [{"title": "The Long Orbit", "author": "Mara Quinn"}, {"title": "Starlight Protocol", "author": "Lena Vogt"}]}', tags = '{book,sci-fi,gift}' WHERE id = 33;
UPDATE products SET attributes = '{"material": "cast iron", "induction": true, "dishwasher_safe": false, "diameter_cm": 26}', tags = '{cookware,cast-iron}' WHERE id = 34;
UPDATE products SET attributes = '{"material": "stainless steel", "induction": true, "dishwasher_safe": true}', tags = '{cookware}' WHERE id = 35;
UPDATE products SET attributes = '{"material": "aluminium", "induction": true, "dishwasher_safe": false}', tags = '{cookware}' WHERE id = 36;
UPDATE products SET attributes = '{"material": "enamelled cast iron", "induction": true, "dishwasher_safe": false, "capacity_l": 5.5}', tags = '{cookware,cast-iron}' WHERE id = 37;
UPDATE products SET attributes = '{"material": "copper", "induction": false, "dishwasher_safe": false}', tags = '{cookware}' WHERE id = 38;
UPDATE products SET attributes = '{"material": "stainless steel", "induction": true, "dishwasher_safe": true, "capacity_l": 6}', tags = '{cookware}' WHERE id = 39;
UPDATE products SET attributes = '{"material": "stainless steel", "blade_cm": 20}', tags = '{cutlery,knife}' WHERE id = 40;
UPDATE products SET attributes = '{"material": "stainless steel", "blade_cm": 18}', tags = '{cutlery,knife}' WHERE id = 41;
UPDATE products SET attributes = '{"material": "stainless steel", "blade_cm": 9}', tags = '{cutlery,knife}' WHERE id = 42;
UPDATE products SET attributes = '{"material": "stainless steel", "blade_cm": 23}', tags = '{cutlery,knife}' WHERE id = 43;
UPDATE products SET attributes = '{"material": "bamboo"}', tags = '{cutlery}' WHERE id = 44;
UPDATE products SET attributes = '{"material": "stone"}', tags = '{cutlery}' WHERE id = 45;
UPDATE products SET attributes = '{"material": "aluminium", "cups": 6, "induction": false}', tags = '{coffee,kitchen}' WHERE id = 46;
UPDATE products SET attributes = '{"size": ["XS", "S", "M", "L", "XL"], "color": "white", "material": "organic cotton", "fit": "regular"}', tags = '{clothing,tee,organic}' WHERE id = 47;
UPDATE products SET attributes = '{"size": ["XS", "S", "M", "L", "XL", "XXL"], "color": "black", "material": "organic cotton", "fit": "regular"}', tags = '{clothing,tee,organic}' WHERE id = 48;
UPDATE products SET attributes = '{"size": ["XS", "S", "M", "L", "XL"], "color": "navy", "material": "cotton", "fit": "regular"}', tags = '{clothing,tee}' WHERE id = 49;
UPDATE products SET attributes = '{"size": ["XS", "S", "M", "L", "XL"], "color": "sand", "material": "linen", "fit": "relaxed"}', tags = '{clothing,tee}' WHERE id = 50;
UPDATE products SET attributes = '{"size": ["XS", "S", "M", "L", "XL"], "color": "black", "material": "cotton", "fit": "regular"}', tags = '{clothing,tee}' WHERE id = 51;
UPDATE products SET attributes = '{"size": ["XS", "S", "M", "L", "XL"], "color": "grey", "material": "cotton", "fit": "regular"}', tags = '{clothing,hoodie}' WHERE id = 52;
UPDATE products SET attributes = '{"size": ["XS", "S", "M", "L", "XL", "XXL"], "color": "olive", "material": "cotton", "fit": "relaxed"}', tags = '{clothing,hoodie}' WHERE id = 53;
UPDATE products SET attributes = '{"size": ["4-6", "7-9", "10-12"], "color": "multicolor", "material": "cotton", "fit": "regular"}', tags = '{clothing,hoodie,kids}' WHERE id = 54;
UPDATE products SET attributes = '{"size": ["XS", "S", "M", "L", "XL"], "color": "charcoal", "material": "cotton", "fit": "regular"}', tags = '{clothing,hoodie}' WHERE id = 55;
UPDATE products SET attributes = '{"size": ["XS", "S", "M", "L", "XL"], "color": "forest green", "material": "merino wool", "fit": "regular"}', tags = '{clothing,hoodie}' WHERE id = 56;
UPDATE products SET attributes = '{"material": "terracotta", "diameter_cm": 20, "drainage_hole": true}', tags = '{garden,planter}' WHERE id = 57;
UPDATE products SET attributes = '{"material": "plastic", "diameter_cm": 25, "reservoir_ml": 800}', tags = '{garden,planter}' WHERE id = 58;
UPDATE products SET attributes = '{"material": "cotton", "handmade": true}', tags = '{garden,planter,handmade}' WHERE id = 59;
UPDATE products SET attributes = '{"material": "cedar", "dimensions_cm": {"l": 120, "w": 60, "h": 30}}', tags = '{garden,outdoor}' WHERE id = 60;
UPDATE products SET attributes = '{"material": "ceramic", "pieces": 3}', tags = '{garden,planter,discontinued}' WHERE id = 61;
UPDATE products SET attributes = '{}', tags = '{freebie}' WHERE id = 62;

-- Promotions: '[lower,upper)' ranges; an empty upper bound means 'no end'.
INSERT INTO promotions (id, product_id, discount_pct, active_during, created_at)
OVERRIDING SYSTEM VALUE
VALUES
    (1, 1, 10.00, '[2024-11-25 00:00:00+00,2024-12-02 00:00:00+00)', '2024-11-22 00:00:00+00'),
    (2, 10, 15.00, '[2024-11-25 00:00:00+00,2024-12-02 00:00:00+00)', '2024-11-22 00:00:00+00'),
    (3, 10, 20.00, '[2025-06-01 00:00:00+00,2025-06-15 00:00:00+00)', '2025-05-29 00:00:00+00'),
    (4, 7, 5.00, '[2025-03-01 00:00:00+00,2025-04-01 00:00:00+00)', '2025-02-26 00:00:00+00'),
    (5, 34, 25.00, '[2024-07-01 00:00:00+00,2024-07-08 00:00:00+00)', '2024-06-28 00:00:00+00'),
    (6, 47, 30.00, '[2025-06-20 00:00:00+00,)', '2025-06-17 00:00:00+00'),
    (7, 48, 30.00, '[2025-06-20 00:00:00+00,)', '2025-06-17 00:00:00+00'),
    (8, 24, 50.00, '[2025-01-01 00:00:00+00,2025-01-08 00:00:00+00)', '2024-12-29 00:00:00+00'),
    (9, 37, 12.50, '[2025-05-01 00:00:00+00,2025-07-01 00:00:00+00)', '2025-04-28 00:00:00+00');

ALTER TABLE promotions ALTER COLUMN id RESTART WITH 10;

COMMIT;
