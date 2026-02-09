"""Streamlit dashboard for OMC Pay Run Funding Tracker.

READ-ONLY — no payments or money movement.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

# Configure page first
st.set_page_config(
    page_title="OMC Funding Tracker — Worksuite",
    page_icon="https://www.worksuite.com/favicon.ico",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Project imports
from dotenv import load_dotenv
load_dotenv()

from db_client import get_omc_payments, get_omc_payruns, status_label, get_connection, OMC_TENANTS
from gmail_client import fetch_all_remittances, fetch_emails, load_processed, EMAIL_SOURCES, get_service
from csv_parser import parse_email_attachments
from matcher import reconcile_batch
from email_db import get_all_emails, get_stats, store_email, store_reconciliation, init_db

logger = logging.getLogger(__name__)

# ── Worksuite Brand Styling ──────────────────────────────────────────────
st.markdown("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@300;400;600;700;900&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
    :root {
        --ws-orange: #FF4821;
        --ws-maroon: #761900;
        --ws-black: #000000;
        --ws-white: #FFFFFF;
        --ws-lavender: #EEEBFF;
        --ws-mint: #C8EDD5;
        --ws-purple: #5032A0;
        --ws-purple-light: #B9ADEA;
        --ws-gold: #E1B347;
        --ws-green: #006032;
        --ws-gray: #DDE3EB;
    }

    /* Global font override */
    html, body, [class*="css"] {
        font-family: 'Archivo', sans-serif !important;
    }

    .block-container { padding-top: 1.5rem; }

    /* Metric cards */
    [data-testid="stMetric"] {
        background: var(--ws-white);
        border: 1px solid var(--ws-gray);
        border-radius: 12px;
        padding: 14px 18px;
    }
    [data-testid="stMetricValue"] {
        font-family: 'Archivo', sans-serif !important;
        font-weight: 700;
        font-size: 1.8rem;
        color: var(--ws-black);
    }
    [data-testid="stMetricLabel"] {
        font-family: 'Archivo', sans-serif !important;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #666;
    }

    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0;
        border-bottom: 2px solid var(--ws-gray);
    }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 20px;
        font-family: 'Archivo', sans-serif !important;
        font-weight: 600;
        font-size: 14px;
        color: #666;
    }
    .stTabs [aria-selected="true"] {
        color: var(--ws-orange) !important;
        border-bottom-color: var(--ws-orange) !important;
    }

    /* Buttons — pill shaped */
    .stButton > button {
        border-radius: 999px !important;
        font-family: 'Archivo', sans-serif !important;
        font-weight: 600;
        font-size: 14px;
        padding: 10px 28px;
        border: none;
    }
    .stButton > button[kind="primary"] {
        background-color: var(--ws-orange) !important;
        color: white !important;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: var(--ws-maroon) !important;
    }

    /* Expanders */
    [data-testid="stExpander"] {
        border: 1px solid var(--ws-gray);
        border-radius: 12px;
    }

    /* Dataframes */
    [data-testid="stDataFrame"] {
        border-radius: 8px;
        overflow: hidden;
    }

    /* Success/Error/Warning/Info boxes */
    [data-testid="stAlert"] {
        border-radius: 8px;
        font-family: 'Archivo', sans-serif !important;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #FAFAFA;
        border-right: 1px solid var(--ws-gray);
    }
    [data-testid="stSidebar"] [data-testid="stMarkdown"] {
        font-family: 'Archivo', sans-serif !important;
    }

    /* Hide Streamlit branding */
    div[data-testid="stStatusWidget"] { display: none; }
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }

    /* Section labels */
    .ws-section-label {
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #888;
        margin-bottom: 4px;
    }

    /* Display headline (Instrument Serif) */
    .ws-display {
        font-family: 'Instrument Serif', Georgia, serif !important;
        font-weight: 400;
        letter-spacing: -0.02em;
        line-height: 1.05;
    }
</style>
""", unsafe_allow_html=True)

