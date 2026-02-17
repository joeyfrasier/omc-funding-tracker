# OMC Funding Tracker — Refactor Log

## Phase 1: Security & Correctness (2026-02-17)

### Task 1: Verify service-account.json git history
**Status:** CLEAR
**Action:** Ran `git log --all -- service-account.json` — returned empty. File was never committed. It's properly listed in `.gitignore`.
**No rotation needed.**

---

### Task 2: Unify PAYMENT_STATUS to single source of truth
**Status:** FIXED
**Problem:** `db_client.py` and `reconciler.py` defined contradictory payment status mappings:

| Code | db_client.py (canonical) | reconciler.py (was wrong) |
|------|--------------------------|---------------------------|
| 0    | Draft                    | Draft                     |
| 1    | Approved                 | Submitted                 |
| 2    | Processing               | Approved                  |
| 3    | In Flight                | Processing                |
| 4    | Paid                     | In Flight                 |
| 5    | Rejected                 | Paid                      |
| 6    | Cancelled                | Rejected                  |

This meant `reconciler.py` was summing "Paid" invoices using `status == 5`, which is actually "Rejected" in the real DB.

**Changes:**
- `reconciler.py`: Removed local `PAYMENT_STATUS` dict, replaced with `from db_client import PAYMENT_STATUS, status_label`
- `reconciler.py`: Fixed status code references: Paid=4, Approved=1, Processing=2
- `reconciler.py`: Changed `PAYMENT_STATUS.get(...)` to use shared `status_label()` function

---

### Task 3: Remove hardcoded infrastructure defaults from source
**Status:** FIXED
**Problem:** `db_client.py` had production RDS hostname, bastion IP, DB username, and local SSH key path as default values in `os.getenv()` calls. Anyone reading the source code learns the exact network topology.

**Changes:**
- `db_client.py`: Added `_require_env()` helper that raises `RuntimeError` if env var is missing
- `DB_HOST`, `DB_NAME`, `DB_USER` now required — fail loudly if not set
- `SSH_BASTION` and `SSH_KEY` default to empty string (only needed in SSH tunnel mode)
- `gmail_client.py`: Removed hardcoded `zoe.merkle@worksuite.com` default for `GMAIL_IMPERSONATE`
- `.env.example`: Fixed variable names to match code (`SERVICE_ACCOUNT_FILE` not `GOOGLE_SERVICE_ACCOUNT_JSON`, `GMAIL_IMPERSONATE` not `GOOGLE_IMPERSONATE_USER`), added `CORS_ORIGINS` and `SSH_TUNNEL_DISABLED` entries

---

### Task 4: Fix health check path, Flask bind address, CORS config
**Status:** FIXED
**Problems:**
1. Docker health check hit `/health` but endpoint is `/api/health` → container always `unhealthy`
2. Flask bound to `127.0.0.1` → unreachable from outside Docker container
3. CORS origins hardcoded to localhost → blocks production deployment

**Changes:**
- `docker-compose.yml`: Health check path changed to `/api/health`
- `app.py`: Flask host changed from `127.0.0.1` to `0.0.0.0`
- `api.py`: CORS origins now read from `CORS_ORIGINS` env var (comma-separated), with localhost defaults for dev

---

### Task 5: Fix funding_amount → payment_amount stale references
**Status:** FIXED
**Problem:** Migration `_migrate_4way_columns()` renamed `funding_*` columns to `payment_*`, but `api.py` still referenced old names in `recon_suggestions` and `recon_associate` endpoints. The suggestions endpoint's funding-amount matching branch was silently broken (always returned `None`).

**Changes:**
- `api.py` `recon_suggestions`: Changed `funding_amount`/`funding` → `payment_amount`/`payment` in both field lists
- `api.py` `recon_associate`: Changed `donor.get('funding_amount')` → `donor.get('payment_amount')`, `funding_account_id` → `payment_account_id`, `funding_date` → `payment_date`, and `upsert_from_funding()` → `upsert_from_payment()`

---

### Task 6: Fix matcher.py counter bug on status override
**Status:** FIXED
**Problem:** When a payment matched by amount but had status Rejected/Cancelled, the code overrode the status to `status_issue` but didn't decrement `matched_count` or `mismatched_count`. This inflated match rates in reports.

**Changes:**
- `matcher.py`: Added `status_issue_count: int = 0` field to `ReconciliationReport`
- `matcher.py`: Before setting `status = 'status_issue'`, now decrements the previously incremented counter (`matched_count` or `mismatched_count`) and increments `status_issue_count`
- `matcher.py`: Added `'status_issues'` to the `summary` property output

---

---

## Phase 2: Consolidation (2026-02-17)

