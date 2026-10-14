"""Exercise 9: two branches, two heads; the CI check; rebase versus merge.

cd project/python && python ../../modules/26-migrations/solutions/ex09_heads.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from alembic import command
from alembic.script import ScriptDirectory
from alembic.util import CommandError

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# We rewrite revision files within the same second: a cached .pyc with the same
# size and mtime would be loaded instead of the edited source.
sys.dont_write_bytecode = True
from scratch_alembic import scratch_db, scratch_migrations  # noqa: E402


def heads_check(cfg) -> list[str]:  # noqa: ANN001
    """The CI check: exactly one head, or fail with a useful message."""
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) != 1:
        print(f"  CI: FAIL, {len(heads)} heads {sorted(heads)}: rebase or merge before merging")
    else:
        print(f"  CI: ok, one head {heads[0]}")
    return heads


def history(cfg) -> None:  # noqa: ANN001
    script = ScriptDirectory.from_config(cfg)
    for rev in script.walk_revisions("2701", "heads"):
        print(f"    {rev.down_revision} -> {rev.revision}  {rev.doc}")


def main() -> None:
    with scratch_migrations() as mig, scratch_db() as dsn:
        cfg = mig.config(dsn)
        cfg.set_section_option("post_write_hooks", "hooks", "")  # no ruff for scratch files
        # Two developers, each on a branch that started at head 2701:
        command.revision(cfg, "add wishlist", rev_id="2702", head="2701")
        command.revision(cfg, "add gift wrap", rev_id="2703", head="2701", splice=True)
        print("after merging both branches in git:")
        heads_check(cfg)
        try:
            command.upgrade(cfg, "head")
        except CommandError as e:
            print("  alembic upgrade head:", str(e)[:60], "...")

        # Fix 1 (preferred): rebase the second revision onto the first.
        versions = mig.root / "migrations" / "versions"
        f = next(versions.glob("2703_*.py"))
        f.write_text(
            re.sub(r"down_revision: (.*) = '2701'", r"down_revision: \1 = '2702'", f.read_text())
        )
        print("after rebasing 2703 onto 2702:")
        heads_check(cfg)
        history(cfg)
        command.upgrade(cfg, "head")

        # Fix 2 (for long-lived parallel lines): a merge revision.
        f.write_text(
            re.sub(r"down_revision: (.*) = '2702'", r"down_revision: \1 = '2701'", f.read_text())
        )
        command.merge(cfg, "heads", message="merge wishlist and gift wrap", rev_id="2704")
        print("with a merge revision instead:")
        heads_check(cfg)
        history(cfg)


if __name__ == "__main__":
    main()