# ── Header ───────────────────────────────────────────────────────────────
st.markdown(f"""
<div style="display:flex;align-items:center;justify-content:space-between;padding:16px 0 12px 0;border-bottom:2px solid #DDE3EB;margin-bottom:16px;">
    <div style="display:flex;align-items:center;gap:16px;">
        <span style="font-family:'Archivo',sans-serif;font-weight:900;font-size:1.5rem;color:#000;letter-spacing:-0.02em;">Worksuite</span>
        <div style="width:1px;height:24px;background:#DDE3EB;"></div>
        <span style="font-family:'Archivo',sans-serif;font-weight:700;font-size:1.1rem;color:#000;">OMC Funding Tracker</span>
        <span style="background:#FF4821;color:white;font-family:'Archivo',sans-serif;font-size:10px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;padding:3px 10px;border-radius:999px;">Read-Only</span>
    </div>
    <span style="font-family:'Archivo',sans-serif;color:#999;font-size:12px;">{datetime.now().strftime('%b %d, %Y · %H:%M')}</span>
</div>
""", unsafe_allow_html=True)

# ── Tabs ─────────────────────────────────────────────────────────────────
tab_overview, tab_emails, tab_payruns, tab_reconcile, tab_history = st.tabs([
    "Overview", "Funding Emails", "Pay Runs", "Reconcile", "History"
])

