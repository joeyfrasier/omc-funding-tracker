# OMC Funding Tracker — Next Steps

*Updated: 2026-02-07*

## Current State (MVP)

✅ **Working:**
- Gmail API integration (service account: `payment-ops-email-reader@worksuite-internal-tools.iam.gserviceaccount.com`)
- CSV parsing for OASYS and D365 ACH remittance formats
- Database connection via SSH tunnel to aggregate DB (76 OMC payments found in last 7 days)
- NVC code matching against `documents_payment` table
- Flask dashboard with dark theme UI
- Activity log with real-time progress tracking

⚠️ **Limitations:**
- Gmail impersonates `zoe.merkle@worksuite.com` (may want to switch to `joey.frasier@worksuite.com` or a shared mailbox)
- LDN GSS emails are image-only PDFs — flagged for manual review, no auto-parsing
- 62 emails already processed; new runs return 0 unless using "re-run" mode
- No persistent storage — results lost on restart
- No scheduled runs — manual trigger only

## Priority 1: Core Improvements

### 1.1 Persistent Results Storage
- Store reconciliation results in SQLite or the aggregate DB
- Keep history of all runs with timestamps
- Allow viewing past runs from the dashboard

### 1.2 Scheduled Runs
- Add APScheduler or cron-based auto-reconciliation (e.g., every 2 hours during business hours)
- Email/Slack alerts when issues are found (mismatches, not-found NVCs)

### 1.3 MoneyCorp Integration
- Refresh MoneyCorp API credentials
- Pull actual funding/FX data to complete the funding picture
- Match remittance amounts → DB amounts → MoneyCorp transfers

## Priority 2: Dashboard Enhancements

### 2.1 Filtering & Search
- Filter reports by agency, date range, status
- Search by NVC code or contractor name
- Sort columns in the match table

### 2.2 Export
- CSV/Excel export of reconciliation reports
- Summary PDF for operations team

### 2.3 Manual Override
- Allow ops team to manually mark items as resolved
- Add notes/comments to individual matches
- Flag items for follow-up

## Priority 3: Operational

### 3.1 LDN GSS OCR
- Add OCR (Tesseract or Google Vision) for image-only remittance PDFs
- Parse the extracted text into the same Remittance format

### 3.2 Multi-Currency Support
- Handle GBP/EUR remittances (currently assumes USD)
- Cross-reference with MoneyCorp FX rates

### 3.3 Deployment
- Dockerize the app
- Deploy to internal infrastructure (EC2 or ECS)
- Add authentication (SSO via Google Workspace)

## Blockers

| Blocker | Status | Owner |
|---------|--------|-------|
| LDN GSS image parsing | 🟡 Needs OCR solution | Engineering |
| Gmail impersonation scope | 🟢 Working (zoe.merkle) | — |
| DB credentials | 🟢 Working | — |
| SSH bastion key | 🟢 Working (db-bastion.pem) | — |
