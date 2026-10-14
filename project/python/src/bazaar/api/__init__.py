"""The Bazaar HTTP service (module 28): FastAPI on the async SQLAlchemy engine.

    uvicorn bazaar.api.app:app                     # one process
    uvicorn bazaar.api.app:app --workers 4         # four processes, a pool each

Layout:
    app.py       create_app(): lifespan (engines, schema check, shutdown), routers
    db.py        Database: the three engines and their session factories; dependencies
    errors.py    domain and database errors -> application/problem+json
    middleware.py request id, deadline, access log, sqlcommenter route tags
    schemas.py   pydantic request/response models
    routes/      health, catalog (products, search), cart + checkout + orders,
                 seller portal (RLS), admin
"""