# ═══════════════════════════════════════════════════════════════════════════
# TAB: Overview
# ═══════════════════════════════════════════════════════════════════════════
with tab_overview:
    # ── Load data upfront ─────────────────────────────────────────────────
    try:
        payments = get_omc_payments(days_back=7)
    except Exception as e:
        payments = []
        db_error = str(e)
    else:
        db_error = None
    
    try:
        processed = load_processed()
        gmail_error = None
    except Exception as e:
        processed = []
        gmail_error = str(e)
    
    try:
        recon_stats = get_stats()
    except Exception:
        recon_stats = {'total_emails': 0, 'total_remittances': 0, 'matched': 0, 'mismatched': 0, 'not_found': 0, 'total_value': 0}
    
    # ── Hero metrics: The numbers that matter ─────────────────────────────
    st.markdown("#### Reconciliation Health")
    
    total_issues = recon_stats['mismatched'] + recon_stats['not_found']
    total_lines = recon_stats['matched'] + total_issues
    match_rate = (recon_stats['matched'] / total_lines * 100) if total_lines > 0 else 0
    
    hero1, hero2, hero3, hero4 = st.columns(4)
    hero1.metric("Match Rate", f"{match_rate:.1f}%", delta=None)
    hero2.metric("Matched", recon_stats['matched'], delta=None)
    hero3.metric("Issues", total_issues, delta=f"{recon_stats['mismatched']} mismatch, {recon_stats['not_found']} missing" if total_issues else None, delta_color="inverse")
    hero4.metric("Total Value", f"${recon_stats.get('total_value', 0):,.0f}")
    
    # ── Two-column layout: Agencies left, Activity right ──────────────────
    st.markdown("---")
    
    col_agencies, col_activity = st.columns([3, 2])
    
    # ── LEFT: Agency breakdown ────────────────────────────────────────────
    with col_agencies:
        st.markdown("#### By Agency")
        
        if payments:
            df = pd.DataFrame(payments)
            df['status_name'] = df['status'].apply(status_label)
            df['tenant_short'] = df['tenant'].str.replace('.worksuite.com', '', regex=False)
            
            # Build agency summary with status indicators
            agency_summary = df.groupby('tenant_short').agg(
                payments=('total_amount', 'size'),
                total=('total_amount', 'sum'),
            ).sort_values('total', ascending=False).reset_index()
            
            # Display as compact cards
            for _, row in agency_summary.head(8).iterrows():
                with st.container():
                    ac1, ac2, ac3 = st.columns([2, 1, 1])
                    ac1.markdown(f"**{row['tenant_short']}**")
                    ac2.markdown(f"{row['payments']} payments")
                    ac3.markdown(f"${row['total']:,.0f}")
            
            if len(agency_summary) > 8:
                st.caption(f"+{len(agency_summary) - 8} more agencies")
        else:
            st.info("No payment data available")
    
    # ── RIGHT: System status + quick actions ──────────────────────────────
    with col_activity:
        st.markdown("#### System Status")
        
        # Connection status as compact indicators
        status_items = []
        
        if db_error:
            st.error(f"Database: {db_error[:50]}")
        else:
            st.success(f"Database: {len(payments)} payments (7d)")
        
        if gmail_error:
            st.error(f"Gmail: {gmail_error[:50]}")
        else:
            st.success(f"Gmail: {len(processed)} processed")
        
        st.warning("MoneyCorp: Token needs refresh")
        
        st.markdown("---")
        st.markdown("#### Quick Stats")
        qs1, qs2 = st.columns(2)
        qs1.metric("Emails Processed", recon_stats['total_emails'])
        qs2.metric("Remittances", recon_stats['total_remittances'])
    
    # ── Expandable: Detailed payment breakdown ────────────────────────────
    if payments:
        with st.expander("View detailed payment breakdown", expanded=False):
            df = pd.DataFrame(payments)
            df['status_name'] = df['status'].apply(status_label)
            df['tenant_short'] = df['tenant'].str.replace('.worksuite.com', '', regex=False)
            
            detail1, detail2 = st.columns(2)
            
            with detail1:
                st.markdown("**Payment Status Distribution**")
                status_counts = df['status_name'].value_counts()
                st.bar_chart(status_counts, horizontal=True)
            
            with detail2:
                st.markdown("**Full Agency Table**")
                tenant_summary = df.groupby('tenant_short').agg(
                    count=('total_amount', 'size'),
                    total=('total_amount', 'sum')
                ).sort_values('total', ascending=False)
                tenant_summary['total'] = tenant_summary['total'].apply(lambda x: f"${x:,.2f}")
                st.dataframe(tenant_summary, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════
# TAB: Funding Emails
# ═══════════════════════════════════════════════════════════════════════════
with tab_emails:
    st.subheader("📧 Funding Request Emails")
    st.caption("Remittance emails from Omnicom agencies (OASYS, D365 ACH, LDN GSS)")
    
    col_src, col_max = st.columns([2, 1])
    with col_src:
        source = st.selectbox("Email Source", ["All Sources", "oasys", "d365_ach", "ldn_gss"])
    with col_max:
        max_emails = st.number_input("Max emails to fetch", 5, 100, 10)
    
    if st.button("🔍 Fetch Emails", key="fetch_emails"):
        with st.spinner("Connecting to Gmail API..."):
            try:
                if source == "All Sources":
                    emails = fetch_all_remittances(max_per_source=max_emails)
                else:
                    emails = fetch_emails(source, max_results=max_emails, include_processed=True)
                
                if not emails:
                    st.info("No emails found for this source/filter.")
                else:
                    st.success(f"Found {len(emails)} emails")
                    
                    # Store in session state for use in reconciliation
                    st.session_state['fetched_emails'] = emails
                    
                    rows = []
                    for e in emails:
                        att_names = [a['filename'] for a in e.get('attachments', [])]
                        rows.append({
                            'Source': e.get('source', ''),
                            'Date': e.get('date', '')[:25],
                            'Subject': e.get('subject', '')[:80],
                            'From': e.get('from', '')[:40],
                            'Attachments': ', '.join(att_names) if att_names else '(none)',
                            'Manual Review': '⚠️' if e.get('manual_review') else '',
                        })
                    
                    df = pd.DataFrame(rows)
                    st.dataframe(df, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"Error fetching emails: {e}")
    
    # Show previously fetched emails from session
    if 'fetched_emails' in st.session_state and not st.session_state.get('_just_fetched'):
        emails = st.session_state['fetched_emails']
        st.info(f"{len(emails)} emails in current session (click Fetch to refresh)")

# ═══════════════════════════════════════════════════════════════════════════
# TAB: Pay Runs
# ═══════════════════════════════════════════════════════════════════════════
with tab_payruns:
    st.subheader("🏦 OMC Pay Runs")
    
    days = st.slider("Days back", 7, 180, 30, key="payrun_days")
    
    if st.button("📊 Load Pay Runs", key="load_payruns"):
        with st.spinner("Querying aggregate DB via SSH tunnel..."):
            try:
                payruns = get_omc_payruns(days_back=days)
                st.success(f"Found {len(payruns)} pay runs in last {days} days")
                
                if payruns:
                    df = pd.DataFrame(payruns)
                    df['tenant_short'] = df['tenant'].str.replace('.worksuite.com', '', regex=False)
                    df['total_amount'] = df['total_amount'].fillna(0)
                    df['total_fmt'] = df['total_amount'].apply(lambda x: f"${x:,.2f}")
                    
                    # Summary
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Total Pay Runs", len(df))
                    c2.metric("Total Value", f"${df['total_amount'].sum():,.2f}")
                    c3.metric("Total Payments", int(df['payment_count'].sum()))
                    
                    # Table
                    display = df[['reference', 'tenant_short', 'status', 'payment_count', 'total_fmt', 'created_at']].copy()
                    display.columns = ['Reference', 'Tenant', 'Status', 'Payments', 'Total', 'Created']
                    display['Created'] = pd.to_datetime(display['Created']).dt.strftime('%Y-%m-%d %H:%M')
                    st.dataframe(display, use_container_width=True, hide_index=True)
                    
                    # Per-tenant breakdown
                    st.subheader("By Agency")
                    agency_df = df.groupby('tenant_short').agg(
                        payruns=('reference', 'count'),
                        payments=('payment_count', 'sum'),
                        total=('total_amount', 'sum')
                    ).sort_values('total', ascending=False)
                    agency_df['total'] = agency_df['total'].apply(lambda x: f"${x:,.2f}")
                    st.dataframe(agency_df, use_container_width=True)
            except Exception as e:
                st.error(f"Error loading pay runs: {e}")
    
    st.divider()
    st.subheader("Payment Lookup")
    nvc_input = st.text_input("Look up NVC code(s)", placeholder="NVC7KTPCPVVV, NVC7KY46WXLW")
    
    if nvc_input and st.button("🔎 Lookup", key="nvc_lookup"):
        from db_client import lookup_payments_by_nvc
        codes = [c.strip() for c in nvc_input.split(',') if c.strip()]
        with st.spinner(f"Looking up {len(codes)} NVC code(s)..."):
            try:
                results = lookup_payments_by_nvc(codes)
                if not results:
                    st.warning("No matching payments found in database.")
                else:
                    for code, rec in results.items():
                        with st.expander(f"{code} — ${rec['total_amount']:,.2f} — {status_label(rec['status'])}", expanded=True):
                            c1, c2, c3 = st.columns(3)
                            c1.write(f"**Tenant:** {rec['tenant'].replace('.worksuite.com', '')}")
                            c2.write(f"**Currency:** {rec['currency']}")
                            c3.write(f"**Pay Run:** {rec.get('payrun_reference', 'N/A')}")
                            st.json(rec)
                    
                    missing = [c for c in codes if c not in results]
                    if missing:
                        st.warning(f"Not found: {', '.join(missing)}")
            except Exception as e:
                st.error(f"Lookup error: {e}")

# ═══════════════════════════════════════════════════════════════════════════
# TAB: Reconcile
# ═══════════════════════════════════════════════════════════════════════════
with tab_reconcile:
    st.subheader("🔄 Run Reconciliation")
    st.caption("Fetch emails → Parse CSVs → Match NVC codes against DB → Report discrepancies")
    
    col1, col2 = st.columns(2)
    with col1:
        recon_max = st.number_input("Max emails per source", 5, 100, 20, key="recon_max")
    with col2:
        include_processed = st.checkbox("Include already-processed emails", value=False)
    
    if st.button("▶️ Run Full Reconciliation", type="primary", key="run_recon"):
        progress = st.progress(0, text="Starting...")
        log_area = st.empty()
        logs = []
        
        def log(msg):
            logs.append(f"`{datetime.now().strftime('%H:%M:%S')}` {msg}")
            log_area.markdown("\n\n".join(logs[-15:]))
        
        try:
            # Step 1: Fetch emails
            progress.progress(10, text="Step 1/4: Fetching remittance emails...")
            log("📧 Fetching remittance emails from Gmail...")
            
            if include_processed:
                # Fetch with include_processed by re-fetching
                emails = []
                for key in ['oasys', 'd365_ach']:
                    try:
                        batch = fetch_emails(key, max_results=recon_max, include_processed=True)
                        emails.extend(batch)
                        log(f"  [{key}] {len(batch)} emails")
                    except Exception as e:
                        log(f"  [{key}] ⚠️ Error: {e}")
            else:
                emails = fetch_all_remittances(max_per_source=recon_max)
            
            log(f"✅ Fetched {len(emails)} emails total")
            progress.progress(30, text=f"Step 1 complete: {len(emails)} emails")
            
            if not emails:
                st.info(f"No new emails. {len(load_processed())} already processed. Enable 'Include processed' to re-run.")
                st.stop()
            
            # Store emails
            for e in emails:
                store_email(e)
            
            # Step 2: Parse CSVs
            progress.progress(40, text="Step 2/4: Parsing CSV attachments...")
            log("📄 Parsing CSV attachments...")
            all_remittances = []
            manual_count = 0
            for i, email in enumerate(emails):
                if email.get('manual_review'):
                    manual_count += 1
                    continue
                try:
                    parsed = parse_email_attachments(email)
                    all_remittances.extend(parsed)
                    if parsed:
                        log(f"  Parsed {len(parsed)} remittance(s) from: {email.get('subject', '?')[:50]}")
                except Exception as e:
                    log(f"  ⚠️ Parse error: {e}")
            
            log(f"✅ {len(all_remittances)} remittances parsed ({manual_count} flagged for manual review)")
            progress.progress(60, text=f"Step 2 complete: {len(all_remittances)} remittances")
            
            if not all_remittances:
                st.warning("No parseable remittances found in the emails.")
                st.stop()
            
            # Step 3: Reconcile against DB
            progress.progress(70, text="Step 3/4: Reconciling against database...")
            log("🗄️ Reconciling against Worksuite aggregate DB...")
            reports = reconcile_batch(all_remittances)
            log(f"✅ {len(reports)} reconciliation reports generated")
            progress.progress(90, text=f"Step 3 complete: {len(reports)} reports")
            
            # Store results
            for report in reports:
                eid = report.remittance.source_email_id
                if eid:
                    store_reconciliation(eid, report)
            
            # Step 4: Mark processed
            from gmail_client import mark_processed
            mark_processed([e['id'] for e in emails])
            log(f"✅ Marked {len(emails)} emails as processed")
            progress.progress(100, text="✅ Reconciliation complete!")
            
            # Summary
            st.divider()
            total_matched = sum(r.matched_count for r in reports)
            total_mismatched = sum(r.mismatched_count for r in reports)
            total_not_found = sum(r.not_found_count for r in reports)
            total_lines = sum(len(r.matches) for r in reports)
            
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Remittances", len(reports))
            c2.metric("Total Lines", total_lines)
            c3.metric("Matched ✅", total_matched)
            c4.metric("Mismatched ⚠️", total_mismatched)
            c5.metric("Not Found ❌", total_not_found)
            
            if total_lines:
                rate = total_matched / total_lines * 100
                st.metric("Match Rate", f"{rate:.1f}%")
            
            # Detailed reports
            for report in reports:
                agency = report.remittance.agency or report.remittance.subject[:40]
                with st.expander(
                    f"{'🟢' if report.not_found_count == 0 and report.mismatched_count == 0 else '🔴'} "
                    f"{agency} — {len(report.matches)} lines — "
                    f"✅{report.matched_count} ⚠️{report.mismatched_count} ❌{report.not_found_count}"
                ):
                    rows = []
                    for m in report.matches:
                        rows.append({
                            'NVC Code': m.nvc_code,
                            'Contractor': m.remittance_line.description[:30],
                            'Remit $': f"${float(m.remittance_amount):,.2f}",
                            'DB $': f"${m.db_amount:,.2f}" if m.db_amount is not None else '—',
                            'Diff': f"${m.difference:+,.2f}" if m.difference is not None else '',
                            'Status': m.status,
                            'Tenant': m.db_record.get('tenant', '').replace('.worksuite.com', '') if m.db_record else '',
                            'Notes': m.notes[:60],
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        
        except Exception as e:
            st.error(f"Reconciliation failed: {e}")
            import traceback
            st.code(traceback.format_exc())

# ═══════════════════════════════════════════════════════════════════════════
# TAB: History
# ═══════════════════════════════════════════════════════════════════════════
with tab_history:
    st.subheader("📜 Processed Email History")
    
    try:
        emails = get_all_emails(limit=100)
        stats = get_stats()
        
        if not emails:
            st.info("No emails processed yet. Run a reconciliation first.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Emails", stats['total_emails'])
            c2.metric("Total Value", f"${stats['total_value']:,.2f}")
            c3.metric("Matched", stats['matched'])
            c4.metric("Issues", stats['mismatched'] + stats['not_found'])
            
            st.divider()
            
            rows = []
            for e in emails:
                rows.append({
                    'Source': e.get('source', ''),
                    'Subject': (e.get('subject') or '')[:60],
                    'Date': (e.get('email_date') or '')[:25],
                    'Fetched': (e.get('fetched_at') or '')[:19],
                    'Attachments': e.get('attachment_count', 0),
                    'Matched': e.get('total_matched') or 0,
                    'Issues': (e.get('total_mismatched') or 0) + (e.get('total_not_found') or 0),
                    'Amount': f"${e.get('total_amount') or 0:,.2f}",
                    'Manual': '⚠️' if e.get('manual_review') else '',
                })
            
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
            
            # Source breakdown
            if stats.get('sources'):
                st.subheader("By Source")
                src_df = pd.DataFrame([
                    {'Source': k, 'Count': v} for k, v in stats['sources'].items()
                ])
                st.bar_chart(src_df.set_index('Source'))
    except Exception as e:
        st.error(f"Error loading history: {e}")

# ── Sidebar ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="padding:8px 0 16px 0;">
        <span style="font-family:'Archivo',sans-serif;font-weight:900;font-size:1.1rem;color:#000;letter-spacing:-0.02em;">Worksuite</span>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown('<p class="ws-section-label">Configuration</p>', unsafe_allow_html=True)
    st.markdown(f"**Gmail:** {os.getenv('GOOGLE_IMPERSONATE_USER', 'N/A')}")
    st.markdown(f"**DB:** {os.getenv('DB_NAME', 'N/A')}@{os.getenv('DB_HOST', 'N/A')[:20]}...")
    st.markdown(f"**Bastion:** {os.getenv('SSH_BASTION_HOST', 'N/A')}")
    
    st.divider()
    st.markdown('<p class="ws-section-label">Email Sources</p>', unsafe_allow_html=True)
    for key, src in EMAIL_SOURCES.items():
        st.markdown(f"**{key}:** {src['description']}")
    
    st.divider()
    st.markdown('<p class="ws-section-label">OMC Tenants</p>', unsafe_allow_html=True)
    for t in sorted(OMC_TENANTS):
        st.markdown(f"· {t.replace('.worksuite.com', '')}")
    
    st.divider()
    st.markdown("""
    <div style="padding:8px 0;text-align:center;">
        <span style="background:#FF4821;color:white;font-family:'Archivo',sans-serif;font-size:10px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;padding:4px 12px;border-radius:999px;">Read-Only Mode</span>
    </div>
    """, unsafe_allow_html=True)
    st.caption(f"v1.0 · {datetime.now().strftime('%Y-%m-%d')}")
