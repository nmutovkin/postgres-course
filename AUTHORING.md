# Authoring guide

These are the conventions every module follows, so the course reads as one book. They apply both to people extending the course and to the agents that wrote it.

<!-- toc:start -->
**Contents**

- [Module layout](#module-layout)
- [Theory (`README.md`)](#theory-readmemd)
- [Exercises (`exercises.md`)](#exercises-exercisesmd)
- [Solutions (`solutions/README.md`)](#solutions-solutionsreadmemd)
- [Project milestone (`project.md`)](#project-milestone-projectmd)
- [SQL style](#sql-style)
- [Python style (Part IV)](#python-style-part-iv)
- [Navigation](#navigation)

<!-- toc:end -->

## Module layout

```
modules/NN-slug/
├── README.md        # theory
├── exercises.md     # 8–15 exercises
├── project.md       # the Bazaar milestone for this module
└── solutions/
    ├── README.md    # worked solutions for every exercise, with explanations
    └── *.sql / *.py # runnable solution files where useful
```

Some modules also have `examples/` (runnable scripts referenced from the theory) or `lab/` (compose files and scripts for hands-on infrastructure work).

## Theory (`README.md`)

**The bar: a learner should never need to open external documentation to understand the module.** Explain *what*, *how it works*, and *why it is designed that way*, plus the edge cases that bite in production.

Required structure:

1. `# Module NN: Title`
2. A short intro paragraph that motivates the topic with a Bazaar scenario.
3. **What you'll learn**: a bullet list.
4. **Before you start**: which reset state to use (`./project/bin/reset.sh NN`) and any prerequisites.
5. Numbered sections (`## 1. ...`). Each section:
   - explains the concept in prose first (intuition, then precision);
   - shows runnable SQL on the Bazaar database, with the output in a separate `text` block where the output teaches something;
   - uses diagrams (ASCII or Mermaid) where a picture helps (plans, page layouts, replication topologies).
6. Callouts as blockquotes: `> **Pitfall:**`, `> **Under the hood:**`, `> **PG 18:**`, `> **In production:**`.
7. **Summary**: the key takeaways as bullets.
8. **What's next**: one or two lines that bridge to the next module.

Depth: heavy modules (indexes, performance, internals, concurrency, replication, ORM) usually need 7,000–12,000 words. Lighter ones need 4,000–7,000. Prefer concrete examples and mechanics over adjectives.

## Exercises (`exercises.md`)

- 8–15 exercises per module, each marked **★** (warm-up), **★★** (core), or **★★★** (stretch). Aim for roughly 3 / 6 / 3.
- **Not too obvious, not too complex.** A good exercise needs one or two ideas from the module combined, or applied to a realistic Bazaar question ("which sellers had a revenue drop month-over-month?"), rather than restating an example from the theory.
- Each exercise states what to produce and gives a way to self-check: expected row count, the first row, a property that must hold, or a query to verify the result.
- Hints go in `<details><summary>Hint</summary>...</details>`.
- Exercises run against a stated reset state (usually the state at the start of the module plus the module's own milestone).

## Solutions (`solutions/README.md`)

- Every exercise is solved, with the **reasoning** explained, not just the code. Mention alternative approaches and their trade-offs.
- Every SQL solution has been **executed on PostgreSQL 17** against the stated reset state, and stated results (row counts, values) are real.

## Project milestone (`project.md`)

- States the goal in business terms, then the concrete requirements: tables, columns, constraints, functions, and acceptance checks.
- Says which reference file(s) in `project/db/` (or `project/python/`) contain the reference solution.
- Includes acceptance checks: queries or tests the learner runs to confirm the milestone is done.

## SQL style

- Keywords in UPPERCASE; identifiers in `snake_case`; 4-space indentation; one clause per line for anything non-trivial.
- Use `bigint GENERATED ALWAYS AS IDENTITY` for surrogate keys, `timestamptz` for timestamps, and `numeric(12,2)` for money.
- Show psql meta-commands in `sql` blocks only when they are run in psql; use `bash` blocks for shell.
- Never present output you did not actually get from PostgreSQL 17.

## Python style (Part IV)

- Python 3.12+, type hints, and `ruff` formatting conventions.
- psycopg 3, asyncpg, SQLAlchemy 2.0 (2.0-style APIs only), and Alembic. All code in `project/python/` must run.

## Navigation

The nav bar at the top of each module file and the **Contents** list in every Markdown file are generated. Don't edit the blocks between `` or `` by hand. After adding or renaming headings or modules, run:

```bash
python3 tools/gen_nav.py           # refresh everything
python3 tools/gen_nav.py --check   # CI-style check: exit 1 if anything is stale
```
