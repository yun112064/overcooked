# 1101_v2 integration record

`1101_v2` is based on `1101_Ｖ1`. It keeps the versioned 1027 SQL recipe release as the only production recipe source and selectively ports unique, tested functionality from the remaining remote branches.

## Included functionality

- `codex/integrate-t11-t12-audit`: audited T11 knowledge retrieval and T12 nutrition planning.
- `t11-t20-demo`: T13–T20 meal cost, generation, result, plan, deployment, retry, load-test and operations features. This branch has an unrelated Git root, so its functional files and migrations were integrated instead of merging its repository snapshot.
- `feature/mock-supermarket`: the standalone local supermarket delivery demo.

## Superseded branches

- `T01_recipe_database` and `feature/recipe_database`: superseded by the 1027 versioned SQL seed and active-release schema in `1101_Ｖ1`.
- `T02_pricing` and `feature/retail_pricing`: superseded by `price_quote_snapshots`, SQL-based ingredient loading and the two-source price collector.
- `T13_ingredient_costs`: superseded by the newer T13 implementation imported from `t11-t20-demo`.
- `ticket/T03-Zhu`: superseded by nutrition rule version 0.4 and its regression tests.
- the separate T11/T12 remote branches: already contained in the audited T11/T12 integration branch.

“Included” means retaining each branch's current unique function. Obsolete Excel loaders, duplicate recipe tables and duplicate Alembic heads are intentionally not restored.

## Data-source rule

- Production recipe and ingredient identity data comes from the active row in `recipe_data_releases` and its linked SQL tables.
- Startup applies migrations, verifies the release manifest and installs `data/recipe_data_1027.sql.gz` idempotently.
- Price collection and the knowledge administration matcher both read the active SQL release.
- The original workbook is an offline release-building input only and is not committed or required by application startup.
