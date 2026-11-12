# Rules for every backend work package (AURA 2.0)

Repository: /home/claude/aura-ai- (branch aura-2.0). Specs: /home/claude/aura-specs/ (00-decisions.md is binding;
10-permissions.md, 20-taskloop-memory.md, 30-migrations.md are the designs; 11- and 21-frontend-contract.md settle
API shapes the web client already uses).

1. Scope: implement exactly the work packages named in your brief, as the spec defines their files and "done when"
   conditions. Do not redesign. Do not touch frontend/ or docs/design/. No git commands that change the tree or
   history (no commit, stash, checkout, reset, clean): the lead commits.
2. Environment: `. /home/claude/venv/bin/activate`; run tests from backend/ with
   `python -m pytest -q -p no:cacheprovider`. The suite is green when you start (the count is in your brief). If it
   is not, stop and report. It must be green when you finish. Never run two pytest processes at once.
3. Portability: CI runs Linux, Windows and macOS, with and without the optional device packages (psutil,
   pyautogui, pyperclip). Tests must skip, not fail, when those are missing. No POSIX-only assumptions in code or
   tests (paths, shells, signals, file locking, case sensitivity).
4. Migrations: follow section 6 of 30-migrations.md (revision ids, both dialects, batch mode on SQLite, guards,
   SQL Core only). A new revision's down_revision is the current head (see EXPECTED_HEAD in backend/app/migrate.py);
   bump EXPECTED_HEAD and anything the spec says must move with it. Then run from backend/:
   `DATABASE_URL=sqlite:///./migration-check.db alembic upgrade head && DATABASE_URL=sqlite:///./migration-check.db alembic check`
   (delete migration-check.db afterwards), and tests/test_migrations.py against PostgreSQL:
   `AURA_TEST_POSTGRES_URL=$(/home/claude/scripts/pg.sh reset <your own db name>) python -m pytest -q -p no:cacheprovider tests/test_migrations.py`.
   Never write a revision that drops a 1.0.0 column or table: contract revisions are a later package.
5. API shapes: where the web client already has a type (frontend/lib/api.ts, frontend/lib/permissions.ts), return
   exactly that shape; read the contract notes first.
6. Safety: never weaken an existing protection (file fence, protected paths, remote-device limits, per-recipient
   email scoping). No secrets in code, tests, logs or stored rows. Tests make no network calls.
7. Tests: add the tests the spec names for your packages, plus any you need. Prefer table-driven tests.
8. If the spec is ambiguous or wrong, choose the safest reading, implement it, and list the point in your report.
9. Last action before reporting: `touch /home/claude/logs/<marker named in your brief>`.
10. Final report, at most 25 lines: files added/changed/deleted (paths only); tests added (names only); results (suite
    count, migration checks if any); every deviation from the spec with a one-line reason; anything the lead must
    decide; anything the next package needs to know.
