-- =============================================================================
-- 1101_seed_large.sql  --  Module 11: Indexes
-- -----------------------------------------------------------------------------
-- Replaces the small hand-written dataset with a LARGE, deterministic, generated
-- one, so that indexes, plans and statistics behave like they do in production.
-- Generated entirely in SQL with generate_series().
--
--   users                 100,000   ids in sign-up order, growth over 2022-2025
--   sellers                 2,000   seller s is owned by user s*37; seller ids are
--                                   ranked by size (seller 1 is the biggest)
--   categories                 69   the small tree (ids 1-23 unchanged) + 46 more
--   products              200,000   Zipf-skewed over sellers: seller 1 ~9%,
--                                   seller 10 ~1%, seller 2000 ~13 products;
--                                   sellers specialise in one top-level category
--   inventory             200,000
--   orders                500,000   2023-01-01 .. 2025-12-31, growth trend,
--                                   weekly + Nov/Dec seasonality, Black Friday spikes;
--                                   ids in chronological order
--   order_items          1,266,928   orders.total = sum(quantity * unit_price) exactly
--   addresses              125,017   every user has a default address (id = user id)
--   payments               536,816
--   reviews                292,355   ~98% by buyers of a delivered order, 6,060 not
--   cart_items              19,843
--   product_price_history  230,263   ~15% of products changed price once;
--                                   unit_price always matches the history
--   promotions              15,047
--   audit_log, jobs         empty
--
-- "Today" in this dataset is 2025-12-31: open orders (pending/paid/shipped)
-- cluster in the last days of December 2025.
--
-- Runtime: ~70 s on the reference machine (4 vCPU, PostgreSQL 17 defaults in
-- Docker), including VACUUM ANALYZE. The database is ~1 GB afterwards.
-- Status mix: ~90.5% delivered, 5% cancelled, 3% refunded; the ~7,600 open
-- orders (pending / paid / shipped) are all from the last 12 days of 2025.
--
-- Determinism. Every "random" choice is a pure function of a row key:
--     rnd(key, salt) = hashint8extended(key, salt) mapped to [0, 1)
-- so the data is identical on every run and does not depend on the order in
-- which the executor happens to process rows. (setseed() + random() is only
-- reproducible as long as the plan, and so the order of random() calls, stays
-- the same; we still call setseed() for good measure.)
--
-- Triggers. The load runs with  SET session_replication_role = replica.
-- In that mode ordinary triggers do not fire, and that includes the internal
-- triggers that enforce FOREIGN KEYS. We want that here:
--   * audit_trigger() would write ~2M audit rows nobody needs;
--   * set_updated_at() would overwrite our carefully generated updated_at;
--   * the order_items total trigger would update every order once per INSERT
--     batch (500k extra row versions); we compute totals ourselves instead;
--   * FK checks would cost one index lookup per referencing row.
-- The price: nothing checks referential integrity or totals for us, so the
-- generator must be correct by construction - project/db/tools/check.sql
-- verifies it. (CHECK, NOT NULL, UNIQUE and EXCLUDE constraints are not
-- triggers and are still enforced.) session_replication_role requires
-- superuser; it is meant for replication tools, use it with care.
-- =============================================================================

SET search_path = bazaar, public;
SET session_replication_role = replica;
SET work_mem = '256MB';
SET maintenance_work_mem = '512MB';
SET synchronous_commit = off;          -- safe here: a crash just means re-running reset
SET client_min_messages = warning;
SELECT setseed(0.1101) \g /dev/null

BEGIN;

TRUNCATE users, sellers, categories, products, inventory, orders, order_items,
         addresses, payments, reviews, cart_items, product_price_history,
         promotions, audit_log, jobs
RESTART IDENTITY CASCADE;

-- -----------------------------------------------------------------------------
-- Helpers (pg_temp = this session only; gone when the session ends).
-- Simple IMMUTABLE SQL functions are inlined by the planner: no call overhead.
-- -----------------------------------------------------------------------------
CREATE FUNCTION pg_temp.rnd(k bigint, salt bigint) RETURNS float8
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT ((hashint8extended(k, salt) >> 11) & 9007199254740991)::float8 / 9007199254740992.0 $$;

-- Pick an element of an array using a uniform number r in [0, 1).
CREATE FUNCTION pg_temp.pick(a anyarray, r float8) RETURNS anyelement
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT a[1 + floor(r * cardinality(a))::int] $$;

