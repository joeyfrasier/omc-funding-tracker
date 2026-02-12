# OFM — 4-Way Reconciliation Design Spec

**Date:** 2026-02-11
**Author:** Red
**Status:** Draft — awaiting Joey's approval before implementation

---

## Overview

Upgrade OFM from 3-way matching to **4-way matching** by separating MoneyCorp data into two distinct legs:

| Leg | Source | What It Represents | Key Fields |
|-----|--------|-------------------|------------|
| **1. Remittance** | Gmail (OASYS/D365 ACH/LDN GSS) | Agency says "we're paying you" | NVC codes, line amounts, lump-sum total, agency name |
| **2. Invoice** | Worksuite Aggregate DB | Our internal record of what's owed | NVC code, amount, currency, status, tenant |
| **3. Funding** _(NEW)_ | MoneyCorp `receivedPayments` | USD cash arriving INTO sub-account from customer | Lump-sum USD amount, payer name, date |
| **4. Payment** _(renamed from "Funding")_ | MoneyCorp `payments` | Money going OUT to contractor | NVC code (from `paymentReference`), amount, currency, recipient, status |

### Why This Matters

- Current "funding" leg is actually outbound payments — which are often in **foreign currencies** (PHP, EUR, GBP, MXN, etc.) after FX conversion
- This causes false mismatches when comparing against USD remittances/invoices
- The **received payments** (leg 3) are always in **USD** — matching the currency of remittances and invoices
- Separating inbound vs outbound gives true end-to-end visibility: **agency funds → money arrives → invoice exists → contractor gets paid**

---

## Data Analysis

### MoneyCorp Received Payments (`/accounts/{accountId}/receivedPayments`)

**Volume:** 209 records across 12 OMC sub-accounts (~$3.28M total)

**Record structure:**
```json
{
  "id": "40755325",
  "type": "ReceivedPayment",
  "attributes": {
    "paymentStatus": "Cleared",
    "paymentType": "None",
    "currency": "USD",
    "amount": "4500.00",
    "paymentDate": "2026-02-11T00:00:00",
    "createdOn": "2026-02-11T12:15:54.717",
    "createdBy": "svc OMNI",
    "reference": null,
    "mslReference1": "40034505559",
    "mslReference2": "40034505559",
    "mslReference3": "",
    "mslReference4": "",
    "mslReference5": "",
    "mslReference6": "",
    "mslReference7": "",
    "mslReference8": "",
    "infoToAccountOwner": "THE SCIENOMICS DES:ACH10030 ID:OCI002503946TRX\r\nINDN:CK5200859133KC - Money CO ID:9080705421 CCD\r\n",
    "originatingCountry": null
  }
}
```

**Key observations:**
- Always USD (customer funds in USD regardless of payment currency)
- `infoToAccountOwner` contains the **payer/agency name** (e.g., "THE SCIENOMICS", "BBDO USA LLC") — parseable from the first segment before `DES:`
- `reference` field is usually null — no NVC codes on inbound funding
- Amounts are **lump sums** per wire/ACH transfer — one received payment may cover multiple NVC invoices
- `mslReference1/2` are bank transaction references

### Matching Challenge

Received payments are **lump sums without NVC codes**. We can't match them 1:1 to individual invoices. Instead:

**Remittance emails ARE the bridge.** Each remittance email from an agency contains:
- A **total payment amount** (the `Remittance.payment_amount` lump sum)
- Individual **NVC line items** that sum to that total
- The **agency name** (from email subject/body)

So the matching chain is:
```
Received Payment (lump USD) ←→ Remittance Email (lump USD + NVC breakdown) ←→ Individual NVCs ←→ Invoices + Outbound Payments
```

---

## Schema Changes

### New table: `received_payments`

