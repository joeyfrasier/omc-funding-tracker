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

## Files Modified

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
