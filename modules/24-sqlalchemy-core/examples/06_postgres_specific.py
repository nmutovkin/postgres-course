"""Module 24, sections 7-8: writing data, and the PostgreSQL-specific constructs.

Everything that writes runs in a transaction that is rolled back at the end.
"""

import sys
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from bazaar.sa.engine import create_engine
from bazaar.sa.tables import cart_items, inventory, jobs, orders, products, users

engine = create_engine()
WANTED = set(sys.argv[1:])
p = products


def show(label: str, stmt, conn, rows: int = 5) -> None:
    if WANTED and label.split()[0].rstrip(".") not in WANTED:
        return
    print(f"--- {label}")
    print(stmt.compile(engine))
    result = conn.execute(stmt)
    if result.returns_rows:
        for r in result.fetchmany(rows):
            print("   ", tuple(r))
    else:
        print("    rowcount:", result.rowcount)
    print()


with engine.connect() as conn:
    # --- JSONB ---------------------------------------------------------------------------
    show(
        "M. jsonb: ->, ->>, #>>, @>, ?",
        sa.select(
            p.c.id,
            p.c.attributes["cpu"].astext.label("cpu"),  # ->> 'cpu'
            p.c.attributes["ram_gb"].as_integer().label("ram"),  # (->> 'ram_gb')::integer
            p.c.attributes[("storage", "gb")].astext.label("storage_gb"),  # #>> '{storage,gb}'
        )
        .where(
            p.c.category_id == 3,
            p.c.attributes.contains({"cpu": "Ryzon 9"}),  # @>  (uses the GIN index)
            p.c.attributes.has_key("screen_in"),  # ?
            p.c.attributes["ram_gb"].as_integer() >= 32,
        )
        .order_by(p.c.id)
        .limit(3),
        conn,
    )
    show(
        "M2. jsonpath through func",
        sa.select(sa.func.count())
        .select_from(p)
        .where(
            p.c.category_id == 3,
            # the path must be bound as jsonpath: a plain str is sent as ::VARCHAR, and
            # jsonb_path_exists(jsonb, varchar) does not exist
            sa.func.jsonb_path_exists(
                p.c.attributes, sa.bindparam("path", "$.storage ? (@.gb >= 2048)", pg.JSONPATH)
            ),
        ),
        conn,
    )

    # --- arrays ------------------------------------------------------------------------------
    show(
        "N. arrays: @>, &&, ANY, unnest",
        sa.select(
            sa.func.count().filter(p.c.tags.contains(["gift", "eco"])).label("gift_and_eco"),
            sa.func.count().filter(p.c.tags.overlap(["gift", "eco"])).label("gift_or_eco"),
            sa.func.count().filter(p.c.tags.any_() == "bestseller").label("bestseller"),
        ).where(p.c.status == "active"),
        conn,
    )
    tag = sa.func.unnest(p.c.tags).label("tag")
    show(
        "N2. unnest + group by",
        sa.select(tag, sa.func.count().label("n"))
        .where(p.c.status == "active")
        .group_by(tag)
        .order_by(sa.desc("n"))
        .limit(3),
        conn,
    )

    # --- full-text search -------------------------------------------------------------------------
    q = sa.func.websearch_to_tsquery("english", "wireless headphones -kids")
    rank = sa.func.ts_rank(p.c.search_vector, q).label("rank")
    show(
        "O. full-text search with websearch_to_tsquery and ts_rank",
        sa.select(p.c.id, p.c.title, rank)
        .where(p.c.search_vector.bool_op("@@")(q), p.c.status == "active")
        .order_by(rank.desc(), p.c.id)
        .limit(3),
        conn,
    )
    show(
        "O2. .match() (plainto_tsquery)",
        sa.select(sa.func.count()).where(
            p.c.search_vector.match("wireless headphones", postgresql_regconfig="english")
        ),
        conn,
    )

    # --- DISTINCT ON, FOR UPDATE SKIP LOCKED ----------------------------------------------------------
    show(
        "P. DISTINCT ON: each user's latest order",
        sa.select(orders.c.user_id, orders.c.id, orders.c.created_at)
        .ext(pg.distinct_on(orders.c.user_id))  # SQLAlchemy 2.0: .distinct(orders.c.user_id)
        .where(orders.c.user_id.in_([8, 11, 13]))
        .order_by(orders.c.user_id, orders.c.created_at.desc()),
        conn,
    )
    show(
        "P2. FOR UPDATE SKIP LOCKED",
        sa.select(jobs.c.id)
        .where(jobs.c.queue == "default", jobs.c.done_at.is_(None))
        .order_by(jobs.c.run_at)
        .limit(10)
        .with_for_update(skip_locked=True),
        conn,
    )
    show(
        "P3. FOR UPDATE OF one table, NOWAIT",
        sa.select(inventory.c.quantity)
        .join(p, p.c.id == inventory.c.product_id)
        .where(p.c.id == 20)
        .with_for_update(of=inventory, nowait=True),
        conn,
    )

    # --- writes ----------------------------------------------------------------------------------
    show(
        "Q. INSERT ... RETURNING",
        sa.insert(cart_items)
        .values(user_id=7, product_id=20, quantity=1)
        .returning(cart_items.c.quantity, cart_items.c.added_at),
        conn,
    )
    ins = pg.insert(cart_items).values(user_id=7, product_id=20, quantity=2)
    upsert = ins.on_conflict_do_update(
        index_elements=[cart_items.c.user_id, cart_items.c.product_id],
        set_={"quantity": cart_items.c.quantity + ins.excluded.quantity},
    ).returning(cart_items.c.quantity, sa.literal_column("xmax = 0").label("inserted"))
    show("Q2. INSERT ... ON CONFLICT DO UPDATE with excluded", upsert, conn)

    new_user = pg.insert(users).values(email="Steven.Durand17@Example.com", full_name="Steven")
    show(
        "Q3. ON CONFLICT on an expression index, DO NOTHING",
        new_user.on_conflict_do_nothing(index_elements=[sa.func.lower(users.c.email)]).returning(
            users.c.id
        ),
        conn,
    )
    show(
        "Q4. ON CONFLICT ON CONSTRAINT ... WHERE",
        pg.insert(p)
        .values(seller_id=7, category_id=3, sku="S7-001725", title="dup", price=29.99)
        .on_conflict_do_update(
            constraint="products_seller_id_sku_key",
            set_={"price": sa.text("excluded.price")},
            where=p.c.status != "archived",
        )
        .returning(p.c.id, p.c.price),
        conn,
    )
    show(
        "R. UPDATE ... FROM (correlated multi-table update)",
        sa.update(inventory)
        .where(inventory.c.product_id == p.c.id, p.c.seller_id == 2000)
        .values(quantity=inventory.c.quantity + 1)
        .returning(inventory.c.product_id, inventory.c.quantity),
        conn,
        rows=3,
    )
    show(
        "R2. DELETE ... USING",
        sa.delete(cart_items).where(cart_items.c.product_id == p.c.id, p.c.seller_id == 2),
        conn,
    )

    # --- functions -----------------------------------------------------------------------------
    pick = (
        sa.select(inventory.c.product_id)
        .join(p, p.c.id == inventory.c.product_id)
        .where(p.c.status == "active", inventory.c.quantity - inventory.c.reserved >= 2)
        .order_by(inventory.c.product_id)
        .limit(1)
        .scalar_subquery()
    )
    pid = conn.execute(sa.select(pick)).scalar_one()
    call = sa.select(
        sa.func.place_order(
            8, sa.bindparam("items", [{"product_id": pid, "quantity": 1}], type_=pg.JSONB)
        ).label("order_id")
    )
    show("S. a set-returning function: place_order()", call, conn)
    conn.execute(sa.insert(jobs).values(kind="m24_demo"))
    dq = sa.func.dequeue_jobs("default", "m24-demo", 5).table_valued("id", "kind", "payload")
    show("S2. table_valued(): SELECT ... FROM dequeue_jobs(...)", sa.select(dq.c.id, dq.c.kind), conn)
    conn.rollback()
    print("(rolled back)")
_ = datetime.now(UTC)
