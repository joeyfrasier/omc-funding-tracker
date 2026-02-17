# OMC Funding Tracker — Next Steps

*Updated: 2026-02-17*

## Current State

### Working
- **Gmail integration** — Service account fetches OASYS, D365 ACH, LDN GSS remittance emails
- **CSV parsing** — Parses OASYS and D365 ACH remittance formats into structured data
- **Worksuite DB** — SSH tunnel to aggregate DB, matches NVC codes against `documents_payment`
- **MoneyCorp API** — Pulls outbound payments (Leg 4) and received payments (Leg 3)
- **4-way reconciliation** — Remittance / Invoice / Received Payment / Outbound Payment matching
- **Background sync** — Automatic 5-minute sync cycle across all data sources
- **Persistent storage** — SQLite DBs for reconciliation records, email history, cached invoices
- **Next.js dashboard** — Modern frontend with overview, reconciliation queue, search, payments
- **Flask dashboard** — Legacy Gen 2 UI (still running, available on :8501)
- **FastAPI API** — Modular REST API with 7 routers, consumed by Next.js frontend
- **Docker deployment** — Multi-stage Dockerfile, deployed via Coolify
- **Test suite** — 28 tests covering CSV parser, matcher, and recon_db

### Known Limitations
- LDN GSS emails are image-only PDFs — flagged for manual review, no auto-parsing
- No authentication on the dashboard (relies on network-level access control)
- Flask Gen 2 UI is still deployed alongside Next.js — should be retired once Next.js is feature-complete

## Remaining Work

### High Priority

#### Retire Flask Gen 2 UI
- Audit remaining Flask-only features (activity log SSE, progress tracking)
- Port any missing features to Next.js/FastAPI
- Remove `app.py`, Flask templates, and static assets
- Remove Flask from `requirements.txt` and `docker-entrypoint.sh`

#### LDN GSS OCR
- Add OCR (Tesseract or Google Vision) for image-only remittance PDFs
- Parse extracted text into the same `Remittance` format
- Unblocks automated processing for London GSS payments

#### Authentication
- Add SSO via Google Workspace (service account already exists)
- Protect dashboard and API endpoints

### Medium Priority

#### Export & Reporting
- CSV/Excel export of reconciliation records and queue
- Summary PDF for operations team
- Automated email/Slack alerts for mismatches and anomalies

#### Multi-Currency Support
- Handle GBP/EUR remittances (currently assumes USD)
- Cross-reference with MoneyCorp FX rates

#### ~~vector_matcher.py Cleanup~~ DONE
- ~~5 raw `sqlite3.connect()` calls remain in vector_matcher.py~~
- Migrated to `recon_db._get_conn()` context manager

### Low Priority

#### Performance
- Connection pooling for Worksuite DB (currently opens new tunnel per query)
- Cache warm-up on startup to reduce first-request latency

#### Code Quality
- Add type hints to Flask endpoints in `app.py` (or remove after retirement)
- Expand test coverage for sync_service, moneycorp_client, gmail_client