-- Strip accents/apostrophes for e-mail local parts (unaccent arrives in module 13).
CREATE FUNCTION pg_temp.ascii_name(s text) RETURNS text
LANGUAGE sql IMMUTABLE
AS $$ SELECT replace(translate(lower(s), 'áàâäãåçéèêëíìîïñóòôöõøúùûüýÿ', 'aaaaaaceeeeiiiinoooooouuuuyy'), '''', '') $$;

-- -----------------------------------------------------------------------------
-- 1. Categories: the small tree keeps ids 1-23; 46 more nodes below.
-- -----------------------------------------------------------------------------
INSERT INTO categories (id, parent_id, name, slug, created_at)
OVERRIDING SYSTEM VALUE
VALUES
    (1, NULL, 'Electronics', 'electronics', '2022-01-01 00:00+00'),
    (2, 1, 'Computers', 'computers', '2022-01-01 00:00+00'),
    (3, 2, 'Laptops', 'laptops', '2022-01-01 00:00+00'),
    (4, 2, 'Monitors', 'monitors', '2022-01-01 00:00+00'),
    (5, 1, 'Audio', 'audio', '2022-01-01 00:00+00'),
    (6, 5, 'Headphones', 'headphones', '2022-01-01 00:00+00'),
    (7, 5, 'Speakers', 'speakers', '2022-01-01 00:00+00'),
    (8, NULL, 'Home & Garden', 'home-garden', '2022-01-01 00:00+00'),
    (9, 8, 'Kitchen', 'kitchen', '2022-01-01 00:00+00'),
    (10, 9, 'Cookware', 'cookware', '2022-01-01 00:00+00'),
    (11, 9, 'Cutlery', 'cutlery', '2022-01-01 00:00+00'),
    (12, 8, 'Garden', 'garden', '2022-01-01 00:00+00'),
    (13, 12, 'Planters', 'planters', '2022-01-01 00:00+00'),
    (14, NULL, 'Books', 'books', '2022-01-01 00:00+00'),
    (15, 14, 'Fiction', 'fiction', '2022-01-01 00:00+00'),
    (16, 15, 'Science Fiction', 'science-fiction', '2022-01-01 00:00+00'),
    (17, 15, 'Mystery', 'mystery', '2022-01-01 00:00+00'),
    (18, 14, 'Non-Fiction', 'non-fiction', '2022-01-01 00:00+00'),
    (19, 18, 'Cooking', 'cooking', '2022-01-01 00:00+00'),
    (20, 18, 'History', 'history', '2022-01-01 00:00+00'),
    (21, NULL, 'Clothing', 'clothing', '2022-01-01 00:00+00'),
    (22, 21, 'T-Shirts', 't-shirts', '2022-01-01 00:00+00'),
    (23, 21, 'Hoodies', 'hoodies', '2022-01-01 00:00+00'),
    (24, 2, 'Tablets', 'tablets', '2022-01-01 00:00+00'),
    (25, 2, 'Keyboards & Mice', 'keyboards-mice', '2022-01-01 00:00+00'),
    (26, 5, 'Microphones', 'microphones', '2022-01-01 00:00+00'),
    (27, 1, 'Phones & Accessories', 'phones-accessories', '2022-01-01 00:00+00'),
    (28, 27, 'Smartphones', 'smartphones', '2022-01-01 00:00+00'),
    (29, 27, 'Phone Cases', 'phone-cases', '2022-01-01 00:00+00'),
    (30, 27, 'Chargers & Cables', 'chargers-cables', '2022-01-01 00:00+00'),
    (31, 1, 'Cameras', 'cameras', '2022-01-01 00:00+00'),
    (32, 31, 'Digital Cameras', 'digital-cameras', '2022-01-01 00:00+00'),
    (33, 31, 'Camera Lenses', 'camera-lenses', '2022-01-01 00:00+00'),
    (34, 9, 'Bakeware', 'bakeware', '2022-01-01 00:00+00'),
    (35, 9, 'Small Appliances', 'small-appliances', '2022-01-01 00:00+00'),
    (36, 8, 'Furniture', 'furniture', '2022-01-01 00:00+00'),
    (37, 36, 'Chairs', 'chairs', '2022-01-01 00:00+00'),
    (38, 36, 'Desks', 'desks', '2022-01-01 00:00+00'),
    (39, 36, 'Shelving', 'shelving', '2022-01-01 00:00+00'),
    (40, 12, 'Garden Tools', 'garden-tools', '2022-01-01 00:00+00'),
    (41, 12, 'Outdoor Lighting', 'outdoor-lighting', '2022-01-01 00:00+00'),
    (42, 15, 'Fantasy', 'fantasy', '2022-01-01 00:00+00'),
    (43, 15, 'Romance', 'romance', '2022-01-01 00:00+00'),
    (44, 15, 'Literary Fiction', 'literary-fiction', '2022-01-01 00:00+00'),
    (45, 18, 'Biography', 'biography', '2022-01-01 00:00+00'),
    (46, 18, 'Science & Nature', 'science-nature', '2022-01-01 00:00+00'),
    (47, 18, 'Business', 'business', '2022-01-01 00:00+00'),
    (48, 14, 'Children''s Books', 'childrens-books', '2022-01-01 00:00+00'),
    (49, 21, 'Jackets', 'jackets', '2022-01-01 00:00+00'),
    (50, 21, 'Jeans', 'jeans', '2022-01-01 00:00+00'),
    (51, 21, 'Dresses', 'dresses', '2022-01-01 00:00+00'),
    (52, 21, 'Shoes', 'shoes', '2022-01-01 00:00+00'),
    (53, 52, 'Sneakers', 'sneakers', '2022-01-01 00:00+00'),
    (54, 52, 'Boots', 'boots', '2022-01-01 00:00+00'),
    (55, NULL, 'Sports & Outdoors', 'sports-outdoors', '2022-01-01 00:00+00'),
    (56, 55, 'Camping', 'camping', '2022-01-01 00:00+00'),
    (57, 56, 'Tents', 'tents', '2022-01-01 00:00+00'),
    (58, 56, 'Sleeping Bags', 'sleeping-bags', '2022-01-01 00:00+00'),
    (59, 55, 'Fitness', 'fitness', '2022-01-01 00:00+00'),
    (60, 59, 'Yoga', 'yoga', '2022-01-01 00:00+00'),
    (61, 59, 'Weights', 'weights', '2022-01-01 00:00+00'),
    (62, 55, 'Cycling', 'cycling', '2022-01-01 00:00+00'),
    (63, NULL, 'Toys & Games', 'toys-games', '2022-01-01 00:00+00'),
    (64, 63, 'Board Games', 'board-games', '2022-01-01 00:00+00'),
    (65, 63, 'Puzzles', 'puzzles', '2022-01-01 00:00+00'),
    (66, 63, 'Building Sets', 'building-sets', '2022-01-01 00:00+00'),
    (67, NULL, 'Beauty', 'beauty', '2022-01-01 00:00+00'),
    (68, 67, 'Skincare', 'skincare', '2022-01-01 00:00+00'),
    (69, 67, 'Fragrance', 'fragrance', '2022-01-01 00:00+00');
ALTER TABLE categories ALTER COLUMN id RESTART WITH 70;

-- -----------------------------------------------------------------------------
-- 2. Vocabularies. Product titles and descriptions are composed from these so
--    that full-text search (module 13) has real words to work with.
-- -----------------------------------------------------------------------------
-- Per "kind" of merchandise: adjectives, brands, features, uses, materials.
CREATE TEMP TABLE kind_vocab (
    kind       text PRIMARY KEY,
    adjs       text[],
    brands     text[],
    features   text[],
    uses       text[],
    materials  text[]
);
INSERT INTO kind_vocab VALUES
('electronics',
 '{Wireless,Smart,Portable,Pro,Ultra,Compact,Slim,Gaming,Studio,Mini,Max,Lite}',
 '{Voltix,Nordvik,Lumio,Arcadia,Zentro,Kairo,Pixelon,Sonora,Quanta,Brightline,Helix,Orbitron}',
 '{"a long-lasting battery","USB-C charging","Bluetooth 5.3","a premium aluminium body","fast charging","a two-year warranty","a low-latency connection","an anti-glare display","voice assistant support","a lightweight design","noise cancelling"}',
 '{"work and travel","gaming marathons","home offices","students","content creators","everyday use","commuting","video calls"}',
 '{aluminium,"recycled plastic",glass,"carbon fibre","magnesium alloy"}'),
('home',
 '{Classic,Modern,Rustic,Nordic,Premium,Essential,Deluxe,Compact,Vintage,Eco,Heavy-Duty,Handmade}',
 '{"Casa Verde","Hearth & Co",Oakhaven,Kitchara,Lindqvist,Terraform,Copperleaf,"Maison Belle",Greenthumb,Fjordhus}',
 '{"a non-slip base","an easy-clean finish","a timeless design","solid construction","dishwasher-safe parts","a lifetime guarantee","ergonomic handles","a weather-resistant coating"}',
 '{"everyday cooking","small apartments","family dinners","the home office","balconies and patios","keen gardeners","entertaining guests","gift giving"}',
 '{"stainless steel","cast iron","solid oak",bamboo,ceramic,terracotta,"tempered glass","recycled aluminium",walnut,enamel}'),
('book',
 '{Silent,Hidden,Last,Broken,Golden,Forgotten,Midnight,Crimson,Endless,Distant,Secret,Burning,Frozen,Wandering,Little,Lost,Wild,Quiet,Hungry,Brave}',
 '{"Penrose Press","Bluebird Books",Inkwell,"Harbor House",Northlight,"Quill & Lantern"}',
 '{"Shortlisted for several awards.","A bestseller in twelve countries.","Now a major TV series.","The first book in a new series.","With a new foreword by the author.","Includes reading-group questions.","Translated into thirty languages."}',
 '{"love and loss","a family secret",survival,friendship,betrayal,"first contact","a small town",redemption,power,memory,courage,ambition}',
 '{hardcover,paperback}'),
('clothing',
 '{Classic,Organic,Relaxed,Slim-Fit,Vintage,Lightweight,Heavyweight,Everyday,Premium,Recycled,Waterproof,Cozy}',
 '{Threadline,"Urban Loom","Alpine Thread",Coastline,"Nordic Wool Co","Indigo & Rye",Fernweh,Saltmarsh}',
 '{"breathable fabric","reinforced seams","a tailored fit","a soft brushed lining","garment-dyed colour","ethical production","machine-washable fabric","deep pockets"}',
 '{"everyday wear","the office","weekends outdoors","layering in autumn",travel,festivals}',
 '{"organic cotton","merino wool",linen,"recycled polyester",denim,fleece,leather,suede}'),
('sports',
 '{Ultralight,Pro,Trail,Compact,Adjustable,All-Season,Performance,Essential,Rugged,Foldable}',
 '{Summit,TrailPeak,IronCore,Zenflow,Velocita,Basecamp,Northridge}',
 '{"a lightweight frame","a quick setup","a carry bag","a non-slip grip","weatherproof construction","adjustable straps","reflective details"}',
 '{"backpacking trips","home workouts","yoga practice","weekend rides","family camping","beginners and pros"}',
 '{"ripstop nylon",aluminium,"natural rubber","cast iron","TPE foam",carbon}'),
('toys',
 '{Classic,Deluxe,Family,Junior,Ultimate,Travel,Educational,Giant,Mini,Wooden}',
 '{Brickworks,Puzzlemania,"Happy Owl","Gamewright Studio",TinyTown,Meeple}',
 '{"1000 pieces","easy-to-learn rules","sturdy components","a storage box","illustrated instructions","replay value for years"}',
 '{"family game night","ages 8 and up","rainy afternoons",parties,"curious kids","puzzle lovers"}',
 '{cardboard,wood,"recycled plastic","FSC paper"}'),
('beauty',
 '{Hydrating,Gentle,Radiant,Nourishing,Organic,Fragrance-Free,Daily,Intense,Botanical,Vegan}',
 '{Lumière,Botanica,"Pure Dew",Aurelia,Verdant,Crème de Lys}',
 '{"hyaluronic acid","vitamin C","shea butter","natural extracts","SPF 30","no parabens","a light texture","notes of bergamot and cedar"}',
 '{"sensitive skin","daily routines","dry skin","evenings out",travel,"all skin types"}',
 '{"glass bottle","recyclable tube","refillable jar"}');

-- Per leaf category: kind, nouns, price range (log-uniform between lo and hi).
CREATE TEMP TABLE leaf_vocab (
    category_id  int PRIMARY KEY,
    kind         text NOT NULL,
    nouns        text[] NOT NULL,
    price_lo     numeric NOT NULL,
    price_hi     numeric NOT NULL
);
INSERT INTO leaf_vocab VALUES
    (3,  'electronics', '{Laptop,Notebook,Ultrabook,"Gaming Laptop","2-in-1 Laptop",Chromebook}', 299, 2999),
    (4,  'electronics', '{Monitor,"4K Monitor","Curved Monitor","Portable Monitor","Gaming Monitor"}', 99, 999),
    (6,  'electronics', '{Headphones,Earbuds,"Over-Ear Headphones",Headset,"In-Ear Monitors"}', 19, 399),
    (7,  'electronics', '{"Bluetooth Speaker",Soundbar,"Smart Speaker","Bookshelf Speakers","Party Speaker"}', 25, 599),
    (24, 'electronics', '{Tablet,"Drawing Tablet",E-Reader,"Kids Tablet"}', 129, 1299),
    (25, 'electronics', '{"Mechanical Keyboard","Wireless Mouse","Ergonomic Keyboard","Gaming Mouse",Trackpad}', 15, 199),
    (26, 'electronics', '{"USB Microphone","Condenser Microphone","Lavalier Mic","Podcast Microphone"}', 29, 399),
    (28, 'electronics', '{Smartphone,Phone,"Rugged Phone","Flip Phone"}', 149, 1499),
    (29, 'electronics', '{"Phone Case","Leather Wallet Case","Clear Case","Screen Protector"}', 9, 59),
    (30, 'electronics', '{"USB-C Charger","Wireless Charger","Power Bank","Charging Cable","Car Charger"}', 7, 79),
    (32, 'electronics', '{"Mirrorless Camera","Action Camera","Instant Camera","Compact Camera","DSLR Camera"}', 199, 2999),
    (33, 'electronics', '{"Prime Lens","Zoom Lens","Wide-Angle Lens","Macro Lens","Telephoto Lens"}', 149, 2499),
    (10, 'home', '{"Frying Pan",Saucepan,Skillet,"Dutch Oven",Wok,Stockpot,"Sauté Pan"}', 19, 299),
    (11, 'home', '{"Chef''s Knife","Knife Set","Santoku Knife","Bread Knife","Cutting Board","Steak Knives"}', 9, 249),
    (34, 'home', '{"Baking Sheet","Muffin Tin","Cake Pan","Loaf Pan","Pie Dish","Cooling Rack"}', 9, 89),
    (35, 'home', '{Blender,Toaster,Kettle,"Coffee Maker","Espresso Machine","Air Fryer","Stand Mixer"}', 25, 499),
    (37, 'home', '{"Office Chair","Dining Chair",Armchair,"Bar Stool","Rocking Chair"}', 49, 899),
    (38, 'home', '{"Standing Desk","Writing Desk","Corner Desk","Computer Desk"}', 99, 1299),
    (39, 'home', '{Bookshelf,"Wall Shelf","Storage Rack","Ladder Shelf","Cube Organizer"}', 29, 499),
    (13, 'home', '{Planter,"Plant Pot","Hanging Planter","Raised Bed","Window Box"}', 9, 149),
    (40, 'home', '{"Pruning Shears","Garden Trowel",Rake,"Watering Can","Hose Reel",Spade}', 9, 199),
    (41, 'home', '{"Solar Lantern","String Lights","Path Lights","Wall Lantern",Floodlight}', 15, 249),
    (16, 'book', '{Orbit,Nebula,Colony,Starship,Horizon,Signal,Protocol,Machine,Frontier,Android,Galaxy,Void}', 6, 35),
    (17, 'book', '{Witness,Alibi,Lighthouse,Manor,Inheritance,Cipher,Verdict,Shadow,Suspect,Confession,Detective}', 6, 35),
    (42, 'book', '{Crown,Dragon,Sword,Kingdom,Sorcerer,Throne,Prophecy,Realm,Forest,Witch}', 6, 35),
    (43, 'book', '{Summer,Promise,Heart,Letters,Vineyard,Wedding,Kiss,Bookshop,Café}', 6, 30),
    (44, 'book', '{River,House,Garden,Daughter,Season,Island,Memory,Orchard,Weather}', 8, 35),
    (19, 'book', '{Kitchen,Table,Feast,Bread,Pasta,Spices,Pantry,Sourdough,Vegetables}', 12, 49),
    (20, 'book', '{Empire,Revolution,War,Century,Republic,Voyage,Dynasty,"Silk Road"}', 12, 45),
    (45, 'book', '{Life,Journey,Memoir,Years,Diary,Portrait}', 10, 40),
    (46, 'book', '{Universe,Brain,Ocean,Forest,Planet,Atom,Gene,Climate}', 12, 45),
    (47, 'book', '{Strategy,Startup,Leader,Habit,Negotiation,Market,Company}', 12, 45),
    (48, 'book', '{Bear,Rabbit,Moon,Adventure,Dinosaur,Bedtime,Train,Fox}', 5, 25),
    (22, 'clothing', '{T-Shirt,Tee,"Graphic Tee","V-Neck Tee","Polo Shirt","Tank Top"}', 9, 49),
    (23, 'clothing', '{Hoodie,"Zip Hoodie","Pullover Hoodie",Sweatshirt}', 29, 129),
    (49, 'clothing', '{"Rain Jacket","Down Jacket","Denim Jacket","Bomber Jacket",Parka,"Softshell Jacket"}', 49, 399),
    (50, 'clothing', '{"Slim Jeans","Straight Jeans","Relaxed Jeans","Bootcut Jeans","Selvedge Jeans"}', 29, 199),
    (51, 'clothing', '{"Summer Dress","Wrap Dress","Maxi Dress","Shirt Dress","Cocktail Dress"}', 25, 299),
    (53, 'clothing', '{"Running Shoes",Sneakers,"Trail Shoes","High-Top Sneakers","Canvas Shoes"}', 39, 249),
    (54, 'clothing', '{"Chelsea Boots","Hiking Boots","Rain Boots","Leather Boots","Winter Boots"}', 59, 399),
    (57, 'sports', '{Tent,"Backpacking Tent","Family Tent","Dome Tent","Pop-Up Tent"}', 59, 899),
    (58, 'sports', '{"Sleeping Bag","Mummy Sleeping Bag","Sleeping Pad","Camping Pillow"}', 29, 399),
    (60, 'sports', '{"Yoga Mat","Yoga Block","Yoga Strap","Meditation Cushion"}', 12, 129),
    (61, 'sports', '{"Dumbbell Set",Kettlebell,"Weight Plates","Adjustable Dumbbells","Resistance Bands"}', 15, 399),
    (62, 'sports', '{"Road Bike","Bike Helmet","Bike Lights","Cycling Gloves","Bike Lock","Saddle Bag"}', 9, 2499),
    (64, 'toys', '{"Board Game","Strategy Game","Party Game","Card Game","Chess Set"}', 15, 99),
    (65, 'toys', '{"Jigsaw Puzzle","3D Puzzle","Puzzle Mat","Brain Teaser"}', 9, 49),
    (66, 'toys', '{"Building Set","Brick Set","Construction Kit","Marble Run"}', 9, 299),
    (68, 'beauty', '{"Face Cream",Serum,Cleanser,Sunscreen,"Lip Balm","Face Mask"}', 6, 129),
    (69, 'beauty', '{"Eau de Parfum","Eau de Toilette","Perfume Oil","Body Mist"}', 19, 249);

-- Top-level category of every leaf, and the leaves of each top-level category.
CREATE TEMP TABLE leaf_root AS
WITH RECURSIVE up (category_id, node, parent_id) AS (
    SELECT l.category_id, c.id, c.parent_id
    FROM leaf_vocab l JOIN categories c ON c.id = l.category_id
    UNION ALL
    SELECT u.category_id, c.id, c.parent_id
    FROM up u JOIN categories c ON c.id = u.parent_id
)
SELECT category_id, node AS root_id FROM up WHERE parent_id IS NULL;

CREATE TEMP TABLE root_leaves AS
SELECT root_id, array_agg(category_id ORDER BY category_id) AS leaves
FROM leaf_root GROUP BY root_id;

-- -----------------------------------------------------------------------------
-- 3. Users: 100k, ids in sign-up order.
--    created_at = 2022-01-01 + span * (id/100000)^0.6: sign-ups accelerate over
--    time. Because created_at is a monotonic function of id, "all users who
--    existed at time t" is simply "id <= max_user_at(t)" (used for orders).
--    Country is weighted (US 28%, GB 10%, DE 10%, ...).
-- -----------------------------------------------------------------------------
CREATE TEMP TABLE names AS
SELECT
    '{James,Mary,John,Patricia,Robert,Jennifer,Michael,Linda,William,Elizabeth,David,Barbara,Richard,Susan,Joseph,Jessica,Thomas,Sarah,Charles,Karen,Daniel,Nancy,Matthew,Lisa,Anthony,Emma,Mark,Olivia,Paul,Sophia,Steven,Mia,Andrew,Isabella,Kevin,Ava,Brian,Charlotte,George,Amelia,Lukas,Hannah,Leon,Lena,Finn,Marie,Hugo,Chloé,Louis,Léa,Mateo,Lucía,Pablo,Sofía,Giulia,Lorenzo,Francesca,Marco,Haruto,Yui,Wei,Mei,Arjun,Priya,Rahul,Ananya,Olumide,Chiamaka,Lars,Ingrid,Søren,Zoë,Noah,Liam,Ethan,Aisha,Omar,Fatima,Ivan,Olga}'::text[] AS firsts,
    '{Smith,Johnson,Williams,Brown,Jones,Garcia,Miller,Davis,Rodriguez,Martinez,Hernández,Lopez,Wilson,Anderson,Taylor,Thomas,Moore,Jackson,Martin,Lee,Thompson,White,Harris,Clark,Lewis,Walker,Young,King,Wright,Scott,Green,Baker,Adams,Nelson,Hill,Müller,Schmidt,Schneider,Fischer,Weber,Meyer,Wagner,Becker,Dubois,Durand,Lefèvre,Moreau,Rossi,Russo,Ferrari,Esposito,Bianchi,Romano,Silva,Santos,Ferreira,Novák,Kowalski,Nowak,Jensen,Hansen,Nielsen,Andersson,Johansson,Berg,Sato,Suzuki,Takahashi,Tanaka,Wang,Li,Zhang,Chen,Nguyen,Tran,Kim,Park,Patel,Sharma,Singh,Okafor,O''Brien,O''Connor,Murphy,Kelly,MacDonald}'::text[] AS lasts,
    '{example.com,example.org,example.net,mail.example.com,inbox.example.net}'::text[] AS domains,
    -- country codes and cumulative thresholds for width_bucket()
    '{US,GB,DE,FR,IT,ES,CA,NL,PL,SE,AU,JP,IN,BR,IE,AT,CZ,DK,NO,PT,MX}'::text[] AS countries,
    '{0,.28,.38,.48,.56,.61,.66,.71,.75,.79,.82,.85,.88,.92,.95,.97,.98,.99,.993,.996,.998}'::float8[] AS country_cum;

CREATE TEMP TABLE country_cities (country_code text PRIMARY KEY, cities text[]);
INSERT INTO country_cities VALUES
    ('US', '{"New York","Los Angeles",Chicago,Houston,Phoenix,Seattle,Boston,Austin,Denver,Portland}'),
    ('GB', '{London,Manchester,Birmingham,Leeds,Glasgow,Bristol,Edinburgh}'),
    ('DE', '{Berlin,Hamburg,München,Köln,Frankfurt,Stuttgart,Leipzig}'),
    ('FR', '{Paris,Lyon,Marseille,Toulouse,Nice,Nantes,Bordeaux}'),
    ('IT', '{Roma,Milano,Napoli,Torino,Bologna,Firenze}'),
    ('ES', '{Madrid,Barcelona,Valencia,Sevilla,Bilbao}'),
    ('CA', '{Toronto,Montréal,Vancouver,Calgary,Ottawa,London}'),   -- London, Ontario!
    ('NL', '{Amsterdam,Rotterdam,Utrecht,"Den Haag"}'),
    ('PL', '{Warszawa,Kraków,Wrocław,Gdańsk}'),
    ('SE', '{Stockholm,Göteborg,Malmö}'),
    ('AU', '{Sydney,Melbourne,Brisbane,Perth}'),
    ('JP', '{Tokyo,Osaka,Kyoto,Yokohama}'),
    ('IN', '{Mumbai,Delhi,Bengaluru,Chennai,Pune}'),
    ('BR', '{"São Paulo","Rio de Janeiro","Belo Horizonte"}'),
    ('IE', '{Dublin,Cork,Galway}'),
    ('AT', '{Wien,Graz,Salzburg}'),
    ('CZ', '{Praha,Brno}'),
    ('DK', '{København,Aarhus}'),
    ('NO', '{Oslo,Bergen}'),
    ('PT', '{Lisboa,Porto}'),
    ('MX', '{"Ciudad de México",Guadalajara,Monterrey}');

CREATE TEMP TABLE user_tmp AS
SELECT
    u AS id,
    pg_temp.pick(n.firsts, pg_temp.rnd(u, 1)) AS first_name,
    pg_temp.pick(n.lasts,  pg_temp.rnd(u, 2)) AS last_name,
    pg_temp.pick(n.domains, pg_temp.rnd(u, 3)) AS domain,
    n.countries[width_bucket(pg_temp.rnd(u, 4), n.country_cum)] AS country_code,
    date_trunc('second',
        timestamptz '2022-01-01 00:00+00'
        + (timestamptz '2025-12-31 12:00+00' - timestamptz '2022-01-01 00:00+00') * power(u / 100000.0, 0.6)
    ) AS created_at
FROM generate_series(1, 100000) AS u
CROSS JOIN names n;

INSERT INTO users (id, email, full_name, is_active, created_at)
OVERRIDING SYSTEM VALUE
SELECT
    id,
    -- ~1% of addresses are typed in mixed case (Anna.Smith123@Example.com):
    -- the lower(email) unique index from module 08 treats them correctly.
    CASE WHEN pg_temp.rnd(id, 5) < 0.01
         THEN initcap(pg_temp.ascii_name(first_name)) || '.' || initcap(pg_temp.ascii_name(last_name)) || id || '@' || initcap(domain)
         ELSE pg_temp.ascii_name(first_name) || '.' || pg_temp.ascii_name(last_name) || id || '@' || domain
    END,
    first_name || ' ' || last_name,
    pg_temp.rnd(id, 6) >= 0.03,                     -- 3% deactivated accounts
    created_at
FROM user_tmp
ORDER BY id;
ALTER TABLE users ALTER COLUMN id RESTART WITH 100001;

-- -----------------------------------------------------------------------------
-- 4. Addresses: every user has a default address whose id equals the user id
--    (handy when generating orders); 25% also have a second (Work) address.
--    The city depends on the country (a functional dependency that module 12
--    uses to demonstrate extended statistics).
-- -----------------------------------------------------------------------------
INSERT INTO addresses (id, user_id, label, recipient, line1, line2, city, postal_code,
                       country_code, is_default, created_at)
OVERRIDING SYSTEM VALUE
SELECT
    a.id, a.user_id, a.label, a.recipient,
    (1 + floor(pg_temp.rnd(a.id, 11) * 240))::int || ' '
        || pg_temp.pick('{"Market Street","Oak Avenue","High Street","Station Road","Mill Lane","Park Road","Church Street","Harbour Road","Elm Road","Victoria Road","King Street","Maple Drive"}'::text[], pg_temp.rnd(a.id, 12)),
    CASE WHEN pg_temp.rnd(a.id, 13) < 0.2 THEN 'Apt ' || (1 + floor(pg_temp.rnd(a.id, 14) * 60))::int END,
    pg_temp.pick(cc.cities, pg_temp.rnd(a.id, 15)),
    CASE
        WHEN a.country_code IN ('GB', 'IE', 'CA', 'NL')
            THEN upper(substr(md5(a.id::text), 1, 3)) || ' ' || upper(substr(md5(a.id::text), 4, 3))
        WHEN a.country_code IN ('AT', 'DK', 'NO', 'AU')
            THEN lpad((floor(pg_temp.rnd(a.id, 16) * 9000) + 1000)::int::text, 4, '0')
        ELSE lpad(floor(pg_temp.rnd(a.id, 16) * 99999)::int::text, 5, '0')
    END,
    a.country_code,
    a.is_default,
    a.created_at
FROM (
    SELECT u.id, u.id AS user_id, 'Home' AS label, u.first_name || ' ' || u.last_name AS recipient,
           u.country_code, true AS is_default, u.created_at + interval '1 hour' AS created_at
    FROM user_tmp u
    UNION ALL
    SELECT 100000 + row_number() OVER (ORDER BY u.id), u.id, 'Work', u.first_name || ' ' || u.last_name,
           u.country_code, false, least(u.created_at + interval '30 days', timestamptz '2025-12-31 23:00+00')
    FROM user_tmp u
    WHERE pg_temp.rnd(u.id, 10) < 0.25
) a
JOIN country_cities cc ON cc.country_code = a.country_code
ORDER BY a.id;
SELECT setval(pg_get_serial_sequence('addresses', 'id'), max(id)) FROM addresses \g /dev/null

-- -----------------------------------------------------------------------------
-- 5. Sellers: 2000 shops. Seller s is owned by user s*37 and opens 1-120 days
--    after that user signed up. Each seller specialises in one top-level
--    category (its "home"); the five biggest are general stores.
-- -----------------------------------------------------------------------------
CREATE TEMP TABLE seller_tmp AS
SELECT
    s AS id,
    s * 37 AS user_id,
    u.created_at + interval '1 day' * (1 + floor(pg_temp.rnd(s, 21) * 120)) AS created_at,
    u.country_code,
    -- home top-level category: Electronics 30%, Home 18%, Books 15%, Clothing 17%,
    -- Sports 8%, Toys 6%, Beauty 6%
    ('{1,8,14,21,55,63,67}'::int[])[width_bucket(pg_temp.rnd(s, 22), '{0,.30,.48,.63,.80,.88,.94}'::float8[])] AS home_root,
    s <= 5 AS is_general
FROM generate_series(1, 2000) AS s
JOIN user_tmp u ON u.id = s * 37;

INSERT INTO sellers (id, user_id, shop_name, country_code, created_at)
OVERRIDING SYSTEM VALUE
SELECT
    s.id,
    s.user_id,
    -- e.g. 'Nordic Corner 17': unique thanks to the id suffix
    pg_temp.pick('{Nordic,Urban,Golden,Little,Happy,Blue,Green,Wild,Royal,Rustic,Bright,Cozy,Modern,Vintage,Lucky,Silver}'::text[], pg_temp.rnd(s.id, 23))
        || ' ' || pg_temp.pick('{Corner,Market,Emporium,Store,Shop,Outlet,Studio,Workshop,Bazaar,Supply,Goods,Trading}'::text[], pg_temp.rnd(s.id, 24))
        || ' ' || s.id,
    s.country_code,
    s.created_at
FROM seller_tmp s
ORDER BY s.id;
ALTER TABLE sellers ALTER COLUMN id RESTART WITH 2001;

-- -----------------------------------------------------------------------------
-- 6. Products: 200k.
--    seller = floor(exp(r * ln 2001)) gives P(seller = s) ~ ln((s+1)/s) ~ 1/s:
--    a Zipf-like distribution (seller 1 ~9% of products, seller 2000 ~13 products).
--    Category: 85% from the seller's home tree, 15% anywhere.
--    Status: 85% active, 7% draft, 8% archived.
--    15% of products changed price once (old_price before change_at).
--    Rows are generated with a key k and renumbered by created_at, so product
--    ids are chronological too.
-- -----------------------------------------------------------------------------
CREATE TEMP TABLE prod_tmp AS
WITH base AS (
    SELECT k, least(2000, floor(exp(pg_temp.rnd(k, 101) * ln(2001))))::int AS seller_id
    FROM generate_series(1, 200000) AS k
),
placed AS (
    SELECT
        b.k,
        b.seller_id,
        CASE
            WHEN s.is_general OR pg_temp.rnd(b.k, 102) < 0.15
                THEN pg_temp.pick((SELECT array_agg(category_id ORDER BY category_id) FROM leaf_vocab), pg_temp.rnd(b.k, 103))
            ELSE pg_temp.pick(rl.leaves, pg_temp.rnd(b.k, 103))
        END AS category_id,
        -- created between the shop's opening and 2025-12-01, skewed to older
        s.created_at + (timestamptz '2025-12-01 00:00+00' - s.created_at) * power(pg_temp.rnd(b.k, 104), 1.5) AS created_at
    FROM base b
    JOIN seller_tmp s ON s.id = b.seller_id
    JOIN root_leaves rl ON rl.root_id = s.home_root
),
priced AS (
    SELECT
        p.*,
        lv.kind,
        lv.nouns,
        -- log-uniform price, then rounded to a "charm" price ending in .99
        lv.price_lo * power(lv.price_hi / lv.price_lo, pg_temp.rnd(p.k, 105)) AS raw_price,
        CASE
            WHEN pg_temp.rnd(p.k, 106) < 0.07 THEN 'draft'
            WHEN pg_temp.rnd(p.k, 106) < 0.15 THEN 'archived'
            ELSE 'active'
        END AS status
    FROM placed p
    JOIN leaf_vocab lv ON lv.category_id = p.category_id
)
SELECT
    p.k,
    p.seller_id,
    p.category_id,
    p.kind,
    date_trunc('second', p.created_at) AS created_at,
    p.status,
    CASE WHEN p.raw_price >= 200 THEN round(p.raw_price / 10) * 10 - 0.01
         ELSE round(p.raw_price) - 0.01 END::numeric(12,2) AS price,
    pg_temp.pick(kv.brands, pg_temp.rnd(p.k, 107)) AS brand,
    pg_temp.pick(kv.adjs, pg_temp.rnd(p.k, 108))   AS adj,
    pg_temp.pick(kv.adjs, pg_temp.rnd(p.k, 109))   AS adj2,
    pg_temp.pick(p.nouns, pg_temp.rnd(p.k, 110))   AS noun,
    pg_temp.pick(kv.features, pg_temp.rnd(p.k, 111)) AS feature1,
    pg_temp.pick(kv.features, pg_temp.rnd(p.k, 112)) AS feature2,
    pg_temp.pick(kv.uses, pg_temp.rnd(p.k, 113))     AS use_case,
    pg_temp.pick(kv.uses, pg_temp.rnd(p.k, 117))     AS use_case2,
    pg_temp.pick(kv.materials, pg_temp.rnd(p.k, 114)) AS material,
    pg_temp.pick('{Black,White,Silver,Navy,Red,Green,Grey,Blue,Beige,Olive,Pink,Graphite}'::text[], pg_temp.rnd(p.k, 115)) AS color,
    pg_temp.rnd(p.k, 116) < 0.15 AS price_changed
FROM priced p
JOIN kind_vocab kv ON kv.kind = p.kind;

-- Final ids: chronological. Also derive archive time and the price change.
CREATE TEMP TABLE prod AS
SELECT
    row_number() OVER (ORDER BY t.created_at, t.k) AS id,
    t.*,
    CASE WHEN t.status = 'archived'
         THEN date_trunc('second', t.created_at + (timestamptz '2025-12-31 00:00+00' - t.created_at) * (0.3 + 0.6 * pg_temp.rnd(t.k, 120)))
    END AS archived_at,
    CASE WHEN t.price_changed
         THEN date_trunc('second', t.created_at + (timestamptz '2025-12-31 00:00+00' - t.created_at) * (0.2 + 0.6 * pg_temp.rnd(t.k, 121)))
    END AS change_at,
    CASE WHEN t.price_changed
         -- the old price was 20% lower to 25% higher, also ending in .99
         THEN greatest(round(t.price * (0.8 + 0.45 * pg_temp.rnd(t.k, 122))) - 0.01, 0.99)::numeric(12,2)
    END AS old_price
FROM prod_tmp t;
CREATE UNIQUE INDEX ON prod (id);
ANALYZE prod;

INSERT INTO products (id, seller_id, category_id, sku, title, description, price, status,
                      created_at, updated_at, attributes, tags)
OVERRIDING SYSTEM VALUE
SELECT
    p.id,
    p.seller_id,
    p.category_id,
    'S' || p.seller_id || '-' || lpad(p.id::text, 6, '0'),
    -- Titles: "Voltix Wireless Headphones 420", "The Silent Witness", ...
    CASE p.kind
        WHEN 'book' THEN
            CASE WHEN pg_temp.rnd(p.k, 130) < 0.3
                 THEN 'The ' || p.noun || ' of ' || pg_temp.pick('{Avalon,"Kestrel Bay","the North",Brightwater,Saturn,"Elm Street",Ashford,"the Deep","Old Harbour",Marrow}'::text[], pg_temp.rnd(p.k, 131))
                 ELSE 'The ' || p.adj || ' ' || p.noun
            END
        WHEN 'electronics' THEN p.brand || ' ' || p.adj || ' ' || p.noun || ' ' || (100 + floor(pg_temp.rnd(p.k, 132) * 900))::int
        WHEN 'clothing'    THEN p.brand || ' ' || p.adj || ' ' || p.noun || ' - ' || p.color
        ELSE p.brand || ' ' || p.adj || ' ' || p.noun
            || CASE WHEN pg_temp.rnd(p.k, 133) < 0.4 THEN ' - ' || p.color ELSE '' END
    END,
    -- Descriptions: two or three sentences; 5% have none.
    CASE
        WHEN pg_temp.rnd(p.k, 134) < 0.05 THEN NULL
        WHEN p.kind = 'book' THEN
            format('A %s story of %s and %s. %s Published by %s.',
                   lower(p.adj2), p.use_case, p.use_case2, p.feature1, p.brand)
        ELSE
            format('%s %s from %s, made of %s. Features %s and %s. Perfect for %s.',
                   p.adj2, lower(p.noun), p.brand, p.material, p.feature1, p.feature2, p.use_case)
    END,
    p.price,
    p.status::product_status,
    p.created_at,
    greatest(p.created_at, p.change_at, p.archived_at),
    -- attributes: category-dependent JSON documents
    CASE p.kind
        WHEN 'electronics' THEN
            jsonb_build_object('brand', p.brand, 'color', p.color,
                               'warranty_years', 1 + floor(pg_temp.rnd(p.k, 140) * 3)::int)
            || CASE p.category_id
                WHEN 3 THEN jsonb_build_object(
                    'ram_gb', pg_temp.pick('{8,16,16,32,64}'::int[], pg_temp.rnd(p.k, 141)),
                    'cpu', pg_temp.pick('{"M-Core 8","M-Core 12","Ryzon 5","Ryzon 7","Ryzon 9","Celera N","Core X7"}'::text[], pg_temp.rnd(p.k, 142)),
                    'screen_in', pg_temp.pick('{13.3,14,15.6,16,17.3}'::numeric[], pg_temp.rnd(p.k, 143)),
                    'storage', jsonb_build_object('type', 'ssd', 'gb', pg_temp.pick('{256,512,1024,2048}'::int[], pg_temp.rnd(p.k, 144))))
                WHEN 4 THEN jsonb_build_object(
                    'size_in', pg_temp.pick('{24,27,32,34}'::int[], pg_temp.rnd(p.k, 141)),
                    'resolution', pg_temp.pick('{1920x1080,2560x1440,3840x2160,3440x1440}'::text[], pg_temp.rnd(p.k, 142)),
                    'refresh_hz', pg_temp.pick('{60,75,144,165,240}'::int[], pg_temp.rnd(p.k, 143)))
                WHEN 6 THEN jsonb_build_object(
                    'wireless', pg_temp.rnd(p.k, 141) < 0.7,
                    'anc', pg_temp.rnd(p.k, 142) < 0.4,
                    'battery_h', pg_temp.pick('{8,20,30,40,60}'::int[], pg_temp.rnd(p.k, 143)))
                WHEN 28 THEN jsonb_build_object(
                    'storage_gb', pg_temp.pick('{64,128,256,512}'::int[], pg_temp.rnd(p.k, 141)),
                    'screen_in', pg_temp.pick('{5.8,6.1,6.4,6.7}'::numeric[], pg_temp.rnd(p.k, 142)),
                    '5g', pg_temp.rnd(p.k, 143) < 0.8)
                ELSE '{}'::jsonb
               END
        WHEN 'book' THEN
            jsonb_build_object(
                'author', pg_temp.pick((SELECT firsts FROM names), pg_temp.rnd(p.k, 141)) || ' '
                          || pg_temp.pick((SELECT lasts FROM names), pg_temp.rnd(p.k, 142)),
                'pages', 80 + floor(pg_temp.rnd(p.k, 143) * 700)::int,
                'isbn', '978' || lpad(p.id::text, 10, '0'),
                'language', pg_temp.pick('{en,en,en,en,en,en,de,fr,es,it}'::text[], pg_temp.rnd(p.k, 144)),
                'format', p.material)
        WHEN 'clothing' THEN
            jsonb_build_object(
                'size', CASE WHEN p.category_id IN (53, 54)
                             THEN '[38,39,40,41,42,43,44,45,46]'::jsonb
                             WHEN pg_temp.rnd(p.k, 141) < 0.5 THEN '["S","M","L","XL"]'::jsonb
                             ELSE '["XS","S","M","L","XL","XXL"]'::jsonb END,
                'color', p.color,
                'material', p.material,
                'fit', pg_temp.pick('{regular,slim,relaxed}'::text[], pg_temp.rnd(p.k, 142)))
        WHEN 'home' THEN
            jsonb_build_object(
                'material', p.material,
                'color', p.color,
                'dimensions_cm', jsonb_build_object(
                    'w', 10 + floor(pg_temp.rnd(p.k, 141) * 150)::int,
                    'h', 5 + floor(pg_temp.rnd(p.k, 142) * 150)::int,
                    'd', 5 + floor(pg_temp.rnd(p.k, 143) * 80)::int))
        WHEN 'sports' THEN
            jsonb_build_object('material', p.material, 'color', p.color,
                               'weight_kg', round((0.1 + pg_temp.rnd(p.k, 141) * 15)::numeric, 1))
        WHEN 'toys' THEN
            jsonb_build_object('age_min', pg_temp.pick('{3,6,8,10,12,14}'::int[], pg_temp.rnd(p.k, 141)),
                               'players', jsonb_build_object('min', 1 + floor(pg_temp.rnd(p.k, 142) * 2)::int,
                                                             'max', 2 + floor(pg_temp.rnd(p.k, 143) * 6)::int))
            || CASE WHEN p.category_id = 65
                    THEN jsonb_build_object('pieces', pg_temp.pick('{500,1000,1500,2000}'::int[], pg_temp.rnd(p.k, 144)))
                    ELSE '{}'::jsonb END
        WHEN 'beauty' THEN
            jsonb_build_object('volume_ml', pg_temp.pick('{15,30,50,100}'::int[], pg_temp.rnd(p.k, 141)),
                               'skin_type', pg_temp.pick('{all,dry,oily,sensitive,combination}'::text[], pg_temp.rnd(p.k, 142)),
                               'vegan', pg_temp.rnd(p.k, 143) < 0.5)
        ELSE '{}'::jsonb
    END,
    -- tags: kind + category slug + a few skewed marketing tags
    ARRAY[p.kind, c.slug]
        || CASE WHEN pg_temp.rnd(p.k, 150) < 0.08 THEN '{bestseller}'::text[] ELSE '{}' END
        || CASE WHEN pg_temp.rnd(p.k, 151) < 0.15 THEN '{gift}'::text[]       ELSE '{}' END
        || CASE WHEN pg_temp.rnd(p.k, 152) < 0.10 THEN '{eco}'::text[]        ELSE '{}' END
        || CASE WHEN p.created_at > timestamptz '2025-09-01 00:00+00' THEN '{new}'::text[] ELSE '{}' END
        || CASE WHEN pg_temp.rnd(p.k, 153) < 0.02 THEN '{limited-edition}'::text[] ELSE '{}' END
FROM prod p
JOIN categories c ON c.id = p.category_id
ORDER BY p.id;
ALTER TABLE products ALTER COLUMN id RESTART WITH 200001;

-- Price history: the initial price at created_at, plus one row per change.
INSERT INTO product_price_history (product_id, price, valid_from)
SELECT id, coalesce(old_price, price), created_at FROM prod
UNION ALL
SELECT id, price, change_at FROM prod WHERE price_changed
ORDER BY 1, 3;

-- Per-seller product list, used to pick the 2nd..nth item of an order.
CREATE TEMP TABLE seller_products AS
SELECT seller_id, row_number() OVER (PARTITION BY seller_id ORDER BY id) AS rn, id AS product_id
FROM prod
WHERE status <> 'draft';
CREATE UNIQUE INDEX ON seller_products (seller_id, rn);
CREATE TEMP TABLE seller_product_counts AS
SELECT seller_id, count(*) AS n FROM seller_products GROUP BY seller_id;
CREATE UNIQUE INDEX ON seller_product_counts (seller_id);
ANALYZE seller_products;
ANALYZE seller_product_counts;

-- -----------------------------------------------------------------------------
-- 7. Orders: 500k.
--    The day of each order is drawn from a weight per day (growth trend x
--    season x weekday x Black Friday) by inverse-CDF sampling: width_bucket()
--    binary-searches the cumulative weights. Same for the hour of day.
--    An order starts from an "anchor" product drawn with popularity skew
--    (older products sell more); the seller is the anchor's seller and the
--    other items come from the same seller. Candidates whose anchor did not
--    exist yet / was a draft / was already archived are discarded, and the
--    first 500k valid candidates are kept.
-- -----------------------------------------------------------------------------
CREATE TEMP TABLE order_days AS
SELECT
    d::date AS day,
    (1 + 1.2 * (d::date - date '2023-01-01') / 1095.0)                       -- growth
    * CASE WHEN to_char(d, 'MM-DD') BETWEEN '11-20' AND '12-23' THEN 1.8      -- holiday season
           WHEN extract(month FROM d) IN (1, 2) THEN 0.85
           WHEN extract(month FROM d) IN (7, 8) THEN 0.9
           ELSE 1 END
    * CASE extract(isodow FROM d) WHEN 6 THEN 1.15 WHEN 7 THEN 1.25 WHEN 1 THEN 0.95 ELSE 1 END
    * CASE WHEN d::date IN (date '2023-11-24', date '2024-11-29', date '2025-11-28') THEN 3 ELSE 1 END  -- Black Friday
    AS w
FROM generate_series(timestamp '2023-01-01', timestamp '2025-12-31', interval '1 day') AS d;

CREATE TEMP TABLE sampling AS
SELECT
    (SELECT array_agg(c ORDER BY day) FROM (
        SELECT day, (coalesce(sum(w) OVER (ORDER BY day ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0)
                     / sum(w) OVER ())::float8 AS c
        FROM order_days) x) AS day_cum,
    -- hour-of-day weights (UTC): quiet nights, busy evenings
    (SELECT array_agg(c ORDER BY h) FROM (
        SELECT h, coalesce(sum(w) OVER (ORDER BY h ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0)
                  / sum(w) OVER () AS c
        FROM unnest('{2,1,1,1,1,2,3,5,6,7,7,8,8,7,7,7,8,9,10,11,11,9,6,4}'::float8[]) WITH ORDINALITY AS t (w, h)) x) AS hour_cum;

CREATE TEMP TABLE ord_cand AS
SELECT
    c,
    date '2023-01-01' + (width_bucket(pg_temp.rnd(c, 201), s.day_cum) - 1)
        + make_interval(hours => width_bucket(pg_temp.rnd(c, 202), s.hour_cum) - 1,
                        secs  => floor(pg_temp.rnd(c, 203) * 3600))
        AS created_ts,
    1 + floor(200000 * power(pg_temp.rnd(c, 204), 2))::bigint AS anchor
FROM generate_series(1, 720000) AS c
CROSS JOIN sampling s;

CREATE TEMP TABLE ord AS
WITH valid AS (
    SELECT
        oc.c,
        (oc.created_ts AT TIME ZONE 'UTC') AS created_at,   -- timestamp -> timestamptz (UTC)
        oc.anchor,
        p.seller_id
    FROM ord_cand oc
    JOIN prod p ON p.id = oc.anchor
    WHERE p.created_at < (oc.created_ts AT TIME ZONE 'UTC')
      AND p.status <> 'draft'
      AND (p.archived_at IS NULL OR (oc.created_ts AT TIME ZONE 'UTC') < p.archived_at)
),
kept AS (
    SELECT * FROM valid ORDER BY c LIMIT 500000
),
with_user AS (
    SELECT
        k.*,
        -- users who existed at created_at are exactly ids 1..max_uid (see users)
        greatest(1, floor(100000 * power(
            extract(epoch FROM k.created_at - timestamptz '2022-01-01 00:00+00')
            / extract(epoch FROM timestamptz '2025-12-31 12:00+00' - timestamptz '2022-01-01 00:00+00'),
            1 / 0.6))::bigint - 1) AS max_uid
    FROM kept k
),
buyer AS (
    SELECT
        w.*,
        -- skewed towards early (loyal) customers; never the shop owner
        CASE WHEN b.u = w.seller_id * 37 THEN b.u - 1 ELSE b.u END AS user_id,
        extract(epoch FROM timestamptz '2025-12-31 23:59:59+00' - w.created_at) / 86400 AS age_days,
        pg_temp.rnd(w.c, 210) AS r_status
    FROM with_user w
    CROSS JOIN LATERAL (SELECT 1 + floor(w.max_uid * power(pg_temp.rnd(w.c, 205), 1.4))::bigint AS u) b
),
statused AS (
    SELECT
        b.*,
        CASE
            WHEN b.r_status < 0.05 THEN 'cancelled'
            WHEN b.r_status < 0.08 AND b.age_days > 14 THEN 'refunded'
            WHEN b.age_days < 2 THEN CASE WHEN pg_temp.rnd(b.c, 211) < 0.6 THEN 'pending' ELSE 'paid' END
            WHEN b.age_days < 5 THEN CASE WHEN pg_temp.rnd(b.c, 211) < 0.15 THEN 'pending'
                                          WHEN pg_temp.rnd(b.c, 211) < 0.5 THEN 'paid' ELSE 'shipped' END
            WHEN b.age_days < 12 THEN CASE WHEN pg_temp.rnd(b.c, 211) < 0.5 THEN 'shipped' ELSE 'delivered' END
            ELSE 'delivered'
        END AS status0,
        b.created_at + make_interval(mins => 2 + floor(pg_temp.rnd(b.c, 212) * 178)::int) AS paid_ts,
        make_interval(days => 1 + floor(pg_temp.rnd(b.c, 213) * 3)::int, hours => floor(pg_temp.rnd(b.c, 214) * 9)::int) AS ship_delay,
        make_interval(days => 2 + floor(pg_temp.rnd(b.c, 215) * 6)::int, hours => floor(pg_temp.rnd(b.c, 216) * 9)::int) AS deliver_delay,
        pg_temp.rnd(b.c, 217) < 0.6 AS refund_after_delivery
    FROM buyer b
),
timed AS (
    SELECT
        s.*,
        CASE WHEN s.status0 IN ('shipped', 'delivered') OR (s.status0 = 'refunded' AND s.refund_after_delivery)
             THEN s.paid_ts + s.ship_delay END AS ship_ts
    FROM statused s
)
SELECT
    row_number() OVER (ORDER BY t.created_at, t.c) AS id,
    t.c, t.anchor, t.seller_id, t.user_id, t.created_at,
    -- downgrade statuses whose next timestamp would lie after "today"
    CASE
        WHEN t.status0 IN ('paid', 'shipped', 'delivered') AND t.paid_ts > timestamptz '2025-12-31 23:59:59+00'
            THEN 'pending'
        WHEN t.status0 = 'delivered' AND t.ship_ts + t.deliver_delay > timestamptz '2025-12-31 23:59:59+00'
            THEN CASE WHEN t.ship_ts > timestamptz '2025-12-31 23:59:59+00' THEN 'paid' ELSE 'shipped' END
        WHEN t.status0 = 'shipped' AND t.ship_ts > timestamptz '2025-12-31 23:59:59+00' THEN 'paid'
        ELSE t.status0
    END AS status,
    t.paid_ts,
    t.ship_ts,
    t.deliver_delay
FROM timed t;

-- Final timestamps from the (possibly downgraded) status.
ALTER TABLE ord ADD COLUMN paid_at timestamptz, ADD COLUMN shipped_at timestamptz, ADD COLUMN delivered_at timestamptz;
UPDATE ord SET
    paid_at = CASE WHEN status IN ('paid', 'shipped', 'delivered', 'refunded') THEN paid_ts END,
    shipped_at = CASE WHEN status IN ('shipped', 'delivered') OR (status = 'refunded' AND ship_ts IS NOT NULL) THEN ship_ts END,
    delivered_at = CASE WHEN status = 'delivered' OR (status = 'refunded' AND ship_ts IS NOT NULL) THEN ship_ts + deliver_delay END;
-- A refund after delivery must have happened before "today".
UPDATE ord SET shipped_at = NULL, delivered_at = NULL
WHERE status = 'refunded' AND delivered_at > timestamptz '2025-12-20 00:00+00';
CREATE UNIQUE INDEX ON ord (id);
ANALYZE ord;

-- -----------------------------------------------------------------------------
-- 8. Order items: the anchor plus 0-6 more products of the same seller.
--    Items that did not exist / were drafts / were archived at order time are
--    dropped; duplicates within an order are collapsed (DISTINCT ON).
--    unit_price = the price valid at order time (old_price before change_at).
-- -----------------------------------------------------------------------------
CREATE TEMP TABLE items AS
WITH lines AS (
    SELECT o.id AS order_id, o.created_at, o.anchor AS product_id, 1 AS line
    FROM ord o
    UNION ALL
    SELECT o.id, o.created_at, sp.product_id, g.line
    FROM ord o
    JOIN seller_product_counts spc ON spc.seller_id = o.seller_id
    -- number of lines drawn: 1:22% 2:24% 3:20% 4:14% 5:10% 6:6% 7:4%
    -- (~3.0 on average; ~2.6 items survive validity filtering and de-duplication)
    CROSS JOIN LATERAL generate_series(2, width_bucket(pg_temp.rnd(o.c, 301), '{0,.22,.46,.66,.80,.90,.96}'::float8[])) AS g (line)
    JOIN seller_products sp
      ON sp.seller_id = o.seller_id
     AND sp.rn = 1 + floor(spc.n * power(pg_temp.rnd(o.c * 8 + g.line, 302), 1.3))::bigint
)
SELECT DISTINCT ON (l.order_id, l.product_id)
    l.order_id,
    l.product_id,
    width_bucket(pg_temp.rnd(l.order_id * 8 + l.line, 303), '{0,.78,.93,.98,.995}'::float8[]) AS quantity,
    CASE WHEN p.change_at IS NOT NULL AND l.created_at < p.change_at THEN p.old_price ELSE p.price END AS unit_price
FROM lines l
JOIN prod p ON p.id = l.product_id
WHERE p.created_at < l.created_at
  AND p.status <> 'draft'
  AND (p.archived_at IS NULL OR l.created_at < p.archived_at)
ORDER BY l.order_id, l.product_id, l.line;

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT order_id, product_id, quantity, unit_price
FROM items
ORDER BY order_id, product_id;

-- Orders, with the total computed from the items (the total-maintenance
-- trigger is disabled during the load, so we do its job here, exactly).
INSERT INTO orders (id, user_id, seller_id, status, total, created_at, paid_at, shipped_at,
                    delivered_at, shipping_address_id, updated_at)
OVERRIDING SYSTEM VALUE
SELECT
    o.id, o.user_id, o.seller_id, o.status::order_status, t.total, o.created_at, o.paid_at,
    o.shipped_at, o.delivered_at,
    -- default address (id = user id), or the Work address for 20% of orders if there is one
    coalesce(CASE WHEN pg_temp.rnd(o.id, 401) < 0.2 THEN w.id END, o.user_id),
    greatest(o.created_at, o.paid_at, o.shipped_at, o.delivered_at)
FROM ord o
JOIN (SELECT order_id, sum(quantity * unit_price) AS total FROM items GROUP BY order_id) t
  ON t.order_id = o.id
LEFT JOIN addresses w ON w.user_id = o.user_id AND NOT w.is_default
ORDER BY o.id;
SELECT setval(pg_get_serial_sequence('orders', 'id'), max(id)) FROM orders \g /dev/null

-- -----------------------------------------------------------------------------
-- 9. Payments: a succeeded payment per paid order (8% after a failed attempt),
--    a negative refund for refunded orders, pending payments for half of the
--    pending orders, failed ones for 40% of the cancelled orders.
-- -----------------------------------------------------------------------------
INSERT INTO payments (order_id, amount, provider, provider_ref, status, created_at)
SELECT order_id, amount, provider, provider_ref, status::payment_status, created_at
FROM (
    SELECT
        o.id AS order_id,
        x.amount,
        pg_temp.pick('{stripe,stripe,stripe,stripe,paypal,paypal,adyen}'::text[], pg_temp.rnd(o.id, 501)) AS provider,
        substr(md5(o.id::text || x.kind), 1, 16) AS provider_ref,
        x.status,
        x.created_at
    FROM orders o
    CROSS JOIN LATERAL (
        VALUES
            ('fail',    o.total,  'failed',    o.paid_at - interval '3 minutes',
             o.paid_at IS NOT NULL AND pg_temp.rnd(o.id, 502) < 0.08),
            ('pay',     o.total,  'succeeded', o.paid_at,
             o.paid_at IS NOT NULL),
            ('refund', -o.total,  'succeeded', least(coalesce(o.delivered_at, o.paid_at) + interval '1 day' * (1 + floor(pg_temp.rnd(o.id, 503) * 5)),
                                                     timestamptz '2025-12-31 23:00+00'),
             o.status = 'refunded'),
            ('pending', o.total,  'pending',   o.created_at + interval '1 minute',
             o.status = 'pending' AND pg_temp.rnd(o.id, 504) < 0.5),
            ('cancel',  o.total,  'failed',    o.created_at + interval '2 minutes',
             o.status = 'cancelled' AND pg_temp.rnd(o.id, 505) < 0.4)
    ) AS x (kind, amount, status, created_at, applies)
    WHERE x.applies
) p
ORDER BY created_at, order_id;

-- -----------------------------------------------------------------------------
-- 10. Reviews: ~25% of delivered (user, product) pairs, written 0-30 days
--     after delivery, ratings skewed positive; plus ~2% "unverified" reviews by
--     users who never bought the product.
-- -----------------------------------------------------------------------------
INSERT INTO reviews (product_id, user_id, rating, title, body, created_at)
SELECT product_id, user_id, rating, title, body, created_at
FROM (
    SELECT DISTINCT ON (i.product_id, o.user_id)
        i.product_id,
        o.user_id,
        r.rating,
        CASE WHEN pg_temp.rnd(o.id * 8 + i.product_id, 603) < 0.15 THEN NULL
             ELSE (ARRAY['Do not buy', 'Disappointing', 'It''s OK', 'Very good', 'Excellent!'])[r.rating] END AS title,
        CASE WHEN pg_temp.rnd(o.id * 8 + i.product_id, 604) < 0.25 THEN NULL
             ELSE (ARRAY['Broke after a week. Returned it.',
                         'Quality is not what the photos suggest.',
                         'Does the job, nothing special. Shipping was slow.',
                         'Good value for the price, would recommend.',
                         'Fantastic quality and fast shipping. Love it!'])[r.rating] END AS body,
        least(o.delivered_at + interval '1 hour' * floor(pg_temp.rnd(o.id * 8 + i.product_id, 605) * 720),
              timestamptz '2025-12-31 23:59:59+00') AS created_at
    FROM orders o
    JOIN order_items i ON i.order_id = o.id
    CROSS JOIN LATERAL (
        SELECT width_bucket(pg_temp.rnd(o.id * 8 + i.product_id, 602), '{0,.05,.11,.23,.50}'::float8[]) AS rating
    ) r
    WHERE o.status = 'delivered'
      AND pg_temp.rnd(o.id * 8 + i.product_id, 601) < 0.25
    ORDER BY i.product_id, o.user_id, o.delivered_at
) verified
ORDER BY created_at;

INSERT INTO reviews (product_id, user_id, rating, title, body, created_at)
SELECT p.id, u.id, 1 + floor(pg_temp.rnd(g, 612) * 5)::int, NULL, 'Saw it at a friend''s place.',
       least(greatest(p.created_at, u.created_at) + interval '3 days', timestamptz '2025-12-31 23:30+00')
FROM generate_series(1, 6500) AS g
JOIN products p ON p.id = 1 + floor(pg_temp.rnd(g, 610) * 200000)::bigint
JOIN users u    ON u.id = 1 + floor(pg_temp.rnd(g, 611) * 100000)::bigint
WHERE p.status <> 'draft'
  AND NOT EXISTS (            -- really unverified: the user never bought it
      SELECT 1 FROM orders o JOIN order_items i ON i.order_id = o.id
      WHERE o.user_id = u.id AND i.product_id = p.id)
ORDER BY 6
ON CONFLICT (product_id, user_id) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 11. Open carts: ~10k active users with 1-4 active products each, December 2025.
-- -----------------------------------------------------------------------------
INSERT INTO cart_items (user_id, product_id, quantity, added_at)
SELECT DISTINCT ON (u, p.id)
    u, p.id,
    width_bucket(pg_temp.rnd(u * 4 + g, 703), '{0,.8,.95}'::float8[]),
    timestamptz '2025-12-01 00:00+00' + interval '1 minute' * floor(pg_temp.rnd(u * 4 + g, 704) * 44000)
FROM generate_series(1, 10000) AS cu
CROSS JOIN LATERAL (SELECT 1 + floor(pg_temp.rnd(cu, 700) * 100000)::bigint AS u) uu
CROSS JOIN LATERAL generate_series(1, 1 + floor(pg_temp.rnd(cu, 701) * 4)::int) AS g
JOIN products p ON p.id = 1 + floor(200000 * power(pg_temp.rnd(u * 4 + g, 702), 2))::bigint
JOIN users us ON us.id = u
WHERE p.status = 'active' AND us.is_active
ORDER BY u, p.id
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 12. Promotions: fixed, non-overlapping campaign windows; each active
--     product joins each campaign with some probability. The last window is
--     open-ended (still running "today").
-- -----------------------------------------------------------------------------
INSERT INTO promotions (product_id, discount_pct, active_during, created_at)
SELECT
    p.id,
    pg_temp.pick('{5,10,10,15,20,20,25,30,40,50}'::numeric[], pg_temp.rnd(p.id * 10 + w.n, 802)),
    w.during,
    lower(w.during) - interval '7 days'
FROM products p
CROSS JOIN (VALUES
    (1, tstzrange('2023-11-20 00:00+00', '2023-11-28 00:00+00'), 0.020),
    (2, tstzrange('2024-07-01 00:00+00', '2024-07-15 00:00+00'), 0.010),
    (3, tstzrange('2024-11-25 00:00+00', '2024-12-03 00:00+00'), 0.025),
    (4, tstzrange('2025-03-01 00:00+00', '2025-03-15 00:00+00'), 0.010),
    (5, tstzrange('2025-07-01 00:00+00', '2025-07-15 00:00+00'), 0.010),
    (6, tstzrange('2025-11-24 00:00+00', '2025-12-02 00:00+00'), 0.030),
    (7, tstzrange('2025-12-15 00:00+00', NULL),                  0.010)
) AS w (n, during, prob)
WHERE p.status = 'active'
  AND p.created_at < lower(w.during)
  AND pg_temp.rnd(p.id * 10 + w.n, 801) < w.prob
ORDER BY lower(w.during), p.id;

-- -----------------------------------------------------------------------------
-- 13. Inventory: reserved = units in pending/paid orders (stock is reserved at
--     checkout and consumed at shipping); ~6% of products are out of stock.
-- -----------------------------------------------------------------------------
INSERT INTO inventory (product_id, quantity, reserved, updated_at)
SELECT
    p.id,
    greatest(
        CASE WHEN pg_temp.rnd(p.id, 901) < 0.06 OR p.status = 'archived' THEN 0
             ELSE 1 + floor(300 * power(pg_temp.rnd(p.id, 902), 2))::int END,
        coalesce(r.units, 0)),
    coalesce(r.units, 0),
    greatest(p.updated_at, timestamptz '2025-12-31 00:00+00' - interval '1 hour' * floor(pg_temp.rnd(p.id, 903) * 2000))
FROM products p
LEFT JOIN (
    SELECT i.product_id, sum(i.quantity)::int AS units
    FROM order_items i
    JOIN orders o ON o.id = i.order_id
    WHERE o.status IN ('pending', 'paid')
    GROUP BY i.product_id
) r ON r.product_id = p.id
ORDER BY p.id;

COMMIT;

RESET session_replication_role;

-- The materialized view still holds the small dataset's numbers.
REFRESH MATERIALIZED VIEW seller_sales_daily;

-- VACUUM sets hint bits and the visibility map (needed for index-only scans
-- in module 11) and ANALYZE gathers planner statistics. Both are also done
-- by autovacuum eventually, but "eventually" makes lessons non-reproducible.
-- (VACUUM cannot run inside a transaction block, hence after COMMIT.)
VACUUM (ANALYZE) users, sellers, categories, products, inventory, orders, order_items,
                 addresses, payments, reviews, cart_items, product_price_history,
                 promotions, audit_log, jobs, seller_sales_daily;

RESET work_mem;
RESET maintenance_work_mem;
RESET synchronous_commit;
