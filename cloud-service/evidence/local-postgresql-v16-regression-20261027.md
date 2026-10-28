# Local PostgreSQL v16 regression (2026-10-27)

This is a local-only regression. No CloudBase endpoint, remote database, account, or secret was accessed.

- Runtime: Python 3.11.15 in a temporary `/tmp` virtual environment; PostgreSQL 17.11 Homebrew binaries.
- Harness: `infra/cloud/validate_local_postgres.py`, using a disposable PostgreSQL cluster bound to a Unix socket under `/tmp`.
- Applied the base schema and migrations v2–v16 in order, then reapplied v11–v16 in ascending order to check idempotence.
- Client table/RPC privilege checks passed for `anon` and `authenticated`.
- Regression result: **268 tests passed, 12 skipped**. Skips are opt-in CloudBase integration tests; they were not run.
- Retained local PostgreSQL data and test logs at `/tmp/yingxu-pg-5gh2s7vl` for replay/inspection.

The separately verified function ZIP remains a local, undeployed candidate. Its SHA-256 and source/schema checks are recorded in `cloudbase-v16-migration-20261027.md`.
