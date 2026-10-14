#!/usr/bin/env python3
"""
Generator for the small, hand-readable Bazaar dataset.

Writes three static SQL files (plain INSERT/UPDATE statements, no logic):

    project/db/0301_seed_small.sql     users, sellers, categories, products,
                                       inventory, orders, order_items
    project/db/0702_seed_modeling.sql  addresses, orders.shipping_address_id,
                                       payments, reviews, cart_items,
                                       product_price_history
    project/db/0802_seed_types.sql     products.attributes / tags, promotions

The output is fully deterministic (fixed random seed, no dependency on the
current date), so re-running this script reproduces the committed files
byte-for-byte. Edit the hand-written tables below, re-run, and commit both the
script and the regenerated SQL.

    python3 project/db/tools/gen_small_seed.py
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

SEED = 20240101
rng = random.Random(SEED)

DB_DIR = Path(__file__).resolve().parent.parent
UTC = timezone.utc
DATA_START = datetime(2024, 1, 1, tzinfo=UTC)
DATA_END = datetime(2025, 6, 30, 23, 59, tzinfo=UTC)  # "today" of the small dataset


def ts(d: datetime) -> str:
    return "'" + d.strftime("%Y-%m-%d %H:%M:%S") + "+00'"


def q(s: str | None) -> str:
    if s is None:
        return "NULL"
    return "'" + s.replace("'", "''") + "'"


def money(x: Decimal) -> str:
    return f"{x:.2f}"


def D(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"))


# =============================================================================
# Hand-written reference data
# =============================================================================

# (id, full_name, email, country, is_active)
USERS = [
    (1, "Alice Johnson", "alice.johnson@example.com", "US", True),
    (2, "Benjamin Clarke", "ben.clarke@example.co.uk", "GB", True),
    (3, "Giulia Rossi", "giulia.rossi@example.it", "IT", True),
    (4, "Lukas Becker", "lukas.becker@example.de", "DE", True),
    (5, "Chloé Tremblay", "chloe.tremblay@example.ca", "CA", True),
    (6, "Haruto Sato", "haruto.sato@example.jp", "JP", True),
    (7, "Priya Patel", "priya.patel@example.com", "US", True),
    (8, "Marcus Lee", "marcus.lee@example.com", "US", True),
    (9, "Sofía Hernández", "sofia.hernandez@example.es", "ES", False),
    (10, "Noah Williams", "noah.williams@example.com", "US", True),
    (11, "Emma Schmidt", "emma.schmidt@example.de", "DE", True),
    (12, "Liam O'Connor", "liam.oconnor@example.ie", "IE", True),
    (13, "Olivia Brown", "olivia.brown@example.co.uk", "GB", True),
    (14, "Mateo García", "mateo.garcia@example.es", "ES", True),
    (15, "Ava Nguyen", "ava.nguyen@example.com", "US", True),
    (16, "Lucas Martin", "lucas.martin@example.fr", "FR", True),
    (17, "Zoë Martin", "Zoe.Martin@Example.COM", "FR", True),
    (18, "Ethan Kim", "ethan.kim+bazaar@example.com", "US", True),
    (19, "Mia Rossi", "mia.rossi@example.it", "IT", True),
    (20, "Oliver Smith", "oliver.smith@example.co.uk", "GB", True),
    (21, "Isabella Silva", "isabella.silva@example.pt", "PT", True),
    (22, "James Anderson", "james.anderson@example.com", "US", False),
    (23, "Charlotte Dubois", "charlotte.dubois@example.fr", "FR", True),
    (24, "Henry Wilson", "henry.wilson@example.com", "US", True),
    (25, "Amelia Fischer", "amelia.fischer@example.at", "AT", True),
    (26, "Daniel Novák", "daniel.novak@example.cz", "CZ", True),
    (27, "Harper Johnson", "harper.johnson@example.com", "US", True),
    (28, "Leo Jensen", "leo.jensen@example.dk", "DK", False),
    (29, "Nora Berg", "nora.berg@example.no", "NO", True),
    (30, "Samuel Okafor", "samuel.okafor@example.ng", "NG", True),
]
NEVER_ORDERED = {6, 7, 15, 24, 28, 29, 30}
HEAVY_BUYERS = {8: 6, 11: 5, 13: 3, 20: 3}  # weight multipliers

# (id, user_id, shop_name, country, created_at)
SELLERS = [
    (1, 1, "TechTrove", "US", datetime(2023, 3, 5, 10, 0, tzinfo=UTC)),
    (2, 2, "Bookworm Corner", "GB", datetime(2023, 3, 20, 9, 30, tzinfo=UTC)),
    (3, 3, "Casa Cucina", "IT", datetime(2023, 4, 12, 14, 0, tzinfo=UTC)),
    (4, 4, "Threadbare Co.", "DE", datetime(2023, 5, 2, 8, 15, tzinfo=UTC)),
    (5, 5, "Maple & Pine", "CA", datetime(2023, 6, 1, 16, 45, tzinfo=UTC)),
    (6, 6, "Sakura Gadgets", "JP", datetime(2025, 5, 20, 3, 0, tzinfo=UTC)),
]

# (id, parent_id, name, slug)
CATEGORIES = [
    (1, None, "Electronics", "electronics"),
    (2, 1, "Computers", "computers"),
    (3, 2, "Laptops", "laptops"),
    (4, 2, "Monitors", "monitors"),
    (5, 1, "Audio", "audio"),
    (6, 5, "Headphones", "headphones"),
    (7, 5, "Speakers", "speakers"),
    (8, None, "Home & Garden", "home-garden"),
    (9, 8, "Kitchen", "kitchen"),
    (10, 9, "Cookware", "cookware"),
    (11, 9, "Cutlery", "cutlery"),
    (12, 8, "Garden", "garden"),
    (13, 12, "Planters", "planters"),
    (14, None, "Books", "books"),
    (15, 14, "Fiction", "fiction"),
    (16, 15, "Science Fiction", "science-fiction"),
    (17, 15, "Mystery", "mystery"),
    (18, 14, "Non-Fiction", "non-fiction"),
    (19, 18, "Cooking", "cooking"),
    (20, 18, "History", "history"),
    (21, None, "Clothing", "clothing"),
    (22, 21, "T-Shirts", "t-shirts"),
    (23, 21, "Hoodies", "hoodies"),
]


@dataclass
class Product:
    id: int
    seller_id: int
    category_id: int
    sku: str
    title: str
    description: str | None
    price: Decimal  # CURRENT price
    status: str
    created_at: datetime
    stock: int
    # price changes: list of (valid_from, new_price). The price before the first
    # change is `initial_price`.
    initial_price: Decimal | None = None
    changes: list[tuple[datetime, Decimal]] = field(default_factory=list)
    archived_at: datetime | None = None

    def price_at(self, when: datetime) -> Decimal:
        p = self.initial_price if self.initial_price is not None else self.price
        for vf, np in self.changes:
            if when >= vf:
                p = np
        return p

    @property
    def updated_at(self) -> datetime:
        last = self.created_at
        if self.changes:
            last = max(last, self.changes[-1][0])
        if self.archived_at:
            last = max(last, self.archived_at)
        return last


def dt(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


# id, seller, category, sku, title, description, price, status, created, stock
P = [
    # --- seller 1: TechTrove (electronics) -----------------------------------
    (1, 1, 3, "TT-LAP-001", "AeroBook 13 Laptop", "Ultralight 13-inch laptop with all-day battery and a backlit keyboard.", "999.00", "active", dt(2023, 3, 10), 25),
    (2, 1, 3, "TT-LAP-002", "AeroBook 15 Pro Laptop", "15-inch performance laptop for creators, with a colour-accurate display.", "1499.00", "active", dt(2023, 3, 10), 12),
    (3, 1, 3, "TT-LAP-003", "Titan G17 Gaming Laptop", "17-inch gaming laptop with a 240 Hz screen and a dedicated graphics card.", "2199.99", "active", dt(2023, 4, 2), 6),
    (4, 1, 3, "TT-LAP-004", "StudyMate 11 Chromebook", "Rugged and affordable laptop for students.", "249.00", "active", dt(2023, 5, 15), 40),
    (5, 1, 3, "TT-LAP-005", "AeroBook 13 Laptop (2023 model)", "Previous generation AeroBook. Discontinued.", "799.00", "archived", dt(2023, 3, 6), 0),
    (6, 1, 4, "TT-MON-001", 'ClearView 24" 1080p Monitor', "Everyday 24-inch IPS monitor with thin bezels.", "159.99", "active", dt(2023, 3, 12), 30),
    (7, 1, 4, "TT-MON-002", 'ClearView 27" 4K Monitor', "Sharp 27-inch 4K monitor with USB-C power delivery.", "379.00", "active", dt(2023, 3, 12), 15),
    (8, 1, 4, "TT-MON-003", 'UltraWide 34" Curved Monitor', "Immersive curved ultrawide display for productivity and games.", "549.00", "active", dt(2023, 6, 1), 8),
    (9, 1, 4, "TT-MON-004", 'PortaScreen 15.6" USB-C Portable Monitor', None, "189.00", "active", dt(2024, 2, 20), 20),
    (10, 1, 6, "TT-AUD-001", "QuietWave ANC Headphones", "Wireless over-ear headphones with active noise cancelling.", "229.00", "active", dt(2023, 3, 15), 18),
    (11, 1, 6, "TT-AUD-002", "QuietWave Buds", "True wireless earbuds with noise cancelling and a pocketable case.", "129.00", "active", dt(2023, 3, 15), 35),
    (12, 1, 6, "TT-AUD-003", "StudioPro Monitor Headphones", "Closed-back studio headphones with a flat frequency response.", "149.00", "active", dt(2023, 7, 1), 10),
    (13, 1, 6, "TT-AUD-004", "KidSafe Volume-Limited Headphones", "Colourful headphones that cap volume at 85 dB.", "29.99", "active", dt(2023, 8, 20), 0),
    (14, 1, 6, "TT-AUD-005", "BassBoom Over-Ear Headphones", None, "89.00", "draft", dt(2025, 6, 2), 0),
    (15, 1, 3, "TT-LAP-006", "Titan G15 Gaming Laptop", "15-inch sibling of the Titan G17.", "1799.00", "active", dt(2024, 2, 1), 4),
    (16, 1, 3, "TT-LAP-007", 'Laptop Sleeve 13"', "Padded neoprene sleeve for 13-inch laptops.", "19.99", "active", dt(2023, 9, 1), 60),
    (17, 1, 2, "TT-CMP-001", "USB-C Docking Station", "One cable for power, two displays, Ethernet and USB.", "129.00", "active", dt(2023, 9, 1), 14),
    (18, 1, 2, "TT-CMP-002", "Wireless Ergonomic Mouse", "Vertical mouse that keeps your wrist in a natural position.", "39.99", "active", dt(2023, 9, 1), 50),
    # --- seller 2: Bookworm Corner (books) ------------------------------------
    (19, 2, 16, "BW-SF-001", "The Long Orbit", "A generation ship, a mutiny, and a 300-year-old secret.", "14.99", "active", dt(2023, 3, 25), 40),
    (20, 2, 16, "BW-SF-002", "Children of the Red Dust", "Colonists on Mars discover they are not the first.", "12.50", "active", dt(2023, 3, 25), 30),
    (21, 2, 16, "BW-SF-003", "Starlight Protocol", "A rogue AI negotiates peace between two empires.", "18.00", "active", dt(2023, 5, 1), 22),
    (22, 2, 16, "BW-SF-004", "Echoes of Andromeda", None, "9.99", "active", dt(2023, 5, 1), 15),
    (23, 2, 16, "BW-SF-005", "The Clockwork Nebula", "Steampunk space opera. Out of print.", "16.00", "archived", dt(2023, 3, 25), 0),
    (24, 2, 17, "BW-MY-001", "Murder at Willow Lane", "A retired detective, a village fete, and a poisoned scone.", "11.99", "active", dt(2023, 4, 1), 25),
    (25, 2, 17, "BW-MY-002", "The Lighthouse Keeper's Secret", "Storms, smugglers and a body on the rocks.", "13.49", "active", dt(2023, 4, 1), 0),
    (26, 2, 17, "BW-MY-003", "A Death in Dorset", "An inspector returns to her home town.", "10.00", "active", dt(2023, 6, 10), 18),
    (27, 2, 17, "BW-MY-004", "The Silent Witness", "A courtroom mystery where the key witness cannot speak.", "12.00", "active", dt(2024, 1, 15), 20),
    (28, 2, 17, "BW-MY-005", "Midnight on the Orient Line", None, "15.00", "draft", dt(2025, 5, 30), 0),
    (29, 2, 19, "BW-CK-001", "Salt, Fat, Flame", "The four pillars of great home cooking, explained.", "29.99", "active", dt(2023, 4, 5), 14),
    (30, 2, 19, "BW-CK-002", "The Weeknight Pasta Book", "Fifty pasta dinners in thirty minutes or less.", "22.00", "active", dt(2023, 4, 5), 16),
    (31, 2, 19, "BW-CK-003", "Ferment Everything", "Kimchi, kombucha, sourdough and beyond.", "24.50", "active", dt(2023, 9, 9), 9),
    (32, 2, 19, "BW-CK-004", "Baking with Grandma", None, "19.95", "active", dt(2024, 3, 3), 11),
    (33, 2, 15, "BW-FIC-001", "The Complete Sci-Fi Box Set", "Five bestselling science fiction novels in one slipcase.", "79.00", "active", dt(2023, 10, 1), 5),
    # --- seller 3: Casa Cucina (kitchen) --------------------------------------
    (34, 3, 10, "CC-CW-001", "Cast Iron Skillet 26cm", "Pre-seasoned cast iron skillet, oven safe.", "45.00", "active", dt(2023, 4, 15), 20),
    (35, 3, 10, "CC-CW-002", "Stainless Steel Saucepan 2L", "Tri-ply saucepan with glass lid.", "39.00", "active", dt(2023, 4, 15), 18),
    (36, 3, 10, "CC-CW-003", "Non-Stick Frying Pan 28cm", "PFOA-free non-stick coating, induction compatible.", "34.99", "active", dt(2023, 4, 20), 26),
    (37, 3, 10, "CC-CW-004", "Dutch Oven 5.5L Enamel", "Enamelled cast iron casserole for braises and bread.", "119.00", "active", dt(2023, 5, 1), 7),
    (38, 3, 10, "CC-CW-005", "Copper Sauté Pan", "Hand-hammered copper pan with a tin lining.", "189.00", "archived", dt(2023, 5, 1), 0),
    (39, 3, 10, "CC-CW-006", "Pasta Pot with Strainer 6L", None, "54.00", "active", dt(2023, 10, 10), 10),
    (40, 3, 11, "CC-CU-001", "Chef's Knife 20cm", "Forged German steel chef's knife.", "89.00", "active", dt(2023, 4, 15), 15),
    (41, 3, 11, "CC-CU-002", "Santoku Knife 18cm", "Japanese-style all-purpose knife with a granton edge.", "79.00", "active", dt(2023, 4, 15), 12),
    (42, 3, 11, "CC-CU-003", "Paring Knife Set (3 pcs)", "Three small knives for peeling and detail work.", "29.00", "active", dt(2023, 6, 1), 30),
    (43, 3, 11, "CC-CU-004", "Bread Knife 23cm", "Serrated knife that glides through crusty loaves.", "35.00", "active", dt(2023, 6, 1), 14),
    (44, 3, 11, "CC-CU-005", "Bamboo Cutting Board", "Large reversible board with a juice groove.", "24.00", "active", dt(2023, 6, 1), 40),
    (45, 3, 11, "CC-CU-006", "Knife Sharpening Stone 1000/6000", "Dual-grit whetstone with a non-slip base.", "32.00", "active", dt(2024, 4, 1), 22),
    (46, 3, 9, "CC-KI-001", "Espresso Moka Pot 6-cup", "Classic stovetop espresso maker.", "42.00", "active", dt(2023, 7, 7), 16),
    # --- seller 4: Threadbare Co. (clothing) ----------------------------------
    (47, 4, 22, "TB-TS-001", "Organic Cotton Tee - White", "Heavyweight organic cotton crew neck.", "19.00", "active", dt(2023, 5, 5), 80),
    (48, 4, 22, "TB-TS-002", "Organic Cotton Tee - Black", "Heavyweight organic cotton crew neck.", "19.00", "active", dt(2023, 5, 5), 75),
    (49, 4, 22, "TB-TS-003", "Vintage Logo Tee", "Garment-dyed tee with a faded retro print.", "24.00", "active", dt(2023, 6, 1), 40),
    (50, 4, 22, "TB-TS-004", "Linen Summer Shirt", "Breathable linen shirt with a relaxed fit.", "39.00", "active", dt(2024, 4, 15), 25),
    (51, 4, 22, "TB-TS-005", "Graphic Tee 'Ctrl+Alt+Del'", "For people who have tried turning it off and on again.", "22.00", "active", dt(2023, 8, 1), 33),
    (52, 4, 23, "TB-HD-001", "Classic Zip Hoodie - Grey", "Brushed fleece zip hoodie.", "49.00", "active", dt(2023, 5, 5), 30),
    (53, 4, 23, "TB-HD-002", "Heavyweight Pullover Hoodie", "450 gsm cotton, built to last.", "59.00", "active", dt(2023, 9, 15), 20),
    (54, 4, 23, "TB-HD-003", "Kids' Rainbow Hoodie", "Soft hoodie with a rainbow print, ages 4-12.", "34.00", "active", dt(2023, 10, 1), 15),
    (55, 4, 23, "TB-HD-004", "Fleece-Lined Winter Hoodie", None, "69.00", "draft", dt(2025, 6, 10), 0),
    (56, 4, 23, "TB-HD-005", "Merino Wool Hoodie", "Fine merino hoodie, temperature regulating.", "119.00", "active", dt(2024, 9, 1), 8),
    # --- seller 5: Maple & Pine (garden) -- has products, but NO sales -------
    (57, 5, 13, "MP-PL-001", "Terracotta Planter 20cm", "Classic unglazed terracotta pot with saucer.", "18.00", "active", dt(2023, 6, 5), 30),
    (58, 5, 13, "MP-PL-002", "Self-Watering Planter", "Planter with a hidden reservoir for busy gardeners.", "29.00", "active", dt(2023, 6, 5), 12),
    (59, 5, 13, "MP-PL-003", "Hanging Macramé Planter", "Handmade cotton hanger.", "22.00", "draft", dt(2023, 6, 5), 0),
    (60, 5, 12, "MP-GD-001", "Cedar Raised Garden Bed", None, "149.00", "active", dt(2023, 7, 1), 3),
    (61, 5, 13, "MP-PL-004", "Ceramic Herb Pot Trio", "Three glazed pots for windowsill herbs.", "35.00", "archived", dt(2023, 6, 5), 0),
    # --- seller 4 again: a free item (price 0.00 is allowed) -----------------
    (62, 4, 21, "TB-ST-001", "Bazaar Sticker Pack", "Five vinyl stickers. Free with any order.", "0.00", "active", dt(2023, 5, 5), 500),
]

PRODUCTS: dict[int, Product] = {}
for row in P:
    pid, sid, cid, sku, title, desc, price, status, created, stock = row
    PRODUCTS[pid] = Product(pid, sid, cid, sku, title, desc, D(price), status, created, stock)

# Price changes (product_id -> (initial_price, [(valid_from, new_price), ...])).
# The LAST new_price must equal the product's current price.
PRICE_CHANGES = {
    1: ("1099.00", [(dt(2024, 11, 25, 0), "999.00")]),
    7: ("399.00", [(dt(2024, 9, 1, 0), "379.00")]),
    10: ("249.00", [(dt(2025, 1, 15, 0), "229.00")]),
    19: ("12.99", [(dt(2025, 2, 1, 0), "14.99")]),
    29: ("32.99", [(dt(2024, 12, 1, 0), "29.99")]),
    34: ("39.00", [(dt(2024, 7, 1, 0), "45.00")]),
    37: ("99.00", [(dt(2024, 6, 1, 0), "109.00"), (dt(2025, 3, 1, 0), "119.00")]),
    52: ("45.00", [(dt(2025, 1, 1, 0), "49.00")]),
}
for pid, (init, changes) in PRICE_CHANGES.items():
    p = PRODUCTS[pid]
    p.initial_price = D(init)
    p.changes = [(vf, D(np)) for vf, np in changes]
    assert p.changes[-1][1] == p.price, pid

# Archived products stop being orderable at archived_at.
PRODUCTS[5].archived_at = dt(2024, 5, 1)
PRODUCTS[23].archived_at = dt(2024, 9, 1)
PRODUCTS[38].archived_at = dt(2024, 8, 15)
PRODUCTS[61].archived_at = dt(2024, 3, 1)

# Products that must never appear in an order.
NEVER_ORDER_PRODUCTS = {15} | {p.id for p in PRODUCTS.values() if p.seller_id == 5 or p.status == "draft"}

# =============================================================================
# Users & sellers timestamps
# =============================================================================
user_created: dict[int, datetime] = {}
for uid, *_ in USERS:
    user_created[uid] = dt(2023, 3, 1, 9) + timedelta(days=(uid - 1) * 21, hours=rng.randint(0, 10), minutes=rng.randint(0, 59))
    if uid <= 6:  # shop owners were among the first to sign up
        user_created[uid] = dt(2023, 3, uid, 9) - timedelta(days=0, hours=rng.randint(0, 5))
user_created[30] = dt(2025, 6, 25, 18, 20)
for sid, uid, _, _, sc in SELLERS:
    assert user_created[uid] < sc, (uid, sid)

# =============================================================================
# Orders
# =============================================================================


@dataclass
class Order:
    id: int
    user_id: int
    seller_id: int
    status: str
    created_at: datetime
    paid_at: datetime | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    items: list[tuple[int, int, Decimal]] = field(default_factory=list)  # (product, qty, unit_price)

    @property
    def total(self) -> Decimal:
        return sum((D(q * up) for _, q, up in self.items), Decimal("0.00"))


N_ORDERS = 120
buyers = [u for u, *_ in USERS if u not in NEVER_ORDERED]
buyer_weights = [HEAVY_BUYERS.get(u, 1) for u in buyers]
seller_weights = {1: 33, 2: 28, 3: 20, 4: 19}

span = (DATA_END - DATA_START).total_seconds()
# Most orders are spread uniformly; 12 are squeezed into the last two weeks so
# that "open" statuses (pending / paid / shipped) are well represented.
recent_start = DATA_END - timedelta(days=14)
created_times = sorted(
    [DATA_START + timedelta(seconds=rng.uniform(0, span)) for _ in range(N_ORDERS - 12)]
    + [recent_start + timedelta(seconds=rng.uniform(0, 14 * 86400 - 3600)) for _ in range(12)]
)
# Round to whole seconds.
created_times = [t.replace(microsecond=0) for t in created_times]

orders: list[Order] = []
for i, created in enumerate(created_times, start=1):
    # Pick a seller first, then a buyer that existed and is not the shop owner.
    for _attempt in range(200):
        sid = rng.choices(list(seller_weights), weights=list(seller_weights.values()))[0]
        uid = rng.choices(buyers, weights=buyer_weights)[0]
        owner = next(s[1] for s in SELLERS if s[0] == sid)
        if uid == owner or user_created[uid] >= created:
            continue
        orderable = [
            p for p in PRODUCTS.values()
            if p.seller_id == sid
            and p.id not in NEVER_ORDER_PRODUCTS
            and p.created_at < created
            and (p.archived_at is None or created < p.archived_at)
        ]
        if orderable:
            break
    else:
        raise RuntimeError("could not place order")
    n_items = min(len(orderable), rng.choices([1, 2, 3, 4], weights=[45, 30, 17, 8])[0])
    chosen = rng.sample(orderable, n_items)
    o = Order(i, uid, sid, "pending", created)
    for p in sorted(chosen, key=lambda p: p.id):
        qty = rng.choices([1, 2, 3], weights=[75, 18, 7])[0]
        if p.id == 62:
            qty = 1
        o.items.append((p.id, qty, p.price_at(created)))
    orders.append(o)

# Status assignment, by age relative to DATA_END.
for o in orders:
    age_days = (DATA_END - o.created_at).total_seconds() / 86400
    r = rng.random()
    if r < 0.07:
        status = "cancelled"
    elif r < 0.12 and age_days > 10:
        status = "refunded"
    elif age_days < 2:
        status = rng.choice(["pending", "pending", "paid"])
    elif age_days < 6:
        status = rng.choice(["paid", "shipped", "pending"])
    elif age_days < 12:
        status = rng.choice(["shipped", "shipped", "delivered"])
    else:
        status = "delivered"
    o.status = status

    def mins(a, b):
        return timedelta(minutes=rng.randint(a, b))

    if status in ("paid", "shipped", "delivered", "refunded"):
        o.paid_at = o.created_at + mins(2, 180)
    if status in ("shipped", "delivered") or (status == "refunded" and rng.random() < 0.6):
        o.shipped_at = o.paid_at + timedelta(days=rng.randint(1, 3), hours=rng.randint(0, 8))
    if status == "delivered" or (status == "refunded" and o.shipped_at):
        o.delivered_at = o.shipped_at + timedelta(days=rng.randint(2, 6), hours=rng.randint(0, 8))
    # Never let a timestamp run past the dataset's "today": downgrade instead.
    if o.delivered_at and o.delivered_at > DATA_END:
        o.delivered_at = None
        if o.status == "delivered":
            o.status = "shipped"
        else:  # refunded: returned before delivery completed
            o.shipped_at = None
    if o.shipped_at and o.shipped_at > DATA_END:
        o.shipped_at = None
        if o.status == "shipped":
            o.status = "paid"
    assert o.paid_at is None or o.paid_at <= DATA_END

# Make sure every status is represented (deterministic tweaks if not).
statuses = {o.status for o in orders}
assert statuses == {"pending", "paid", "shipped", "delivered", "cancelled", "refunded"}, statuses

# Inventory: reserved = units in pending/paid orders (not yet shipped).
reserved = defaultdict(int)
for o in orders:
    if o.status in ("pending", "paid"):
        for pid, qty, _ in o.items:
            reserved[pid] += qty
for pid, p in PRODUCTS.items():
    if reserved[pid] > p.stock:
        p.stock = reserved[pid]
# One reserved product is fully booked: available = 0 while quantity > 0.
fully_booked = None
for pid in sorted(reserved):
    if reserved[pid] > 0 and PRODUCTS[pid].status == "active" and pid not in (62,):
        fully_booked = pid
        PRODUCTS[pid].stock = reserved[pid]
        break

# =============================================================================
# 0301_seed_small.sql
# =============================================================================


def header(name: str, module: str, body: str) -> str:
    return (
        "-- =============================================================================\n"
        f"-- {name}  --  {module}\n"
        "-- -----------------------------------------------------------------------------\n"
        + "".join(f"-- {line}".rstrip() + "\n" for line in body.strip("\n").split("\n"))
        + "--\n"
        "-- GENERATED FILE: do not edit by hand. Edit project/db/tools/gen_small_seed.py\n"
        "-- and re-run it (output is deterministic).\n"
        "-- =============================================================================\n\n"
        "SET search_path = bazaar, public;\n\n"
    )


def values_block(table: str, cols: list[str], rows: list[list[str]], overriding: bool = True) -> str:
    ov = "\nOVERRIDING SYSTEM VALUE" if overriding else ""
    if overriding:
        values_block_ids(table, rows)
    out = f"INSERT INTO {table} ({', '.join(cols)}){ov}\nVALUES\n"
    out += ",\n".join("    (" + ", ".join(r) + ")" for r in rows)
    return out + ";\n\n"


MAX_IDS: dict[str, int] = {}


def values_block_ids(table: str, rows: list[list[str]]) -> None:
    MAX_IDS[table] = max(int(r[0]) for r in rows)


def setval(table: str) -> str:
    # ALTER ... RESTART is the identity-column way to move the sequence on.
    # (Equivalent: SELECT setval(pg_get_serial_sequence('t', 'id'), max(id)) FROM t;)
    return f"ALTER TABLE {table} ALTER COLUMN id RESTART WITH {MAX_IDS[table] + 1};\n"


never_products_sorted = sorted(set(PRODUCTS) - {pid for o in orders for pid, _, _ in o.items})
out = [header("0301_seed_small.sql", "Module 03: Reading and Writing Data", f"""
The small, hand-readable Bazaar dataset used by modules 03-07 (and still present,
extended, up to module 10). Every id is fixed (OVERRIDING SYSTEM VALUE) so
exercises can refer to "user 7" or "order 42"; afterwards each identity sequence
is moved past the highest id (ALTER TABLE ... RESTART) so new INSERTs keep working.

