"""Module 24, section 5: reflection and the Inspector."""

import time

import sqlalchemy as sa

from bazaar.sa.engine import create_engine

engine = create_engine()

# 1. One table
t = time.perf_counter()
reviews = sa.Table("reviews", sa.MetaData(), autoload_with=engine)
print(f"1. Table('reviews', autoload_with=engine) in {(time.perf_counter() - t) * 1000:.0f} ms")
for col in reviews.columns:
    print(f"   {col.name:<11} {col.type!r:<22} nullable={col.nullable}")
print("   constraints:", sorted(c.name for c in reviews.constraints))
print("   foreign keys pulled in:", sorted(reviews.metadata.tables))

# 2. Everything
md = sa.MetaData()
t = time.perf_counter()
md.reflect(engine, views=True)
print(f"\n2. MetaData.reflect(views=True): {len(md.tables)} tables/views in "
      f"{(time.perf_counter() - t) * 1000:.0f} ms")
print("   partitions are tables too:", [n for n in md.tables if n.startswith("events_y2025m0")][:3])

# 3. What reflection does NOT give you
products = md.tables["products"]
print("\n3. products.status:", repr(products.c.status.type))
print("   products.search_vector computed:", str(products.c.search_vector.computed.sqltext)[:60])
print("   server default of products.attributes:", products.c.attributes.server_default.arg)
print("   python_type of status:", products.c.status.type.python_type)

# 4. The Inspector: answers without building Table objects
insp = sa.inspect(engine)
print("\n4. inspector.get_indexes('orders'):")
for ix in insp.get_indexes("orders"):
    print("   ", ix["name"], ix["column_names"], ix.get("column_sorting", ""))
print("   get_check_constraints('inventory'):",
      [c["name"] for c in insp.get_check_constraints("inventory")])
print("   enums:", [(e["name"], e["labels"]) for e in insp.get_enums()][:2])
