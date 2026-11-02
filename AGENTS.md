# overcooked 1102_V3

- User scope: inspect all origin branches except `t11-t20-demo`; integrate T01–T13 architecture on `1102_V3`.
- Read `docs/1102_V3/README.md`, `SOURCES.md`, and `DATABASE.md` before changing integration or data behavior.
- Source datasets, existing importer commands and inherited source approval files are frozen. Do not run Excel/SQL/seed imports, migrations, crawlers, index builds or external models for this architecture task.
- New empty configuration contracts are deliberately pending review. Never populate them with historical approvals or pretend missing values are zero.
- Preserve existing T07–T10 contracts, exact arithmetic, ±10% energy/protein checks, household authorization, and distinct package spend/proportional cost.
- Use synthetic SQLite, synthetic temporary workbooks and fake external services for checks. Leave `TEST_DATABASE_URL` unset; PostgreSQL/pgvector and real data acceptance remain NOT_RUN until separately authorized.
- Preserve `main` and all source branches. Do not push or rewrite shared history without a user request.
- Record actually executed checks and limitations in `docs/1102_V3/VALIDATION.md`. Historical reports are not evidence for this branch.