### Task 7: Delete dead code
**Status:** DONE
**Deleted:** `remittance_parser.py`, `reconciler.py`, `dashboard.py`, `.streamlit/config.toml`
**Frontend:** Removed dead `ReconcileTab` and `HistoryTab` from `page.tsx` (~217 lines)
**Cleaned imports:** Removed unused `ReconcileResult`, `ProcessedEmail`, `StatsData` types

---

### Task 8: Extract reconciliation pipeline into shared service
**Status:** DONE
**Problem:** The fetch-parse-reconcile-mark workflow was duplicated across `api.py` and `app.py` (~100 lines each). Both had nearly identical code for the 4-step pipeline.

**Changes:**
- Created `reconciliation_service.py` with `run_pipeline()`, `format_report_data()`, `build_summary()`
- `run_pipeline()` accepts an optional `progress_callback` for UI progress tracking
- Returns a `ReconciliationResult` dataclass with all stats
- `api.py` `/api/reconcile` now calls `run_pipeline()` (3 lines vs 100)
- `app.py` `/api/run` now calls `run_pipeline()` with progress callback (20 lines vs 130)
- Removed duplicate `_build_summary()` from `app.py`, uses `build_summary()` from service

---

### Task 9: Fix DB connection leaks
**Status:** DONE
**Problem:** ~30 functions across `recon_db.py` and `email_db.py` had `conn.close()` not protected by try/finally.

**Changes:**
- Refactored `_get_conn()` to `@contextmanager` in both files
- All consumer functions now use `with _get_conn() as conn:`
- Connection always closed even on exception

---

### Task 10: Split api.py into FastAPI routers
**Status:** DONE
**Problem:** `api.py` was 1137 lines with all endpoints in a single file, plus 10+ raw `sqlite3.connect()` calls bypassing the DB helper modules.

**Changes:**
- Created `routers/` package with 7 router modules:
  - `routers/__init__.py` — shared `serialize()` and `DecimalEncoder`
  - `routers/core.py` — health, overview, tenants, moneycorp, config
  - `routers/emails.py` — email fetch, processed, detail
  - `routers/payruns.py` — payruns, payments, cached data
  - `routers/recon.py` — reconciliation records, queue, suggestions, associate, flag
  - `routers/received_payments.py` — received payments CRUD + suggestions
  - `routers/sync.py` — sync trigger, status
  - `routers/search.py` — cross-search
- `api.py` reduced from 1137 → 110 lines (thin app shell with router mounting)
- All 10 raw `sqlite3.connect()` calls eliminated from API layer
- Added 8 helper functions to `recon_db.py`: `get_agency_stats()`, `get_recon_queue()`, `get_nvc_codes_for_email()`, `get_email_remittance_totals()`, `update_recon_flag()`, `append_recon_note()`, `search_recon_records()`, `find_amount_suggestions()`
- Added `search_email_matches()` to `email_db.py`
- Fixed `sync_service.py` `run_funding_matcher()` to use helpers instead of raw sqlite3
- Updated `Dockerfile` to copy `routers/` directory

---

### Task 11: Pin all Python dependencies
**Status:** DONE
- Removed unused `streamlit` and `pandas`
- All deps pinned with exact versions in `requirements.txt`
- Added `fastapi`, `uvicorn`, `pydantic` (previously installed via Dockerfile only)
- Dockerfile: removed redundant `pip install fastapi uvicorn`, fixed `COPY *.json` footgun

---

## Phase 1 Files Modified

| File | Changes |
|------|---------|
| `db_client.py` | Required env vars, removed hardcoded defaults |
| `reconciler.py` | Import canonical PAYMENT_STATUS, fix status code usage |
| `matcher.py` | Fix counter bug, add status_issue_count |
| `api.py` | CORS from env var, fix funding→payment column refs |
| `app.py` | Flask bind 0.0.0.0 |
| `docker-compose.yml` | Fix health check path |
| `gmail_client.py` | Remove hardcoded email default |
| `.env.example` | Align var names with code, add new vars |

## Phase 2 Files Modified

| File | Changes |
|------|---------|
| `api.py` | Rewritten: 1137 → 110 lines, mounts 7 routers |
| `app.py` | Uses `reconciliation_service`, removed duplicated pipeline + summary |
| `reconciliation_service.py` | NEW: shared fetch-parse-reconcile pipeline |
| `routers/` | NEW: 7 router modules + `__init__.py` |
| `recon_db.py` | 8 new helper functions, replaced old `get_recon_records_queue()` |
| `email_db.py` | Added `search_email_matches()`, `Optional` import |
| `sync_service.py` | Fixed `run_funding_matcher()` — uses helpers, no raw sqlite3 |
| `Dockerfile` | Added `COPY routers/` |
| `requirements.txt` | Removed streamlit/pandas, pinned all deps |