```sql
CREATE TABLE received_payments (
    id TEXT PRIMARY KEY,                    -- MoneyCorp receivedPayment ID
    account_id TEXT NOT NULL,               -- MoneyCorp sub-account ID
    account_name TEXT,                      -- e.g., "Worksuite Inc. re: Omnicom Healthcare"
    amount REAL NOT NULL,                   -- USD amount received
    currency TEXT DEFAULT 'USD',            -- Always USD in practice
    payment_date TEXT,                      -- Date funds arrived
    payment_status TEXT,                    -- "Cleared", etc.
    payer_name TEXT,                        -- Extracted from infoToAccountOwner
    raw_info TEXT,                          -- Full infoToAccountOwner field
    msl_reference TEXT,                     -- mslReference1 (bank ref)
    created_on TEXT,                        -- MoneyCorp createdOn timestamp
    -- Matching fields
    matched_remittance_email_id TEXT,       -- Linked remittance email (if matched)
    match_confidence REAL,                  -- 0.0-1.0 confidence score
    match_method TEXT,                      -- "exact_amount", "amount_date", "fuzzy", "manual"
    match_status TEXT DEFAULT 'unmatched',  -- unmatched, matched, partial, manual
    matched_at TEXT,
    matched_by TEXT,                        -- "auto" or user
    notes TEXT,
    fetched_at TEXT NOT NULL
);
CREATE INDEX idx_rp_account ON received_payments(account_id);
CREATE INDEX idx_rp_status ON received_payments(match_status);
CREATE INDEX idx_rp_date ON received_payments(payment_date);
CREATE INDEX idx_rp_payer ON received_payments(payer_name);
```

### Rename in `reconciliation_records`

Current `funding_*` columns represent **outbound payments**. Rename for clarity and add received payment linkage:

```sql
-- Rename existing funding columns → payment (outbound)
ALTER TABLE reconciliation_records RENAME COLUMN funding_amount TO payment_amount;
ALTER TABLE reconciliation_records RENAME COLUMN funding_account_id TO payment_account_id;
ALTER TABLE reconciliation_records RENAME COLUMN funding_date TO payment_date;
ALTER TABLE reconciliation_records RENAME COLUMN funding_currency TO payment_currency;
ALTER TABLE reconciliation_records RENAME COLUMN funding_status TO payment_status;
ALTER TABLE reconciliation_records RENAME COLUMN funding_recipient TO payment_recipient;
ALTER TABLE reconciliation_records RENAME COLUMN funding_recipient_country TO payment_recipient_country;

-- Add received payment linkage (per-NVC, via remittance email)
ALTER TABLE reconciliation_records ADD COLUMN received_payment_id TEXT;
ALTER TABLE reconciliation_records ADD COLUMN received_payment_amount REAL;
ALTER TABLE reconciliation_records ADD COLUMN received_payment_date TEXT;
```

> **Note:** SQLite supports `ALTER TABLE RENAME COLUMN` since 3.25.0 (2018). macOS ships 3.39+. Safe to use.

### New table: `remittance_emails` (upgrade existing `email_db`)

Add fields to track lump-sum matching:

```sql
ALTER TABLE emails ADD COLUMN remittance_total REAL;       -- Parsed lump-sum total from CSV
ALTER TABLE emails ADD COLUMN agency_name TEXT;             -- Normalized agency name
ALTER TABLE emails ADD COLUMN received_payment_id TEXT;     -- Linked received payment
ALTER TABLE emails ADD COLUMN funding_match_status TEXT DEFAULT 'unmatched';
```

---

## Matching Algorithm

### Stage 1: Received Payment → Remittance Email (Lump-Sum Matching)

Match each `received_payment` to a `remittance email` using:

```
Score = weight_amount * amount_match + weight_date * date_match + weight_payer * payer_match
```

| Signal | Weight | Logic |
|--------|--------|-------|
| **Amount** | 0.5 | Exact match (±$0.01) = 1.0, within 1% = 0.7, within 5% = 0.3 |
| **Date** | 0.2 | Same day = 1.0, ±1 day = 0.8, ±3 days = 0.5, ±7 days = 0.2 |
| **Payer name** | 0.3 | Fuzzy match of `payer_name` (from `infoToAccountOwner`) against `agency` (from email subject). Use normalized names + alias table. |

**Thresholds:**
- Score ≥ 0.8 → auto-match
- Score 0.5–0.8 → suggest (needs manual confirmation)
- Score < 0.5 → unmatched