Deliberate features worth querying:
  * 30 users; inactive: 9, 22, 28. Never ordered anything:
    {', '.join(map(str, sorted(set(user_created) - {o.user_id for o in orders})))}.
    User 17 has a mixed-case email (Zoe.Martin@Example.COM); user 12 has an
    apostrophe in his name (Liam O'Connor); several names have accents.
  * 6 sellers (owned by users 1-6) in 6 countries. Seller 5 (Maple & Pine) has
    products but no sales; seller 6 (Sakura Gadgets) has no products at all.
  * 23 categories, 3 levels deep (Electronics > Computers > Laptops ...).
    Leaf categories with no products: 7 (Speakers), 20 (History).
    Some products sit in non-leaf categories (17, 18 in Computers; 33 in Fiction;
    46 in Kitchen; 60 in Garden; 62 in Clothing).
  * 62 products: drafts, archived and active; NULL descriptions; prices from
    0.00 (product 62, a free sticker pack) to 2199.99. Products never ordered:
    {', '.join(map(str, never_products_sorted))}
    (15 is an active, in-stock product of an active seller that nobody bought).
  * Inventory for every product; some out of stock (quantity 0); reserved =
    units in pending/paid orders. Product {fully_booked} is fully reserved (available 0).
  * 120 orders 2024-01-01..2025-06-30, ids in chronological order, every status
    represented; paid_at/shipped_at/delivered_at consistent with status.
  * order_items.unit_price is the price at order time: products
    {', '.join(map(str, sorted(PRICE_CHANGES)))} changed price, so older orders show the old price.
  * orders.total = sum(quantity * unit_price) exactly.
""")]

out.append("BEGIN;\n\n")

out.append("-- Users ---------------------------------------------------------------------\n")
out.append(values_block("users", ["id", "email", "full_name", "is_active", "created_at"], [
    [str(uid), q(email), q(name), "true" if active else "false", ts(user_created[uid])]
    for uid, name, email, _c, active in USERS
]))

out.append("-- Sellers (a seller is a user who opened a shop) ---------------------------\n")
out.append(values_block("sellers", ["id", "user_id", "shop_name", "country_code", "created_at"], [
    [str(sid), str(uid), q(name), q(cc), ts(sc)] for sid, uid, name, cc, sc in SELLERS
]))

out.append("-- Category tree: roots first so parent_id references already exist ---------\n")
out.append(values_block("categories", ["id", "parent_id", "name", "slug", "created_at"], [
    [str(cid), "NULL" if par is None else str(par), q(name), q(slug), ts(dt(2023, 3, 1, 8))]
    for cid, par, name, slug in CATEGORIES
]))

out.append("-- Products: price is the CURRENT price -------------------------------------\n")
out.append(values_block("products", ["id", "seller_id", "category_id", "sku", "title", "description",
                                     "price", "status", "created_at", "updated_at"], [
    [str(p.id), str(p.seller_id), str(p.category_id), q(p.sku), q(p.title), q(p.description),
     money(p.price), q(p.status), ts(p.created_at), ts(p.updated_at)]
    for p in PRODUCTS.values()
]))

out.append("-- Inventory: one row per product ------------------------------------------\n")
out.append(values_block("inventory", ["product_id", "quantity", "reserved", "updated_at"], [
    [str(p.id), str(p.stock), str(reserved[p.id]), ts(dt(2025, 6, 30, 23, 0))]
    for p in PRODUCTS.values()
], overriding=False))

out.append("-- Orders: ids are chronological ------------------------------------------\n")
out.append(values_block("orders", ["id", "user_id", "seller_id", "status", "total",
                                   "created_at", "paid_at", "shipped_at", "delivered_at"], [
    [str(o.id), str(o.user_id), str(o.seller_id), q(o.status), money(o.total), ts(o.created_at),
     ts(o.paid_at) if o.paid_at else "NULL",
     ts(o.shipped_at) if o.shipped_at else "NULL",
     ts(o.delivered_at) if o.delivered_at else "NULL"]
    for o in orders
]))

out.append("-- Order items ------------------------------------------------------------\n")
out.append(values_block("order_items", ["order_id", "product_id", "quantity", "unit_price"], [
    [str(o.id), str(pid), str(qty), money(up)] for o in orders for pid, qty, up in o.items
], overriding=False))

out.append("-- Move identity sequences past the explicit ids --------------------------\n")
for t in ("users", "sellers", "categories", "products", "orders"):
    out.append(setval(t))
out.append("\nCOMMIT;\n")
(DB_DIR / "0301_seed_small.sql").write_text("".join(out))

# =============================================================================
# 0702_seed_modeling.sql
# =============================================================================
CITIES = {
    "US": [("Portland", "97205"), ("Austin", "78701"), ("Chicago", "60607"), ("Boston", "02116")],
    "GB": [("London", "SW1A 1AA"), ("Manchester", "M1 1AE"), ("Bristol", "BS1 4DJ")],
    "IT": [("Milano", "20121"), ("Bologna", "40121")],
    "DE": [("Berlin", "10115"), ("München", "80331"), ("Hamburg", "20095")],
    "CA": [("Toronto", "M5V 2T6"), ("Montréal", "H2X 1Y4")],
    "JP": [("Tokyo", "100-0001")],
    "ES": [("Madrid", "28013"), ("Sevilla", "41001")],
    "IE": [("Dublin", "D02 X285")],
    "FR": [("Paris", "75003"), ("Lyon", "69002")],
    "PT": [("Lisboa", "1100-148")],
    "AT": [("Wien", "1010")],
    "CZ": [("Praha", "110 00")],
    "DK": [("København", "1050")],
    "NO": [("Oslo", "0150")],
    "NG": [("Lagos", "101233")],
}
STREETS = ["Market St", "Oak Avenue", "High Street", "Via Roma", "Hauptstraße", "Rue de Rivoli",
           "Elm Road", "Calle Mayor", "Mill Lane", "Harbour Road", "Station Road", "Lindenweg"]

user_info = {u[0]: u for u in USERS}
NO_ADDRESS = {24, 28, 29, 30}       # users without any address
TWO_ADDRESSES = {1, 8, 11, 13, 17, 20}
NO_DEFAULT = {7}                    # user 7 has an address but no default

addresses = []  # (id, user_id, label, recipient, line1, line2, city, postal, cc, is_default, created)
aid = 0
user_addresses: dict[int, list[int]] = defaultdict(list)
user_default: dict[int, int] = {}
for uid, name, _email, cc, _a in USERS:
    if uid in NO_ADDRESS:
        continue
    n = 2 if uid in TWO_ADDRESSES else 1
    for k in range(n):
        aid += 1
        city, postal = rng.choice(CITIES[cc])
        label = "Home" if k == 0 else "Work"
        line1 = f"{rng.randint(1, 250)} {rng.choice(STREETS)}"
        line2 = rng.choice([None, None, None, f"Apt {rng.randint(1, 40)}", f"Floor {rng.randint(1, 9)}"])
        if k == 1:
            line2 = "c/o Reception"
        recipient = name
        if uid == 13 and k == 1:
            recipient = "Olivia Brown (Acme Ltd)"
        is_default = (k == 0) and uid not in NO_DEFAULT
        created = user_created[uid] + timedelta(hours=1 + 24 * 30 * k)
        addresses.append((aid, uid, label, recipient, line1, line2, city, postal, cc, is_default, created))
        user_addresses[uid].append(aid)
        if is_default:
            user_default[uid] = aid

order_ship = {}
for o in orders:
    addrs = user_addresses[o.user_id]
    assert addrs, o.user_id
    if len(addrs) > 1 and rng.random() < 0.3:
        order_ship[o.id] = addrs[1]
    else:
        order_ship[o.id] = user_default.get(o.user_id, addrs[0])
# One very early order without a shipping address (data from before addresses
# existed), to keep NULL handling interesting.
order_ship[1] = None

payments = []  # (order_id, amount, provider, ref, status, created)
for o in orders:
    provider = rng.choice(["stripe", "stripe", "stripe", "paypal", "adyen"])

    def ref():
        return f"{provider[:2]}_{rng.getrandbits(40):010x}"

    if o.paid_at:
        if rng.random() < 0.15:  # a failed attempt first
            payments.append((o.id, o.total, provider, ref(), "failed", o.paid_at - timedelta(minutes=rng.randint(1, 5))))
        payments.append((o.id, o.total, provider, ref(), "succeeded", o.paid_at))
        if o.status == "refunded":
            when = (o.delivered_at or o.paid_at) + timedelta(days=rng.randint(1, 5))
            if when > DATA_END:
                when = DATA_END - timedelta(hours=1)
            payments.append((o.id, -o.total, provider, ref(), "succeeded", when))
    elif o.status == "pending":
        if rng.random() < 0.5:
            payments.append((o.id, o.total, provider, ref(), "pending", o.created_at + timedelta(minutes=1)))
    elif o.status == "cancelled":
        if rng.random() < 0.5:
            payments.append((o.id, o.total, provider, ref(), "failed", o.created_at + timedelta(minutes=2)))
payments.sort(key=lambda p: p[5])
# Free orders (total 0) cannot have a payment (amount <> 0): drop them.
payments = [p for p in payments if p[1] != 0]

REVIEW_TITLES = {
    5: ["Excellent", "Love it", "Exactly as described", "Five stars", "Would buy again"],
    4: ["Very good", "Solid purchase", "Happy with it", None],
    3: ["It's OK", "Average", None],
    2: ["Disappointing", "Not great"],
    1: ["Broke after a week", "Do not buy"],
}
REVIEW_BODIES = {
    5: ["Fast shipping and great quality.", "Better than I expected!", "Perfect gift.", None],
    4: ["Does the job well. Minor quibbles.", "Good value for the price.", None],
    3: ["Fine, but nothing special.", "Packaging was damaged, product OK."],
    2: ["Quality is not what the photos suggest.", "Smaller than expected."],
    1: ["Stopped working quickly; seller was helpful though.", "Arrived broken."],
}
reviews = []  # (product, user, rating, title, body, created)
seen = set()
for o in orders:
    if o.status != "delivered":
        continue
    for pid, _q, _u in o.items:
        if (pid, o.user_id) in seen or pid == 62:
            continue
        if rng.random() < 0.45:
            rating = rng.choices([1, 2, 3, 4, 5], weights=[5, 7, 13, 30, 45])[0]
            created = o.delivered_at + timedelta(days=rng.randint(0, 20), hours=rng.randint(0, 12))
            if created > DATA_END:
                created = DATA_END - timedelta(minutes=30)
            reviews.append((pid, o.user_id, rating, rng.choice(REVIEW_TITLES[rating]),
                            rng.choice(REVIEW_BODIES[rating]), created))
            seen.add((pid, o.user_id))
# Deliberate anomaly: user 7 never ordered anything but reviewed product 1.
reviews.append((1, 7, 1, "Overpriced", "Saw it in a shop. Way too expensive.", dt(2025, 2, 14, 20, 5)))
reviews.sort(key=lambda r: r[5])

cart = []
CART_USERS = {8: [(1, 1), (24, 2), (47, 3)], 11: [(36, 1)], 13: [(41, 1), (44, 1)], 17: [(52, 1), (62, 1)],
              21: [(7, 1)], 25: [(19, 1), (20, 1), (21, 1)], 15: [(3, 1)], 30: [(57, 2)]}
for uid, items in CART_USERS.items():
    for pid, qty in items:
        assert PRODUCTS[pid].status == "active"
        cart.append((uid, pid, qty, dt(2025, 6, 20, 10) + timedelta(hours=rng.randint(0, 240))))

out = [header("0702_seed_modeling.sql", "Module 07: Data Modeling", f"""
Data for the schema v2 tables added in 0701_schema_modeling.sql, consistent with
the small dataset of 0301_seed_small.sql.

  * addresses: users {', '.join(map(str, sorted(NO_ADDRESS)))} have none; users
    {', '.join(map(str, sorted(TWO_ADDRESSES)))} have two (Home = default, Work);
    user 7 has an address but no default one.
  * orders.shipping_address_id: the default address (sometimes the Work one);
    order 1 has NULL (placed before addresses were captured).
  * payments: one succeeded payment per paid order (amount = total), some
    failed attempts first, a negative refund payment for refunded orders,
    pending payments for some pending orders, failed ones for some cancelled.
  * reviews: written after delivery by buyers of the product, EXCEPT one
    deliberate anomaly: user 7 (never ordered) reviewed product 1.
  * cart_items: a few open carts (users {', '.join(map(str, CART_USERS))}).
  * product_price_history: one row per product at created_at with the initial
    price plus one row per price change; consistent with order_items.unit_price.
""")]
out.append("BEGIN;\n\n")
out.append(values_block("addresses", ["id", "user_id", "label", "recipient", "line1", "line2", "city",
                                      "postal_code", "country_code", "is_default", "created_at"], [
    [str(a[0]), str(a[1]), q(a[2]), q(a[3]), q(a[4]), q(a[5]), q(a[6]), q(a[7]), q(a[8]),
     "true" if a[9] else "false", ts(a[10])] for a in addresses
]))
out.append(setval("addresses"))
out.append("\n-- Shipping address per order (UPDATE ... FROM (VALUES ...) is a handy bulk-update idiom)\n")
out.append("UPDATE orders AS o\nSET shipping_address_id = v.address_id\nFROM (VALUES\n")
out.append(",\n".join(f"    ({oid}, {aid_})" for oid, aid_ in sorted(order_ship.items()) if aid_ is not None))
out.append("\n) AS v (order_id, address_id)\nWHERE o.id = v.order_id;\n\n")

out.append("-- Payments (negative amount = refund) --------------------------------------\n")
out.append(values_block("payments", ["id", "order_id", "amount", "provider", "provider_ref", "status", "created_at"], [
    [str(i), str(p[0]), money(p[1]), q(p[2]), q(p[3]), q(p[4]), ts(p[5])] for i, p in enumerate(payments, 1)
]))
out.append(setval("payments"))

out.append("\n-- Reviews --------------------------------------------------------------------\n")
out.append(values_block("reviews", ["id", "product_id", "user_id", "rating", "title", "body", "created_at"], [
    [str(i), str(r[0]), str(r[1]), str(r[2]), q(r[3]), q(r[4]), ts(r[5])] for i, r in enumerate(reviews, 1)
]))
out.append(setval("reviews"))

out.append("\n-- Open shopping carts --------------------------------------------------------\n")
out.append(values_block("cart_items", ["user_id", "product_id", "quantity", "added_at"], [
    [str(c[0]), str(c[1]), str(c[2]), ts(c[3])] for c in cart
], overriding=False))

out.append("-- Price history: initial price at created_at, then each change -----------\n")
ph_rows = []
for p in PRODUCTS.values():
    ph_rows.append([str(p.id), money(p.initial_price if p.initial_price is not None else p.price), ts(p.created_at)])
    for vf, np in p.changes:
        ph_rows.append([str(p.id), money(np), ts(vf)])
out.append(values_block("product_price_history", ["product_id", "price", "valid_from"], ph_rows, overriding=False))
out.append("COMMIT;\n")
(DB_DIR / "0702_seed_modeling.sql").write_text("".join(out))

# =============================================================================
# 0802_seed_types.sql
# =============================================================================
ATTRS = {
    1: ({"brand": "AeroTech", "ram_gb": 16, "cpu": "M-Core 8", "screen_in": 13.3, "storage": {"type": "ssd", "gb": 512}}, ["ultralight", "laptop", "bestseller"]),
    2: ({"brand": "AeroTech", "ram_gb": 32, "cpu": "M-Core 12", "screen_in": 15.6, "storage": {"type": "ssd", "gb": 1024}}, ["laptop", "creator"]),
    3: ({"brand": "Titan", "ram_gb": 32, "cpu": "Ryzon 9", "screen_in": 17.3, "gpu": "RTX-class", "storage": {"type": "ssd", "gb": 2048}}, ["laptop", "gaming"]),
    4: ({"brand": "StudyMate", "ram_gb": 4, "cpu": "Celera N", "screen_in": 11.6, "storage": {"type": "emmc", "gb": 64}}, ["laptop", "student", "budget"]),
    5: ({"brand": "AeroTech", "ram_gb": 8, "cpu": "M-Core 6", "screen_in": 13.3}, ["laptop", "discontinued"]),
    6: ({"brand": "ClearView", "size_in": 24, "resolution": "1920x1080", "panel": "IPS", "refresh_hz": 75}, ["monitor"]),
    7: ({"brand": "ClearView", "size_in": 27, "resolution": "3840x2160", "panel": "IPS", "refresh_hz": 60, "ports": ["HDMI", "DisplayPort", "USB-C"]}, ["monitor", "4k", "usb-c"]),
    8: ({"brand": "UltraWide", "size_in": 34, "resolution": "3440x1440", "panel": "VA", "refresh_hz": 144, "curved": True}, ["monitor", "gaming"]),
    9: ({"brand": "PortaScreen", "size_in": 15.6, "resolution": "1920x1080", "panel": "IPS", "ports": ["USB-C"]}, ["monitor", "portable", "usb-c"]),
    10: ({"brand": "QuietWave", "wireless": True, "anc": True, "battery_h": 30, "colors": ["black", "silver"]}, ["headphones", "wireless", "anc", "bestseller"]),
    11: ({"brand": "QuietWave", "wireless": True, "anc": True, "battery_h": 8}, ["earbuds", "wireless", "anc"]),
    12: ({"brand": "StudioPro", "wireless": False, "impedance_ohm": 32}, ["headphones", "studio"]),
    13: ({"brand": "KidSafe", "wireless": False, "max_db": 85, "colors": ["pink", "blue", "green"]}, ["headphones", "kids"]),
    14: ({"brand": "BassBoom", "wireless": True}, ["headphones"]),
    15: ({"brand": "Titan", "ram_gb": 16, "cpu": "Ryzon 7", "screen_in": 15.6, "gpu": "RTX-class"}, ["laptop", "gaming"]),
    16: ({"material": "neoprene", "fits_in": 13, "colors": ["grey", "black"]}, ["accessory", "laptop"]),
    17: ({"ports": ["HDMI", "HDMI", "RJ45", "USB-A", "USB-A", "USB-C"], "power_w": 100}, ["accessory", "usb-c"]),
    18: ({"brand": "ErgoGrip", "wireless": True, "dpi": 1600}, ["accessory", "ergonomic"]),
}
BOOKS = {
    19: ("Mara Quinn", 412, "9780000000019", "en"), 20: ("Ade Okonkwo", 356, "9780000000026", "en"),
    21: ("Lena Vogt", 498, "9780000000033", "en"), 22: ("Tom Reyes", 289, "9780000000040", "en"),
    23: ("E. H. Blackwood", 520, "9780000000057", "en"), 24: ("Agatha Pembroke", 301, "9780000000064", "en"),
    25: ("Rosalind Hart", 334, "9780000000071", "en"), 26: ("Agatha Pembroke", 288, "9780000000088", "en"),
    27: ("Jonas Weber", 365, "9780000000095", "de"), 28: ("Agatha Pembroke", None, None, "en"),
    29: ("Nadia Farouk", 480, "9780000000101", "en"), 30: ("Marco Bellini", 224, "9780000000118", "it"),
    31: ("Sandor Katz-Smith", 512, "9780000000125", "en"), 32: ("Rose Whitfield", 196, "9780000000132", "en"),
}
for pid, (author, pages, isbn, lang) in BOOKS.items():
    a = {"author": author, "language": lang}
    if pages:
        a["pages"] = pages
    if isbn:
        a["isbn"] = isbn
    cat = PRODUCTS[pid].category_id
    tags = {16: ["book", "sci-fi"], 17: ["book", "mystery"], 19: ["book", "cooking"]}[cat]
    if pid == 25:
        tags = tags + ["bestseller"]
    ATTRS[pid] = (a, tags)
ATTRS[33] = ({"format": "box set", "volumes": 5, "language": "en",
              "contents": [{"title": "The Long Orbit", "author": "Mara Quinn"},
                           {"title": "Starlight Protocol", "author": "Lena Vogt"}]}, ["book", "sci-fi", "gift"])
for pid in range(34, 40):
    mat = {34: "cast iron", 35: "stainless steel", 36: "aluminium", 37: "enamelled cast iron", 38: "copper", 39: "stainless steel"}[pid]
    ATTRS[pid] = ({"material": mat, "induction": pid not in (38,), "dishwasher_safe": pid in (35, 39)}, ["cookware"] + (["cast-iron"] if "cast iron" in mat else []))
ATTRS[37][0]["capacity_l"] = 5.5
ATTRS[39][0]["capacity_l"] = 6
ATTRS[34][0]["diameter_cm"] = 26
for pid in range(40, 46):
    ATTRS[pid] = ({"material": "bamboo" if pid == 44 else ("stone" if pid == 45 else "stainless steel"),
                   "blade_cm": {40: 20, 41: 18, 42: 9, 43: 23}.get(pid)}, ["cutlery"] + (["knife"] if pid <= 43 else []))
    if ATTRS[pid][0]["blade_cm"] is None:
        del ATTRS[pid][0]["blade_cm"]
ATTRS[46] = ({"material": "aluminium", "cups": 6, "induction": False}, ["coffee", "kitchen"])
for pid in range(47, 57):
    is_hoodie = PRODUCTS[pid].category_id == 23
    color = {47: "white", 48: "black", 49: "navy", 50: "sand", 51: "black", 52: "grey", 53: "olive",
             54: "multicolor", 55: "charcoal", 56: "forest green"}[pid]
    sizes = ["4-6", "7-9", "10-12"] if pid == 54 else ["XS", "S", "M", "L", "XL"] + (["XXL"] if pid in (48, 53) else [])
    mat = {50: "linen", 56: "merino wool"}.get(pid, "organic cotton" if pid in (47, 48) else "cotton")
    ATTRS[pid] = ({"size": sizes, "color": color, "material": mat, "fit": "relaxed" if pid in (50, 53) else "regular"},
                  ["clothing", "hoodie" if is_hoodie else "tee"] + (["organic"] if mat == "organic cotton" else []) + (["kids"] if pid == 54 else []))
ATTRS[57] = ({"material": "terracotta", "diameter_cm": 20, "drainage_hole": True}, ["garden", "planter"])
ATTRS[58] = ({"material": "plastic", "diameter_cm": 25, "reservoir_ml": 800}, ["garden", "planter"])
ATTRS[59] = ({"material": "cotton", "handmade": True}, ["garden", "planter", "handmade"])
ATTRS[60] = ({"material": "cedar", "dimensions_cm": {"l": 120, "w": 60, "h": 30}}, ["garden", "outdoor"])
ATTRS[61] = ({"material": "ceramic", "pieces": 3}, ["garden", "planter", "discontinued"])
ATTRS[62] = ({}, ["freebie"])  # deliberately empty attributes
assert set(ATTRS) == set(PRODUCTS), set(PRODUCTS) - set(ATTRS)

PROMOS = [  # (product, pct, lower, upper or None)
    (1, "10.00", dt(2024, 11, 25, 0), dt(2024, 12, 2, 0)),
    (10, "15.00", dt(2024, 11, 25, 0), dt(2024, 12, 2, 0)),
    (10, "20.00", dt(2025, 6, 1, 0), dt(2025, 6, 15, 0)),
    (7, "5.00", dt(2025, 3, 1, 0), dt(2025, 4, 1, 0)),
    (34, "25.00", dt(2024, 7, 1, 0), dt(2024, 7, 8, 0)),
    (47, "30.00", dt(2025, 6, 20, 0), None),  # open-ended: still running "today"
    (48, "30.00", dt(2025, 6, 20, 0), None),
    (24, "50.00", dt(2025, 1, 1, 0), dt(2025, 1, 8, 0)),
    (37, "12.50", dt(2025, 5, 1, 0), dt(2025, 7, 1, 0)),
]

out = [header("0802_seed_types.sql", "Module 08: Beyond Scalars", """
Fills the columns/tables added by 0801_schema_types.sql for the small dataset:
  * products.attributes: category-dependent JSON documents. Laptops have
    brand/ram_gb/cpu/screen_in and a nested "storage" object; books have
    author/pages/isbn/language (product 28 lacks pages/isbn); clothing has a
    "size" ARRAY and a color; product 33 has an array of objects; product 62
    has an empty object {}.
  * products.tags: a few lowercase tags per product ('bestseller', 'gaming', ...).
  * promotions: time-boxed discounts as half-open tstzrange [from, to).
    Products 47 and 48 have an open-ended promotion (upper bound NULL = infinity)
    that is still running; product 10 has two non-overlapping promotions.
""")]
out.append("BEGIN;\n\n")
out.append("-- One UPDATE per product keeps the file easy to read and diff.\n")
for pid in sorted(ATTRS):
    a, tags = ATTRS[pid]
    arr = "{" + ",".join(tags) + "}"
    out.append(f"UPDATE products SET attributes = {q(json.dumps(a, ensure_ascii=False))}, tags = {q(arr)} WHERE id = {pid};\n")
out.append("\n-- Promotions: '[lower,upper)' ranges; an empty upper bound means 'no end'.\n")
out.append(values_block("promotions", ["id", "product_id", "discount_pct", "active_during", "created_at"], [
    [str(i), str(p[0]), p[1],
     q(f"[{p[2].strftime('%Y-%m-%d %H:%M:%S')}+00,{p[3].strftime('%Y-%m-%d %H:%M:%S') + '+00' if p[3] else ''})"),
     ts(p[2] - timedelta(days=3))]
    for i, p in enumerate(PROMOS, 1)
]))
out.append(setval("promotions"))
out.append("\nCOMMIT;\n")
(DB_DIR / "0802_seed_types.sql").write_text("".join(out))

# Summary for the maintainer.
from collections import Counter
print("orders by status:", dict(Counter(o.status for o in orders)))
print("order_items:", sum(len(o.items) for o in orders))
print("fully booked product:", fully_booked)
print("addresses:", len(addresses), "payments:", len(payments), "reviews:", len(reviews), "cart:", len(cart))
print("orders per user:", dict(sorted(Counter(o.user_id for o in orders).items())))
print("orders per seller:", dict(sorted(Counter(o.seller_id for o in orders).items())))
never = set(PRODUCTS) - {pid for o in orders for pid, _, _ in o.items}
print("never ordered products:", sorted(never))
