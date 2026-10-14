"""Reuse the package's fixtures and hooks (fresh_db, the `migrated` skip logic, ...).

Run from project/python, so that `tests` and `bazaar` are importable and
pyproject.toml's pytest settings (asyncio_mode = auto) apply:

    python -m pytest -c pyproject.toml ../../modules/28-capstone/solutions
"""

from tests.conftest import *  # noqa: F403