**Agency alias table** (handles name variations):
```python
AGENCY_ALIASES = {
    "THE SCIENOMICS": ["Scienomics"],
    "ADELPHI RESEARCH": ["Adelphi Research Global"],
    "DDB CHICAGO INC.": ["DDB Chicago"],
    "BBDO USA LLC": ["BBDO"],
    "ENERGY BBDO": ["Energy BBDO"],
    "FLEISHMANHILLARD": ["FleishmanHillard"],
    # ... built iteratively from data
}
```

### Stage 2: Cascade to NVC Records

Once a received payment is matched to a remittance email, all NVC codes from that email inherit the funding status:

```python
for nvc in remittance_email.nvc_lines:
    recon_record[nvc].received_payment_id = received_payment.id
    recon_record[nvc].received_payment_amount = received_payment.amount  # lump sum (shared)
    recon_record[nvc].received_payment_date = received_payment.payment_date
    recalculate_match_status(nvc)  # Now checks all 4 legs
```

### Stage 3: Updated Match Status Calculation

```python
def recalculate_match_status(nvc_code):
    has_remittance = remittance_amount is not None
    has_invoice    = invoice_amount is not None
    has_funding    = received_payment_id is not None   # ← NEW: inbound USD
    has_payment    = payment_amount is not None         # ← RENAMED: outbound to contractor

    if all 4:
        if remittance ≈ invoice (USD comparison):
            if payment matches (any currency, via NVC):
                status = "full_4way"       # ✅ Complete end-to-end
            else:
                status = "payment_mismatch"
        else:
            status = "amount_mismatch"
    elif has_remittance and has_invoice and has_funding:
        status = "3way_awaiting_payment"   # Funded but not yet paid out
    elif has_remittance and has_invoice and has_payment:
        status = "3way_no_funding"         # Paid out but no inbound funding record
    elif has_remittance and has_invoice:
        status = "2way_matched"            # Email + DB match, no MoneyCorp data
    elif has_invoice and has_payment:
        status = "invoice_payment_only"    # No remittance email
    elif only one leg:
        status = "remittance_only" / "invoice_only" / "payment_only"
    ...
```

---

## Sync Changes

### New sync function: `sync_received_payments()`

```python
def sync_received_payments():
    """Fetch receivedPayments from all OMC sub-accounts."""
    for account in omc_accounts:
        records = GET /accounts/{account_id}/receivedPayments
        for record in records:
            payer_name = parse_payer_from_info(record.infoToAccountOwner)
            upsert into received_payments table
    
    # After fetching, run matching
    run_funding_matcher()
```

### Updated sync cycle order

```python
def run_sync_cycle():
    sync_emails()              # 1. Get remittance emails (has NVC breakdown + totals)
    sync_invoices()            # 2. Get Worksuite DB records
    sync_received_payments()   # 3. Get inbound USD funding  ← NEW
    sync_payments()            # 4. Get outbound payments (renamed from sync_funding)
    run_funding_matcher()      # 5. Match received payments ↔ remittance emails ← NEW
```

### Rename `sync_funding()` → `sync_payments()`

Update to use renamed columns (`payment_*` instead of `funding_*`).

---

## API Changes

### New endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/received-payments` | List received payments with filters (account, status, date range, payer) |
| `GET` | `/api/received-payments/:id` | Single received payment detail |
| `GET` | `/api/received-payments/summary` | Counts by match_status + total amounts |
| `POST` | `/api/received-payments/:id/match` | Manual match to a remittance email |
| `POST` | `/api/received-payments/:id/unmatch` | Undo a match |
| `GET` | `/api/received-payments/suggestions/:id` | Get suggested remittance email matches |

### Updated endpoints

| Endpoint | Change |
|----------|--------|
| `/api/recon/queue` | Add `received_payment_*` fields, new status values |
| `/api/recon/summary` | New status categories (full_4way, 3way_awaiting_payment, etc.) |
| `/api/overview` | Add received payments stats (total inbound, matched %, unmatched) |
| `/api/sync/status` | Add `received_payments` source |

---

## Frontend Changes

### Rename "Funding" tab → "Received Funds"

Shows inbound USD from customers:
- Table: Date, Payer, Amount (USD), Sub-Account, Status, Matched Remittance
- Filters: account, payer, match status, date range
- Click row → detail panel showing linked remittance email + NVC breakdown
- Manual match action (dropdown of suggested remittance emails)

### New "Payments" tab (or rename existing)

Shows outbound payments to contractors:
- Table: Date, NVC, Recipient, Amount, Currency, Status, Sub-Account
- This is the existing funding data, just reframed

### Updated "Workbench" tab

- Status badges reflect 4-way statuses
- New status colors:
  - `full_4way` → green ✅
  - `3way_awaiting_payment` → blue (funded, payment pending)
  - `3way_no_funding` → amber (paid but no inbound record)
  - `2way_matched` → yellow (no MoneyCorp data yet)
  - `amount_mismatch` → red
  - `payment_mismatch` → orange

### Updated "Overview" tab

Add funding pipeline visualization:
```
Remittances Received    →    Funds Arrived    →    Invoices    →    Payments Out
   $2.3M (320)              $3.28M (209)         $X (488)        $Y (454)
```

---

## Migration Plan

### Phase 1: Schema + Sync (no breaking changes)
1. Create `received_payments` table
2. Add new columns to `reconciliation_records` (additive only — keep old `funding_*` columns temporarily)
3. Build `sync_received_payments()` + payer name parser
4. Build funding matcher (received payment ↔ remittance email)
5. Populate data, validate matching quality

### Phase 2: Rename + API
1. Migrate `funding_*` → `payment_*` columns
2. Add new API endpoints for received payments
3. Update existing API responses with 4-way status values
4. Update `recalculate_match_status()` for 4-way logic

### Phase 3: Frontend
1. Rename Funding tab → Received Funds
2. Add Payments tab (outbound)
3. Update Workbench with new status badges
4. Update Overview with funding pipeline
5. Add manual matching UI for received payments

---

## Open Questions

1. **Pagination:** The `receivedPayments` endpoint returned all records on one page (max 73 per account). Do we need pagination handling, or is the volume always small enough?

2. **Historical data:** Current received payments appear to go back ~1 month. Is there a date filter on the API, or do we get everything available?

3. **Agency name normalization:** The `infoToAccountOwner` field has inconsistent formatting (some have `DES:ACH...`, some have `WIRE TYPE:WIRE IN...`). We'll need iterative refinement of the payer name parser. Should we start with exact-amount matching as the primary signal and use payer name as confirmation?

4. **Multiple received payments per remittance:** Can a single remittance email correspond to multiple wires (e.g., split payments)? If so, we need M:N matching capability.

5. **Remittance emails without received payments:** Some agencies may pay late. Should we flag remittances older than N days with no matched funding as "overdue"?

---

## Configuration UI (macOS System Settings Style)

### Design
A modal overlay triggered from the header avatar menu → "Configuration". Styled like macOS System Settings:

- **Left sidebar:** Section list with icons, search bar at top
- **Right panel:** Settings content for selected section
- **Smooth transitions** between sections

### Sections

| Section | Content |
|---------|---------|
| **Data Sources** | Gmail service account, email source configs (OASYS, D365 ACH, LDN GSS), Worksuite DB connection, MoneyCorp API status |
| **Sync Schedule** | Sync interval (currently 5min), last sync timestamps per source, manual trigger button, sync history |
| **Matching Thresholds** | Amount tolerance (default ±$0.01), date window for funding match (default ±3 days), auto-match confidence threshold (default 0.8), suggest threshold (default 0.5) |
| **Agency Aliases** | Payer name → agency mappings for received payment matching, add/edit/delete aliases, import from CSV |
| **Notifications** | Alert on new mismatches, daily summary email toggle, Slack webhook for critical alerts |
| **Display Preferences** | Default tab, records per page, currency format, timezone, dark/light theme toggle |

### API Endpoint
`GET /api/config` — returns current configuration
`PUT /api/config` — update configuration (future)

