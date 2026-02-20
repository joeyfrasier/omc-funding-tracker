"use client";

import { useState, useEffect, useCallback } from "react";
import Header from "@/components/Header";
import Tabs from "@/components/Tabs";
import MetricCard from "@/components/MetricCard";
import StatusDot from "@/components/StatusDot";
import { api, OverviewData, EmailItem, PayRun, ConfigData, TenantInfo, MoneyCorpAccount, ReconRecord, CachedInvoice, ReceivedPayment } from "@/lib/api";
import { getInvoiceUrl } from "@/lib/worksuite-links";

const TAB_NAMES = ["Overview", "Workbench", "Invoices", "Remittances", "Payments", "Funding", "Pay Runs"];

/** Clickable NVC code that deep-links to Worksuite via Happy Place. */
function NvcLink({ nvcCode, tenant, className = "" }: { nvcCode: string; tenant?: string | null; className?: string }) {
  const url = tenant ? getInvoiceUrl(tenant, nvcCode) : null;
  if (url) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className={`font-mono text-sm font-medium text-[var(--color-ws-orange)] hover:underline ${className}`}
        title={`Open in Worksuite (${tenant})`}
      >
        {nvcCode}
        <svg className="inline-block w-3 h-3 ml-1 -mt-0.5 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
        </svg>
      </a>
    );
  }
  return <span className={`font-mono text-sm font-medium ${className}`}>{nvcCode}</span>;
}

function formatCurrency(n: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(n);
}

function formatCurrencyFull(n: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 }).format(n);
}

/* ── Overview Tab ────────────────────────────────────────────────────── */

/* ── Queue Tab — Primary Workspace ───────────────────────────────────── */

function QueueTab() {
  const [records, setRecords] = useState<ReconRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedRecord, setSelectedRecord] = useState<ReconRecord | null>(null);
  const [summary, setSummary] = useState<Record<string, number>>({});

  // Filters
  const [filterStatus, setFilterStatus] = useState("");
  const [filterTenant, setFilterTenant] = useState("");
  const [filterSearch, setFilterSearch] = useState("");
  const [filterInvStatus, setFilterInvStatus] = useState("");
  const [sortBy, setSortBy] = useState("last_updated_at");
  const [sortDir, setSortDir] = useState("desc");

  const loadQueue = useCallback(() => {
    setLoading(true);
    setError("");
    const params: Record<string, any> = {
      status: filterStatus || undefined,
      tenant: filterTenant || undefined,
      search: filterSearch || undefined,
      sort_by: sortBy,
      sort_dir: sortDir,
      limit: 200,
    };
    if (filterInvStatus) (params as any).invoice_status = filterInvStatus;
    api.reconQueue(params)
      .then((res) => { setRecords(res.records); setTotal(res.total); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [filterStatus, filterTenant, filterSearch, filterInvStatus, sortBy, sortDir]);

  useEffect(() => { loadQueue(); }, [loadQueue]);
  useEffect(() => {
    api.reconSummary().then(setSummary).catch(() => {});
  }, []);

  const statusLabel = (s: string) => {
    const map: Record<string, string> = {
      full_4way: "Full 4-Way ✅",
      "3way_awaiting_payment": "Awaiting Payment",
      "3way_no_funding": "No Funding Record",
      "2way_matched": "2-Way Matched",
      amount_mismatch: "Amount Mismatch",
      invoice_payment_only: "Missing Remittance",
      invoice_only: "Invoice Only",
      remittance_only: "Remittance Only",
      payment_only: "Payment Only",
      unmatched: "Unmatched",
      resolved: "Resolved",
    };
    return map[s] || s;
  };

  const statusColor = (s: string) => {
    if (s === "full_4way" || s === "resolved") return "text-[var(--color-ws-green)]";
    if (s === "amount_mismatch") return "text-red-600";
    if (s === "3way_awaiting_payment") return "text-blue-600";
    if (s === "3way_no_funding") return "text-amber-600";
    if (s === "2way_matched") return "text-yellow-600";
    return "text-[var(--color-ws-orange)]";
  };

  const hasLeg = (r: ReconRecord, leg: "remittance" | "invoice" | "funding" | "payment") => {
    if (leg === "remittance") return r.remittance_amount !== null;
    if (leg === "invoice") return r.invoice_amount !== null;
    if (leg === "funding") return r.received_payment_id !== null;
    return r.payment_amount !== null;
  };

  const formatAmt = (n: number | null) =>
    n !== null ? `$${n.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "—";

  // Summary pills
  const pills = [
    { key: "amount_mismatch", label: "Mismatch", count: summary.amount_mismatch || 0, color: "bg-red-100 text-red-700" },
    { key: "3way_no_funding", label: "No Funding", count: summary["3way_no_funding"] || 0, color: "bg-amber-100 text-amber-700" },
    { key: "3way_awaiting_payment", label: "Awaiting Pay", count: summary["3way_awaiting_payment"] || 0, color: "bg-blue-100 text-blue-700" },
    { key: "2way_matched", label: "2-Way", count: summary["2way_matched"] || 0, color: "bg-yellow-100 text-yellow-700" },
    { key: "invoice_only", label: "Invoice Only", count: summary.invoice_only || 0, color: "bg-orange-100 text-[var(--color-ws-orange)]" },
    { key: "invoice_payment_only", label: "No Remittance", count: summary.invoice_payment_only || 0, color: "bg-orange-100 text-[var(--color-ws-orange)]" },
    { key: "full_4way", label: "Full 4-Way", count: summary.full_4way || 0, color: "bg-green-100 text-[var(--color-ws-green)]" },
    { key: "", label: "All", count: summary.total || 0, color: "bg-gray-100 text-gray-700" },
  ];

  return (
    <div className="space-y-6">
      {/* Status pills */}
      <div className="flex gap-2 flex-wrap">
        {pills.map((p) => (
          <button
            key={p.key}
            onClick={() => setFilterStatus(filterStatus === p.key ? "" : p.key)}
            className={`px-3 py-1.5 rounded-full text-xs font-semibold transition-all ${
              filterStatus === p.key
                ? "ring-2 ring-[var(--color-ws-orange)] ring-offset-1"
                : ""
            } ${p.color}`}
          >
            {p.label} ({p.count})
          </button>
        ))}
      </div>

      {/* Filters row */}
      <div className="flex items-end gap-4 flex-wrap">
        <div className="flex-1 min-w-[200px]">
          <label className="section-label block mb-2">Search NVC Code</label>
          <input
            type="text"
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm w-full"
            placeholder="NVC7KYDFK3QF..."
            value={filterSearch}
            onChange={(e) => setFilterSearch(e.target.value)}
          />
        </div>
        <div>
          <label className="section-label block mb-2">Group</label>
          <select
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm"
            value={filterTenant}
            onChange={(e) => setFilterTenant(e.target.value)}
          >
            <option value="">All Groups</option>
            {["omcbbdo","omcflywheel","omcohg","omnicom","omnicombranding","omnicomddb","omnicommedia","omnicomoac","omnicomprecision","omnicomprg","omnicomtbwa"].map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="section-label block mb-2">Invoice Status</label>
          <select
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm"
            value={filterInvStatus}
            onChange={(e) => setFilterInvStatus(e.target.value)}
          >
            <option value="">All</option>
            <option value="Approved">Approved</option>
            <option value="Scheduled">Scheduled</option>
            <option value="Processing">Processing</option>
            <option value="In Flight">In Flight</option>
            <option value="Paid">Paid</option>
            <option value="New">New</option>
            <option value="Rejected">Rejected</option>
          </select>
        </div>
        <div>
          <label className="section-label block mb-2">Sort</label>
          <select
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm"
            value={`${sortBy}:${sortDir}`}
            onChange={(e) => { const [b, d] = e.target.value.split(":"); setSortBy(b); setSortDir(d); }}
          >
            <option value="last_updated_at:desc">Recently Updated</option>
            <option value="first_seen_at:asc">Oldest First</option>
            <option value="invoice_amount:desc">Highest Amount</option>
            <option value="invoice_amount:asc">Lowest Amount</option>
          </select>
        </div>
        {(filterStatus || filterTenant || filterSearch || filterInvStatus) && (
          <button
            className="text-sm text-[var(--color-ws-orange)] font-semibold hover:underline pb-2"
            onClick={() => { setFilterStatus(""); setFilterTenant(""); setFilterSearch(""); setFilterInvStatus(""); }}
          >
            Clear
          </button>
        )}
      </div>

      <p className="text-xs text-gray-400">
        {total} records{filterStatus || filterTenant || filterSearch ? " (filtered)" : ""}
      </p>

      {error && <ErrorBox message={error} />}
      {loading && <LoadingSkeleton rows={8} />}

      {!loading && records.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <table className="ws-table">
            <thead>
              <tr>
                <th>NVC Code</th>
                <th>Status</th>
                <th>Sources</th>
                <th>Group</th>
                <th>Inv. Status</th>
                <th>Amount</th>
                <th>Flag</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr
                  key={r.nvc_code}
                  className={`cursor-pointer ${selectedRecord?.nvc_code === r.nvc_code ? "bg-orange-50" : ""}`}
                  onClick={() => setSelectedRecord(r)}
                >
                  <td><NvcLink nvcCode={r.nvc_code} tenant={r.invoice_tenant} /></td>
                  <td>
                    <span className={`text-xs font-semibold ${statusColor(r.match_status)}`}>
                      {statusLabel(r.match_status)}
                    </span>
                  </td>
                  <td>
                    <div className="flex gap-1">
                      <span className={`inline-block w-2 h-2 rounded-full ${hasLeg(r, "remittance") ? "bg-[var(--color-ws-green)]" : "bg-gray-300"}`} title="Remittance" />
                      <span className={`inline-block w-2 h-2 rounded-full ${hasLeg(r, "invoice") ? "bg-[var(--color-ws-green)]" : "bg-gray-300"}`} title="Invoice" />
                      <span className={`inline-block w-2 h-2 rounded-full ${hasLeg(r, "funding") ? "bg-blue-500" : "bg-gray-300"}`} title="Funding (incoming)" />
                      <span className={`inline-block w-2 h-2 rounded-full ${hasLeg(r, "payment") ? "bg-[var(--color-ws-green)]" : "bg-gray-300"}`} title="Payment (outgoing)" />
                    </div>
                  </td>
                  <td className="text-sm text-gray-500">{r.invoice_tenant || "—"}</td>
                  <td>
                    {r.invoice_status ? (
                      <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                        r.invoice_status === "Processing" || r.invoice_status === "In Flight" ? "bg-orange-100 text-[var(--color-ws-orange)]" :
                        r.invoice_status === "Scheduled" ? "bg-blue-100 text-blue-700" :
                        r.invoice_status === "Paid" ? "bg-green-100 text-[var(--color-ws-green)]" :
                        r.invoice_status === "Approved" ? "bg-yellow-100 text-yellow-700" :
                        r.invoice_status === "Rejected" ? "bg-red-100 text-red-700" :
                        r.invoice_status === "New" ? "bg-gray-100 text-gray-500" :
                        "bg-gray-100 text-gray-600"
                      }`}>{r.invoice_status}</span>
                    ) : <span className="text-xs text-gray-300">—</span>}
                  </td>
                  <td className="text-sm font-medium">{formatAmt(r.invoice_amount || r.remittance_amount || r.payment_amount)}</td>
                  <td>
                    {r.flag ? (
                      <span
                        className={`inline-block w-5 h-5 rounded-full text-center text-[10px] leading-5 cursor-help ${
                          r.flag === "needs_outreach" ? "bg-red-100 text-red-600" :
                          r.flag === "investigating" ? "bg-amber-100 text-amber-600" :
                          r.flag === "escalated" ? "bg-purple-100 text-purple-600" :
                          r.flag === "resolved" ? "bg-green-100 text-[var(--color-ws-green)]" :
                          "bg-gray-100 text-gray-600"
                        }`}
                        title={`${r.flag.replace(/_/g, " ")}${r.flag_notes ? `: ${r.flag_notes}` : ""}`}
                      >
                        {r.flag === "needs_outreach" ? "!" : r.flag === "investigating" ? "?" : r.flag === "escalated" ? "↑" : "✓"}
                      </span>
                    ) : null}
                  </td>
                  <td className="text-xs text-gray-400 whitespace-nowrap">
                    {r.last_updated_at ? new Date(r.last_updated_at).toLocaleDateString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && records.length === 0 && !error && (
        <div className="py-12 text-center">
          <p className="text-lg font-semibold text-gray-400">
            {filterStatus === "full_3way" ? "All reconciled records shown above" : "No unreconciled records found"}
          </p>
          <p className="text-sm text-gray-300 mt-1">
            {filterStatus || filterTenant || filterSearch ? "Try adjusting your filters" : "Everything is reconciled!"}
          </p>
        </div>
      )}

      {/* Side Panel */}
      {selectedRecord && (
        <RecordDetailPanel
          record={selectedRecord}
          onClose={() => setSelectedRecord(null)}
          onUpdate={(updated) => {
            setSelectedRecord(updated);
            setRecords((prev) => prev.map((r) => r.nvc_code === updated.nvc_code ? updated : r));
          }}
        />
      )}
    </div>
  );
}

/* ── Record Detail / Cross-Search Panel ─────────────────────────────── */

function RecordDetailPanel({
  record,
  onClose,
  onUpdate,
}: {
  record: ReconRecord;
  onClose: () => void;
  onUpdate: (r: ReconRecord) => void;
}) {
  const [searchSource, setSearchSource] = useState<"invoices" | "funding" | "emails">(
    !record.invoice_amount ? "invoices" : !record.payment_amount ? "funding" : "emails"
  );
  const [searchQuery, setSearchQuery] = useState(record.nvc_code);
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [searching, setSearching] = useState(false);
  const [flagNote, setFlagNote] = useState("");
  const [showFlagInput, setShowFlagInput] = useState(false);

  const doSearch = () => {
    setSearching(true);
    api.crossSearch({
      q: searchQuery,
      source: searchSource,
      amount_min: (record.invoice_amount || record.remittance_amount || record.payment_amount || 0) * 0.95,
      amount_max: (record.invoice_amount || record.remittance_amount || record.payment_amount || 0) * 1.05,
      tenant: record.invoice_tenant || undefined,
      limit: 10,
    })
      .then((res) => setSearchResults(res.results))
      .catch(() => setSearchResults([]))
      .finally(() => setSearching(false));
  };

  const handleAssociate = (targetNvc: string) => {
    api.reconAssociate({
      nvc_code: record.nvc_code,
      associate_with: targetNvc,
      source: searchSource,
    })
      .then((res) => {
        if (res.record) onUpdate(res.record);
      })
      .catch((e) => alert(`Association failed: ${e.message}`));
  };

  const handleFlag = (flag: string) => {
    api.reconFlag({ nvc_code: record.nvc_code, flag, notes: flagNote })
      .then(() => {
        onUpdate({ ...record, flag, flag_notes: flagNote });
        setShowFlagInput(false);
        setFlagNote("");
      })
      .catch((e) => alert(`Flag failed: ${e.message}`));
  };

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const formatAmt = (n: number | null) =>
    n !== null ? `$${n.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "—";

  const hasRemittance = record.remittance_amount !== null;
  const hasInvoice = record.invoice_amount !== null;
  const hasFunding = record.received_payment_id !== null;
  const hasPayment = record.payment_amount !== null;

  const statusLabels: Record<string, string> = {
    full_4way: "Full 4-Way Match ✅",
    "3way_awaiting_payment": "3-Way — Awaiting Payment",
    "3way_no_funding": "3-Way — No Funding Record",
    "2way_matched": "2-Way Matched",
    amount_mismatch: "Amount Mismatch",
    invoice_payment_only: "Invoice + Payment Only",
    invoice_only: "Invoice Only",
    remittance_only: "Remittance Only",
    payment_only: "Payment Only",
    unmatched: "Unmatched",
    resolved: "Resolved",
  };

  const statusColorClass = 
    record.match_status === "full_4way" || record.match_status === "resolved" ? "text-[var(--color-ws-green)]" :
    record.match_status === "amount_mismatch" ? "text-red-600" :
    record.match_status === "3way_awaiting_payment" ? "text-blue-600" :
    record.match_status === "3way_no_funding" ? "text-amber-600" :
    "text-[var(--color-ws-orange)]";

  return (
    <div className="fixed inset-0 bg-black/30 z-50 flex justify-end" onClick={onClose}>
      <div
        className="bg-white w-full max-w-xl h-full overflow-auto shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 bg-white border-b border-[var(--color-ws-gray)] p-6 flex items-center justify-between z-10">
          <div>
            <h2 className="text-lg font-bold"><NvcLink nvcCode={record.nvc_code} tenant={record.invoice_tenant} /></h2>
            <p className={`text-sm font-semibold ${statusColorClass}`}>
              {statusLabels[record.match_status] || record.match_status}
            </p>
          </div>
          <button
            className="w-8 h-8 rounded-full hover:bg-gray-100 flex items-center justify-center text-gray-400"
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <div className="p-6 space-y-6">
          {/* Four Sources */}
          <div className="space-y-3">
            <p className="section-label">Sources (4-Way Match)</p>

            {/* Leg 1: Remittance */}
            <div className={`rounded-xl p-4 border ${hasRemittance ? "border-[var(--color-ws-green)] bg-green-50/30" : "border-dashed border-gray-300 bg-gray-50"}`}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">① Remittance (Email)</span>
                <span className={`w-2 h-2 rounded-full ${hasRemittance ? "bg-[var(--color-ws-green)]" : "bg-gray-300"}`} />
              </div>
              {hasRemittance ? (
                <div className="space-y-1">
                  <p className="text-lg font-bold">{formatAmt(record.remittance_amount)}</p>
                  <p className="text-xs text-gray-500">Source: {record.remittance_source} · Date: {record.remittance_date || "—"}</p>
                </div>
              ) : (
                <p className="text-sm text-gray-400 italic">No remittance email found</p>
              )}
            </div>

            {/* Leg 2: Invoice */}
            <div className={`rounded-xl p-4 border ${hasInvoice ? "border-[var(--color-ws-green)] bg-green-50/30" : "border-dashed border-gray-300 bg-gray-50"}`}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">② Invoice (Worksuite)</span>
                <span className={`w-2 h-2 rounded-full ${hasInvoice ? "bg-[var(--color-ws-green)]" : "bg-gray-300"}`} />
              </div>
              {hasInvoice ? (
                <div className="space-y-1">
                  <p className="text-lg font-bold">{formatAmt(record.invoice_amount)}</p>
                  <p className="text-xs text-gray-500">
                    Group: {record.invoice_tenant || "—"} · Status: {record.invoice_status || "—"} · Pay Run: {record.invoice_payrun_ref || "—"}
                  </p>
                </div>
              ) : (
                <p className="text-sm text-gray-400 italic">No Worksuite invoice found</p>
              )}
            </div>

            {/* Leg 3: Funding (Incoming) */}
            <div className={`rounded-xl p-4 border ${hasFunding ? "border-blue-400 bg-blue-50/30" : "border-dashed border-gray-300 bg-gray-50"}`}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">③ Funding — Incoming (MoneyCorp)</span>
                <span className={`w-2 h-2 rounded-full ${hasFunding ? "bg-blue-500" : "bg-gray-300"}`} />
              </div>
              {hasFunding ? (
                <div className="space-y-1">
                  <p className="text-lg font-bold">{formatAmt(record.received_payment_amount)}</p>
                  <p className="text-xs text-gray-500">
                    Received Payment ID: {record.received_payment_id || "—"} · Date: {record.received_payment_date || "—"}
                  </p>
                </div>
              ) : (
                <p className="text-sm text-gray-400 italic">No inbound funding record</p>
              )}
            </div>

            {/* Leg 4: Payment (Outgoing) */}
            <div className={`rounded-xl p-4 border ${hasPayment ? "border-[var(--color-ws-green)] bg-green-50/30" : "border-dashed border-gray-300 bg-gray-50"}`}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">④ Payment — Outgoing (MoneyCorp)</span>
                <span className={`w-2 h-2 rounded-full ${hasPayment ? "bg-[var(--color-ws-green)]" : "bg-gray-300"}`} />
              </div>
              {hasPayment ? (
                <div className="space-y-1">
                  <p className="text-lg font-bold">
                    {formatAmt(record.payment_amount)}
                    {record.payment_currency && record.payment_currency !== "USD" && (
                      <span className="ml-2 text-xs font-normal text-gray-500">{record.payment_currency}</span>
                    )}
                  </p>
                  <p className="text-xs text-gray-500">
                    Account: {record.payment_account_id || "—"} · Date: {record.payment_date || "—"}
                    {record.payment_status && ` · Status: ${record.payment_status}`}
                  </p>
                  {record.payment_recipient && (
                    <p className="text-xs text-gray-400">
                      Recipient: {record.payment_recipient}
                      {record.payment_recipient_country && ` (${record.payment_recipient_country})`}
                    </p>
                  )}
                </div>
              ) : (
                <p className="text-sm text-gray-400 italic">No outbound payment found</p>
              )}
            </div>
          </div>

          {/* Amount Mismatch Detail */}
          {record.match_status === "amount_mismatch" && (
            <div className="rounded-xl bg-red-50 p-4 border border-red-200">
              <p className="text-xs font-semibold text-red-700 mb-2">Amount Mismatch Details</p>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div><span className="text-gray-500">Remittance:</span> {formatAmt(record.remittance_amount)}</div>
                <div><span className="text-gray-500">Invoice:</span> {formatAmt(record.invoice_amount)}</div>
                <div><span className="text-gray-500">Funding (in):</span> {formatAmt(record.received_payment_amount)}</div>
                <div><span className="text-gray-500">Payment (out):</span> {formatAmt(record.payment_amount)}</div>
              </div>
            </div>
          )}

          {/* Cross-Search — only show if actually missing a source */}
          {(!hasRemittance || !hasInvoice || !hasPayment) && (
            <div>
              <p className="section-label mb-3">Find Missing Source</p>
              <div className="flex gap-2 mb-3">
                {(["invoices", "funding", "emails"] as const).map((s) => (
                  <button
                    key={s}
                    className={`px-3 py-1.5 rounded-full text-xs font-semibold ${
                      searchSource === s ? "bg-[var(--color-ws-orange)] text-white" : "bg-gray-100 text-gray-600"
                    }`}
                    onClick={() => setSearchSource(s)}
                  >
                    {s === "invoices" ? "Worksuite" : s === "funding" ? "MoneyCorp" : "Emails"}
                  </button>
                ))}
              </div>
              <div className="flex gap-2">
                <input
                  type="text"
                  className="flex-1 border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm"
                  placeholder="Search NVC code, amount, description..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && doSearch()}
                />
                <button className="btn btn-black text-sm" onClick={doSearch} disabled={searching}>
                  {searching ? "..." : "Search"}
                </button>
              </div>

              {searchResults.length > 0 && (
                <div className="mt-3 space-y-2">
                  {searchResults.map((r: any, i: number) => (
                    <div key={i} className="flex items-center justify-between p-3 rounded-lg border border-[var(--color-ws-gray)] hover:border-[var(--color-ws-orange)] transition-colors">
                      <div>
                        <p className="text-sm font-mono font-medium">{r.nvc_code || r.reference || r.subject || "—"}</p>
                        <p className="text-xs text-gray-500">
                          {r.invoice_tenant || r.tenant || ""} · {formatAmt(r.invoice_amount || r.amount || r.total_amount || null)}
                          {r.invoice_status ? ` · ${r.invoice_status}` : ""}
                        </p>
                      </div>
                      <button
                        className="btn btn-outline text-xs"
                        onClick={() => handleAssociate(r.nvc_code || r.reference)}
                      >
                        Associate
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {searchResults.length === 0 && !searching && searchQuery && (
                <p className="text-xs text-gray-400 mt-2">No results. Try a different search term or source.</p>
              )}
            </div>
          )}

          {/* Actions */}
          <div className="border-t border-[var(--color-ws-gray)] pt-4">
            <p className="section-label mb-3">Actions</p>
            <div className="flex flex-wrap gap-2">
              {!showFlagInput ? (
                <>
                  <button
                    className="btn btn-outline text-xs"
                    onClick={() => setShowFlagInput(true)}
                  >
                    Flag for Follow-up
                  </button>
                  {record.match_status !== "resolved" && (
                    <button
                      className="btn btn-outline text-xs"
                      onClick={() => handleFlag("resolved")}
                    >
                      Mark Resolved
                    </button>
                  )}
                </>
              ) : (
                <div className="w-full space-y-2">
                  <textarea
                    className="w-full border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm"
                    placeholder="Add a note (e.g., 'Emailed omcbbdo finance team 2/9')"
                    rows={2}
                    value={flagNote}
                    onChange={(e) => setFlagNote(e.target.value)}
                  />
                  <div className="flex gap-2">
                    <button className="btn btn-black text-xs" onClick={() => handleFlag("needs_outreach")}>
                      Needs Outreach
                    </button>
                    <button className="btn btn-outline text-xs" onClick={() => handleFlag("investigating")}>
                      Investigating
                    </button>
                    <button className="btn btn-outline text-xs" onClick={() => handleFlag("escalated")}>
                      Escalated
                    </button>
                    <button className="text-xs text-gray-400 hover:underline" onClick={() => setShowFlagInput(false)}>
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>

            {record.flag && (
              <div className="mt-3 p-3 rounded-lg bg-amber-50 border border-amber-200">
                <p className="text-xs font-semibold text-amber-700">
                  Flagged: {record.flag.replace(/_/g, " ")}
                </p>
                {record.flag_notes && <p className="text-xs text-amber-600 mt-1">{record.flag_notes}</p>}
              </div>
            )}

            {record.notes && (
              <div className="mt-3 p-3 rounded-lg bg-gray-50 border border-[var(--color-ws-gray)]">
                <p className="text-xs text-gray-500">{record.notes}</p>
              </div>
            )}
          </div>

          {/* Metadata */}
          <div className="border-t border-[var(--color-ws-gray)] pt-4 text-xs text-gray-400 space-y-1">
            <p>First seen: {record.first_seen_at ? new Date(record.first_seen_at).toLocaleString() : "—"}</p>
            <p>Last updated: {record.last_updated_at ? new Date(record.last_updated_at).toLocaleString() : "—"}</p>
            {record.resolved_at && <p>Resolved: {new Date(record.resolved_at).toLocaleString()} by {record.resolved_by || "—"}</p>}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Overview Tab ────────────────────────────────────────────────────── */

function OverviewTab() {
  const [data, setData] = useState<OverviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadData = useCallback(() => {
    setLoading(true);
    setError("");
    api.overview(7)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  if (loading) return <LoadingSkeleton rows={4} />;

  // If we got an error but no data at all, show error with retry
  if (error && !data) {
    return (
      <div className="space-y-4">
        <ErrorBox message={error} />
        <button className="btn btn-outline" onClick={loadData}>Retry</button>
      </div>
    );
  }

  // Render with whatever data we have (may be partial)
  const hasDbError = data?.errors?.db;
  const hasGmailError = data?.errors?.gmail;

  return (
    <div className="space-y-8">
      {/* Service status banner when degraded */}
      {(hasDbError || hasGmailError) && (
        <div className="card bg-amber-50 border-amber-200">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <StatusDot status="warn" />
              <div>
                <p className="text-sm font-semibold text-amber-900">Running in degraded mode</p>
                <p className="text-xs text-amber-700">
                  {hasDbError && "Worksuite unreachable (SSH tunnel timeout). "}
                  {hasGmailError && "PayOps Email unavailable. "}
                  Showing cached/local data only.
                </p>
              </div>
            </div>
            <button className="btn btn-outline text-xs" onClick={loadData}>Retry</button>
          </div>
        </div>
      )}

      <div>
        <p className="section-label mb-4">Reconciliation Health</p>
        <div className="grid grid-cols-4 gap-4">
          <MetricCard
            label="3-Way Match Rate"
            value={data ? `${data.match_rate}%` : "—"}
            delta={data && data.match_rate_2way > 0 ? `${data.match_rate_2way}% 2-way verified` : undefined}
            deltaColor={data && data.match_rate > 0 ? "green" : "gray"}
          />
          <MetricCard
            label="Fully Reconciled"
            value={data?.matched_3way ?? "—"}
            delta={data ? `of ${data.matchable_total || data.total_lines} matchable records` : undefined}
            deltaColor="green"
          />
          <MetricCard
            label="Unreconciled"
            value={data ? data.unverified : "—"}
            delta={data && data.unverified > 0 ? `${data.mismatched} amount mismatch · ${data.not_found} missing source` : undefined}
            deltaColor="red"
          />
          <MetricCard
            label="Remittance Value (Parsed)"
            value={data ? formatCurrency(data.total_value) : "—"}
            delta="Total from processed email CSVs"
            deltaColor="gray"
          />
        </div>
        {data && ((data.excluded_prematch || 0) > 0 || (data.excluded_terminal || 0) > 0) && (
          <div className="flex items-center gap-4 mt-3 text-xs text-gray-400">
            <span>{data.total_lines} total records</span>
            {(data.excluded_prematch || 0) > 0 && <span>{data.excluded_prematch} New (pre-match)</span>}
            {(data.excluded_terminal || 0) > 0 && <span>{data.excluded_terminal} Rejected</span>}
            {(data.anomaly_rejected_with_funding || 0) > 0 && (
              <span className="text-red-500 font-medium">{data.anomaly_rejected_with_funding} rejected with funding</span>
            )}
          </div>
        )}
      </div>

      <div className="grid grid-cols-5 gap-8">
        <div className="col-span-3">
          <p className="section-label mb-4">By Group — Reconciliation Status</p>
          {data && data.agencies.length > 0 ? (
            <div className="space-y-1">
              <div className="flex items-center justify-between py-1 px-3 text-xs text-gray-400">
                <span className="w-36">Group</span>
                <span className="w-20 text-right">Matchable</span>
                <span className="w-24 text-right">Total</span>
                <span className="w-24 text-right">Reconciled</span>
                <span className="w-24 text-right">Unreconciled</span>
                <span className="w-20 text-right">Anomalies</span>
              </div>
              {data.agencies.slice(0, 12).map((a: any) => {
                const unrecon = a.unreconciled_count ?? 0;
                const anomalies = a.anomaly_count ?? 0;
                return (
                  <div key={a.name} className="flex items-center justify-between py-2 px-3 rounded-lg hover:bg-gray-50">
                    <span className="font-semibold text-sm w-36 truncate">{a.name}</span>
                    <span className="text-sm text-gray-500 w-20 text-right">{a.matchable_count ?? a.count}</span>
                    <span className="text-sm font-medium w-24 text-right">{formatCurrency(a.total)}</span>
                    <span className={`text-sm w-24 text-right ${(a.reconciled_count || 0) > 0 ? 'text-[var(--color-ws-green)] font-medium' : 'text-gray-400'}`}>
                      {a.reconciled_count ?? '—'}
                    </span>
                    <span className={`text-sm w-24 text-right ${unrecon > 0 ? 'text-[var(--color-ws-orange)] font-medium' : 'text-gray-400'}`}>
                      {unrecon > 0 ? unrecon : '—'}
                    </span>
                    <span className={`text-sm w-20 text-right ${anomalies > 0 ? 'text-red-600 font-medium' : 'text-gray-300'}`}>
                      {anomalies > 0 ? anomalies : '—'}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : hasDbError ? (
            <div className="py-6 text-center">
              <p className="text-sm text-gray-400 mb-1">Requires database connection</p>
              <p className="text-xs text-gray-300">Connect to VPN and retry</p>
            </div>
          ) : (
            <p className="text-sm text-gray-400">No payment data available</p>
          )}
        </div>

        <div className="col-span-2">
          <p className="section-label mb-4">Data Sources</p>
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <StatusDot status={hasDbError ? "error" : "ok"} />
              <span className="text-sm">
                {hasDbError
                  ? "Worksuite: Unreachable"
                  : `Worksuite: ${data?.payments_count ?? 0} invoices (7d)`}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <StatusDot status={hasGmailError ? "error" : "ok"} />
              <span className="text-sm">
                {hasGmailError
                  ? "PayOps Email: Unavailable"
                  : `PayOps Email: ${data?.processed_count ?? 0} emails · ${data?.total_remittances ?? 0} remittances`}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <StatusDot status={data?.sync?.funding === 'ok' ? 'ok' : 'warn'} />
              <span className="text-sm">MoneyCorp: {data?.sync?.funding === 'ok' ? `${data.funding_count || 0} payments synced` : 'Token needs refresh'}</span>
            </div>
          </div>

          <div className="mt-6">
            <p className="section-label mb-3">Sync Schedule</p>
            <p className="text-sm text-gray-500">All sources sync every 5 minutes.</p>
            <p className="text-xs text-gray-400 mt-1">Last sync: {data?.sync?.emails === 'ok' ? 'OK' : 'Pending'}</p>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Funding Emails Tab ──────────────────────────────────────────────── */

function FundingEmailsTab() {
  const [emails, setEmails] = useState<EmailItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [filterSource, setFilterSource] = useState("");
  const [sortField, setSortField] = useState<"date" | "subject" | "source">("date");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selectedEmail, setSelectedEmail] = useState<EmailItem | null>(null);

  // Load from local DB first (fast), then try live fetch
  useEffect(() => {
    // Try local processed emails first (always available)
    api.processedEmails(200)
      .then((res) => {
        if (res.emails.length > 0) {
          const mapped: EmailItem[] = res.emails.map((e: any) => {
            let atts: string[] = [];
            if (typeof e.attachments === 'string' && e.attachments) {
              try { atts = JSON.parse(e.attachments).map((a: any) => a.filename || a); } catch {}
            } else if (Array.isArray(e.attachments)) {
              atts = e.attachments.map((a: any) => typeof a === 'string' ? a : a.filename);
            }
            return {
              id: e.id || e.email_id || "",
              source: e.source || "",
              date: e.email_date || e.fetched_at || "",
              subject: e.subject || "",
              from: e.sender || "",
              attachments: atts,
              manual_review: e.manual_review || false,
              _raw: e,
            };
          });
          setEmails(mapped);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // Filter + sort
  const filtered = emails
    .filter((e) => {
      if (filterSource && e.source !== filterSource) return false;
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        return (
          e.subject.toLowerCase().includes(q) ||
          e.from.toLowerCase().includes(q) ||
          e.attachments.some((a) => a.toLowerCase().includes(q)) ||
          e.id.toLowerCase().includes(q)
        );
      }
      return true;
    })
    .sort((a, b) => {
      const mul = sortDir === "asc" ? 1 : -1;
      if (sortField === "date") return mul * a.date.localeCompare(b.date);
      if (sortField === "subject") return mul * a.subject.localeCompare(b.subject);
      return mul * a.source.localeCompare(b.source);
    });

  const toggleSort = (field: typeof sortField) => {
    if (sortField === field) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortField(field); setSortDir("desc"); }
  };

  const SortIcon = ({ field }: { field: typeof sortField }) => (
    <span className="ml-1 text-[10px]">{sortField === field ? (sortDir === "asc" ? "↑" : "↓") : "↕"}</span>
  );

  if (loading) return <LoadingSkeleton rows={6} />;
  if (error) return <ErrorBox message={error} />;

  return (
    <div className="space-y-6">
      {/* Search + Filters */}
      <div className="flex items-end gap-4 flex-wrap">
        <div className="flex-1 min-w-[200px]">
          <label className="section-label block mb-2">Search</label>
          <input
            type="text"
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm w-full"
            placeholder="Search subject, sender, attachments…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
        <div>
          <label className="section-label block mb-2">Source</label>
          <select
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm"
            value={filterSource}
            onChange={(e) => setFilterSource(e.target.value)}
          >
            <option value="">All Sources</option>
            <option value="oasys">OASYS</option>
            <option value="d365_ach">D365 ACH</option>
            <option value="ldn_gss">LDN GSS</option>
          </select>
        </div>
        {(searchQuery || filterSource) && (
          <button
            className="text-sm text-[var(--color-ws-orange)] font-semibold hover:underline pb-2"
            onClick={() => { setSearchQuery(""); setFilterSource(""); }}
          >
            Clear
          </button>
        )}
      </div>

      <p className="text-xs text-gray-400">
        {filtered.length} of {emails.length} emails
        {filtered.length !== emails.length && " (filtered)"}
        {" · Auto-synced every 5 minutes"}
      </p>

      {filtered.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <table className="ws-table">
            <thead>
              <tr>
                <th className="cursor-pointer select-none" onClick={() => toggleSort("source")}>
                  Source <SortIcon field="source" />
                </th>
                <th className="cursor-pointer select-none" onClick={() => toggleSort("date")}>
                  Date <SortIcon field="date" />
                </th>
                <th className="cursor-pointer select-none" onClick={() => toggleSort("subject")}>
                  Subject <SortIcon field="subject" />
                </th>
                <th>From</th>
                <th>Attachments</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e) => (
                <tr
                  key={e.id}
                  className="cursor-pointer"
                  onClick={() => setSelectedEmail(e)}
                >
                  <td><span className="badge badge-gray">{e.source}</span></td>
                  <td className="text-sm text-gray-500 whitespace-nowrap">{e.date}</td>
                  <td className="text-sm max-w-md truncate">{e.subject}</td>
                  <td className="text-sm text-gray-500 max-w-[200px] truncate">{e.from}</td>
                  <td className="text-sm text-gray-500">{e.attachments.join(", ") || "—"}</td>
                  <td>{e.manual_review && <span className="badge badge-orange">Manual</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Email Detail Modal */}
      {selectedEmail && (
        <EmailDetailModal email={selectedEmail} onClose={() => setSelectedEmail(null)} />
      )}
    </div>
  );
}

function EmailDetailModal({ email, onClose }: { email: EmailItem; onClose: () => void }) {
  const [showAttachment, setShowAttachment] = useState(false);
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") { if (showAttachment) setShowAttachment(false); else onClose(); } };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose, showAttachment]);

  const loadDetail = () => {
    if (detail) { setShowAttachment(true); return; }
    setDetailLoading(true);
    setShowAttachment(true);
    api.emailDetail(email.id)
      .then(setDetail)
      .catch(() => setDetail({ error: true }))
      .finally(() => setDetailLoading(false));
  };

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-6" onClick={onClose}>
      <div
        className={`bg-white rounded-2xl shadow-xl flex transition-all duration-200 max-h-[85vh] ${showAttachment ? "max-w-6xl" : "max-w-3xl"} w-full`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Left: Email info */}
        <div className={`${showAttachment ? "w-1/2 border-r border-[var(--color-ws-gray)]" : "w-full"} overflow-auto flex flex-col`}>
          {/* Header */}
          <div className="flex items-center justify-between p-6 border-b border-[var(--color-ws-gray)]">
            <div className="flex items-center gap-3">
              <span className="badge badge-gray">{email.source}</span>
              {email.manual_review && <span className="badge badge-orange">Manual Review</span>}
            </div>
            <button
              className="w-8 h-8 rounded-full hover:bg-gray-100 flex items-center justify-center text-gray-400"
              onClick={onClose}
            >
              ✕
            </button>
          </div>

          {/* Body */}
          <div className="p-6 space-y-6 flex-1 overflow-auto">
            <div>
              <h2 className="text-lg font-bold mb-1">{email.subject}</h2>
              <div className="flex items-center gap-4 text-sm text-gray-500">
                <span>From: {email.from}</span>
                <span>·</span>
                <span>{email.date}</span>
              </div>
            </div>

            <div>
              <p className="section-label mb-2">Email ID</p>
              <code className="text-xs bg-gray-100 px-2 py-1 rounded font-mono">{email.id}</code>
            </div>

            {email.attachments.length > 0 && (
              <div>
                <p className="section-label mb-2">Attachments ({email.attachments.length})</p>
                <div className="space-y-1">
                  {email.attachments.map((a, i) => (
                    <button
                      key={i}
                      className="flex items-center gap-2 text-sm hover:text-[var(--color-ws-orange)] transition-colors w-full text-left group"
                      onClick={loadDetail}
                    >
                      <svg className="w-4 h-4 text-gray-400 group-hover:text-[var(--color-ws-orange)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
                      </svg>
                      <span className="group-hover:underline">{a}</span>
                      <svg className="w-3 h-3 text-gray-300 group-hover:text-[var(--color-ws-orange)] ml-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {(email as any)._raw?.remittance_count > 0 && (
              <div className="border-t border-[var(--color-ws-gray)] pt-4">
                <p className="section-label mb-2">Parsed Remittance Data</p>
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <p className="text-xs text-gray-400">Remittances</p>
                    <p className="text-lg font-bold">{(email as any)._raw.remittance_count}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-400">Matched</p>
                    <p className="text-lg font-bold text-[var(--color-ws-green)]">{(email as any)._raw.total_matched || 0}</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-400">Total Amount</p>
                    <p className="text-lg font-bold">${((email as any)._raw.total_amount || 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}</p>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-3 p-6 border-t border-[var(--color-ws-gray)]">
            {!showAttachment && email.attachments.length > 0 && (
              <button className="btn btn-outline text-sm" onClick={loadDetail}>View Attachment Data</button>
            )}
            <button className="btn btn-outline" onClick={onClose}>Close</button>
          </div>
        </div>

        {/* Right: Attachment content panel */}
        {showAttachment && (
          <div className="w-1/2 overflow-auto flex flex-col">
            <div className="flex items-center justify-between p-6 border-b border-[var(--color-ws-gray)]">
              <p className="section-label">Parsed Attachment Content</p>
              <button
                className="w-8 h-8 rounded-full hover:bg-gray-100 flex items-center justify-center text-gray-400"
                onClick={() => setShowAttachment(false)}
              >
                ✕
              </button>
            </div>
            <div className="p-6 flex-1 overflow-auto">
              {detailLoading && <LoadingSkeleton rows={4} />}
              {detail?.error && <p className="text-sm text-gray-400">Could not load attachment details.</p>}
              {detail && !detail.error && (
                <div className="space-y-6">
                  {(detail.remittances || []).length === 0 && (
                    <p className="text-sm text-gray-400">No parsed remittance data for this email.</p>
                  )}
                  {(detail.remittances || []).map((rem: any, ri: number) => (
                    <div key={ri}>
                      <div className="flex items-center justify-between mb-3">
                        <div>
                          <p className="text-sm font-semibold">{rem.agency || "Remittance"}</p>
                          <p className="text-xs text-gray-400">
                            {rem.line_count || 0} lines · ${(rem.payment_amount || 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
                          </p>
                        </div>
                        <div className="flex gap-2">
                          {rem.matched_count > 0 && <span className="badge badge-green">{rem.matched_count} matched</span>}
                          {(rem.mismatched_count || 0) > 0 && <span className="badge badge-orange">{rem.mismatched_count} mismatch</span>}
                          {(rem.not_found_count || 0) > 0 && <span className="badge badge-lavender">{rem.not_found_count} missing</span>}
                        </div>
                      </div>
                      {(rem.matches || []).length > 0 && (
                        <div className="card p-0 overflow-hidden">
                          <table className="ws-table text-xs">
                            <thead>
                              <tr>
                                <th>NVC Code</th>
                                <th>Description</th>
                                <th>Remit $</th>
                                <th>DB $</th>
                                <th>Status</th>
                              </tr>
                            </thead>
                            <tbody>
                              {rem.matches.map((m: any, mi: number) => (
                                <tr key={mi}>
                                  <td><NvcLink nvcCode={m.nvc_code} tenant={m.tenant} /></td>
                                  <td className="max-w-[160px] truncate">{m.description || m.company || "—"}</td>
                                  <td>${(m.remittance_amount || 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}</td>
                                  <td>{m.db_amount != null ? `$${m.db_amount.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "—"}</td>
                                  <td>
                                    <span className={`badge ${m.status === "matched" ? "badge-green" : m.status === "not_found" ? "badge-lavender" : "badge-orange"}`}>
                                      {m.status}
                                    </span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Pay Runs Tab ────────────────────────────────────────────────────── */

function PayRunsTab() {
  const [payruns, setPayruns] = useState<PayRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterTenant, setFilterTenant] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterMinAmount, setFilterMinAmount] = useState("");
  const [nvcInput, setNvcInput] = useState("");
  const [lookupResults, setLookupResults] = useState<any>(null);
  const [error, setError] = useState("");

  const loadPayruns = useCallback(() => {
    setLoading(true);
    setError("");
    api.cachedPayruns({ limit: 500 })
      .then((res) => setPayruns(res.payruns))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const lookupNVC = useCallback(() => {
    if (!nvcInput.trim()) return;
    api.lookupNVC(nvcInput)
      .then(setLookupResults)
      .catch((e) => setError(e.message));
  }, [nvcInput]);

  // Apply filters
  const filtered = payruns.filter((p) => {
    const tenant = (p.tenant || "").replace(".worksuite.com", "");
    if (filterTenant && !tenant.toLowerCase().includes(filterTenant.toLowerCase())) return false;
    if (filterStatus && String(p.status) !== filterStatus) return false;
    if (filterMinAmount && (p.total_amount || 0) < Number(filterMinAmount)) return false;
    return true;
  });

  const totalValue = filtered.reduce((s, p) => s + (p.total_amount || 0), 0);
  const totalPayments = filtered.reduce((s, p) => s + p.payment_count, 0);

  // Unique tenants/statuses for filter dropdowns
  const tenantOptions = [...new Set(payruns.map((p) => (p.tenant || "").replace(".worksuite.com", "")))].sort();
  const statusOptions = [...new Set(payruns.map((p) => String(p.status)))].sort();

  useEffect(() => { loadPayruns(); }, [loadPayruns]);

  return (
    <div className="space-y-6">
      {error && <ErrorBox message={error} />}
      {loading && <LoadingSkeleton rows={6} />}

      {!loading && payruns.length > 0 && (
        <>
          {/* Filters */}
          <div className="card bg-gray-50 border-gray-200">
            <p className="section-label mb-3">Filters</p>
            <div className="flex items-end gap-4 flex-wrap">
              <div>
                <label className="text-xs text-gray-500 block mb-1">Tenant</label>
                <select
                  className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm"
                  value={filterTenant}
                  onChange={(e) => setFilterTenant(e.target.value)}
                >
                  <option value="">All Tenants</option>
                  {tenantOptions.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">Status</label>
                <select
                  className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm"
                  value={filterStatus}
                  onChange={(e) => setFilterStatus(e.target.value)}
                >
                  <option value="">All Statuses</option>
                  {statusOptions.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">Min Amount</label>
                <input
                  type="number"
                  className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm w-32"
                  placeholder="$0"
                  value={filterMinAmount}
                  onChange={(e) => setFilterMinAmount(e.target.value)}
                />
              </div>
              {(filterTenant || filterStatus || filterMinAmount) && (
                <button
                  className="text-sm text-[var(--color-ws-orange)] font-semibold hover:underline"
                  onClick={() => { setFilterTenant(""); setFilterStatus(""); setFilterMinAmount(""); }}
                >
                  Clear filters
                </button>
              )}
            </div>
            {filtered.length !== payruns.length && (
              <p className="text-xs text-gray-400 mt-2">Showing {filtered.length} of {payruns.length} pay runs</p>
            )}
          </div>

          <div className="grid grid-cols-3 gap-4">
            <MetricCard label="Pay Runs" value={filtered.length} delta={filtered.length !== payruns.length ? `of ${payruns.length} total` : undefined} />
            <MetricCard label="Total Value" value={formatCurrencyFull(totalValue)} />
            <MetricCard label="Total Payments" value={totalPayments} />
          </div>

          <div className="card p-0 overflow-hidden">
            <table className="ws-table">
              <thead>
                <tr>
                  <th>Reference</th>
                  <th>Tenant</th>
                  <th>Status</th>
                  <th>Payments</th>
                  <th>Total</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((p, i) => (
                  <tr key={i}>
                    <td className="text-sm font-medium">{p.reference}</td>
                    <td className="text-sm">{(p.tenant || "").replace(".worksuite.com", "")}</td>
                    <td><span className="badge badge-gray">{p.status}</span></td>
                    <td className="text-sm">{p.payment_count}</td>
                    <td className="text-sm font-medium">{formatCurrencyFull(p.total_amount || 0)}</td>
                    <td className="text-sm text-gray-500">{p.created_at?.slice(0, 16)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div className="border-t border-[var(--color-ws-gray)] pt-6">
        <p className="section-label mb-4">Payment Lookup</p>
        <div className="flex items-end gap-4">
          <div className="flex-1">
            <input
              type="text"
              className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm w-full"
              placeholder="NVC7KTPCPVVV, NVC7KY46WXLW"
              value={nvcInput}
              onChange={(e) => setNvcInput(e.target.value)}
            />
          </div>
          <button className="btn btn-outline" onClick={lookupNVC}>Lookup</button>
        </div>

        {lookupResults && (
          <div className="mt-4 space-y-3">
            {lookupResults.found.map((code: string) => (
              <div key={code} className="card">
                <div className="flex items-center gap-2 mb-2">
                  <span className="font-semibold text-sm">{code}</span>
                  <span className="badge badge-green">Found</span>
                </div>
                <pre className="text-xs text-gray-600 overflow-x-auto">
                  {JSON.stringify(lookupResults.results[code], null, 2)}
                </pre>
              </div>
            ))}
            {lookupResults.missing.length > 0 && (
              <div className="card border-[var(--color-ws-orange)]">
                <span className="text-sm text-[var(--color-ws-orange)] font-semibold">
                  Not found: {lookupResults.missing.join(", ")}
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Invoices Tab (Cached) ────────────────────────────────────────────── */

function InvoicesTab() {
  const [invoices, setInvoices] = useState<CachedInvoice[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filterTenant, setFilterTenant] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterSearch, setFilterSearch] = useState("");
  const [sortBy, setSortBy] = useState("created_at");
  const [sortDir, setSortDir] = useState("desc");

  const loadInvoices = useCallback(() => {
    setLoading(true);
    setError("");
    api.cachedInvoices({
      tenant: filterTenant || undefined,
      status: filterStatus || undefined,
      search: filterSearch || undefined,
      sort_by: sortBy,
      sort_dir: sortDir,
      limit: 500,
    })
      .then((res) => { setInvoices(res.invoices); setTotal(res.total); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [filterTenant, filterStatus, filterSearch, sortBy, sortDir]);

  useEffect(() => { loadInvoices(); }, [loadInvoices]);

  // Compute summary stats
  const totalValue = invoices.reduce((s, inv) => s + (inv.total_amount || 0), 0);
  const statusCounts: Record<string, number> = {};
  invoices.forEach((inv) => {
    const sl = inv.status_label || "Unknown";
    statusCounts[sl] = (statusCounts[sl] || 0) + 1;
  });

  // Unique tenants for filter
  const tenantOptions = [...new Set(invoices.map((inv) => inv.tenant).filter(Boolean))].sort();

  const statusBadge = (s: string) => {
    if (s === "Paid") return "badge-green";
    if (s === "Processing" || s === "In Flight") return "badge-orange";
    if (s === "Scheduled") return "bg-blue-100 text-blue-700";
    if (s === "Approved") return "bg-yellow-100 text-yellow-700";
    if (s === "Rejected") return "bg-red-100 text-red-700";
    if (s === "New") return "bg-gray-100 text-gray-500";
    return "badge-gray";
  };

  return (
    <div className="space-y-6">
      {/* Summary metrics */}
      <div className="grid grid-cols-4 gap-4">
        <MetricCard label="Total Invoices" value={total} />
        <MetricCard label="Total Value" value={formatCurrencyFull(totalValue)} />
        <MetricCard label="Paid" value={statusCounts["Paid"] || 0} />
        <MetricCard label="Processing" value={(statusCounts["Processing"] || 0) + (statusCounts["In Flight"] || 0)} />
      </div>

      {/* Filters */}
      <div className="flex items-end gap-4 flex-wrap">
        <div className="flex-1 min-w-[200px]">
          <label className="section-label block mb-2">Search</label>
          <input
            type="text"
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm w-full"
            placeholder="NVC code, invoice number, tenant..."
            value={filterSearch}
            onChange={(e) => setFilterSearch(e.target.value)}
          />
        </div>
        <div>
          <label className="section-label block mb-2">Tenant</label>
          <select
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm"
            value={filterTenant}
            onChange={(e) => setFilterTenant(e.target.value)}
          >
            <option value="">All Tenants</option>
            {tenantOptions.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div>
          <label className="section-label block mb-2">Status</label>
          <select
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm"
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
          >
            <option value="">All Statuses</option>
            <option value="Approved">Approved</option>
            <option value="Scheduled">Scheduled</option>
            <option value="Processing">Processing</option>
            <option value="In Flight">In Flight</option>
            <option value="Paid">Paid</option>
            <option value="New">New</option>
            <option value="Rejected">Rejected</option>
          </select>
        </div>
        <div>
          <label className="section-label block mb-2">Sort</label>
          <select
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm"
            value={`${sortBy}:${sortDir}`}
            onChange={(e) => { const [b, d] = e.target.value.split(":"); setSortBy(b); setSortDir(d); }}
          >
            <option value="created_at:desc">Newest First</option>
            <option value="created_at:asc">Oldest First</option>
            <option value="total_amount:desc">Highest Amount</option>
            <option value="total_amount:asc">Lowest Amount</option>
            <option value="tenant:asc">Tenant A-Z</option>
          </select>
        </div>
        {(filterTenant || filterStatus || filterSearch) && (
          <button
            className="text-sm text-[var(--color-ws-orange)] font-semibold hover:underline pb-2"
            onClick={() => { setFilterTenant(""); setFilterStatus(""); setFilterSearch(""); }}
          >
            Clear
          </button>
        )}
      </div>

      <p className="text-xs text-gray-400">
        {invoices.length} of {total} invoices{filterTenant || filterStatus || filterSearch ? " (filtered)" : ""} · Synced from Worksuite every 5 minutes
      </p>

      {error && <ErrorBox message={error} />}
      {loading && <LoadingSkeleton rows={8} />}

      {!loading && invoices.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <table className="ws-table">
            <thead>
              <tr>
                <th>NVC Code</th>
                <th>Invoice #</th>
                <th>Tenant</th>
                <th>Status</th>
                <th>Amount</th>
                <th>Currency</th>
                <th>Pay Run</th>
                <th>Created</th>
                <th>Paid Date</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((inv) => (
                <tr key={inv.nvc_code}>
                  <td><NvcLink nvcCode={inv.nvc_code} tenant={inv.tenant} /></td>
                  <td className="text-sm text-gray-500">{inv.invoice_number || "—"}</td>
                  <td className="text-sm">{inv.tenant}</td>
                  <td>
                    <span className={`badge ${statusBadge(inv.status_label)}`}>
                      {inv.status_label || "Unknown"}
                    </span>
                  </td>
                  <td className="text-sm font-medium">{formatCurrencyFull(inv.total_amount || 0)}</td>
                  <td className="text-sm text-gray-400">{inv.currency || "USD"}</td>
                  <td className="text-sm text-gray-500 font-mono">{inv.payrun_id || "—"}</td>
                  <td className="text-sm text-gray-500 whitespace-nowrap">{(inv.created_at || "").slice(0, 16)}</td>
                  <td className="text-sm text-gray-500 whitespace-nowrap">{inv.paid_date || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && invoices.length === 0 && !error && (
        <div className="py-12 text-center">
          <p className="text-lg font-semibold text-gray-400">No invoices found</p>
          <p className="text-sm text-gray-300 mt-1">Data syncs from Worksuite every 5 minutes. Check the sync status in Configuration.</p>
        </div>
      )}
    </div>
  );
}

/* ── Configuration Panel (Settings Overlay) ──────────────────────────── */

const HAPPYPLACE_TENANTS = [
  { slug: "omcbbdo",           platform: "omcbbdo",           env: "us-e-6", clientId: 360, name: "OMC BBDO" },
  { slug: "omcflywheel",      platform: "omcflywheel",       env: "us-e-6", clientId: 350, name: "OMC Flywheel" },
  { slug: "omcohg",           platform: "omcohg",            env: "us-e-6", clientId: 351, name: "OMC OHG" },
  { slug: "omnicom",          platform: "omnicom",           env: "us-e-6", clientId: 325, name: "Omnicom (Corporate)" },
  { slug: "omnicombranding",  platform: "omnicombranding",   env: "us-e-6", clientId: 357, name: "Omnicom Branding" },
  { slug: "omnicomddb",       platform: "omnicomddb",        env: "us-e-5", clientId: 212, name: "Omnicom DDB" },
  { slug: "omnicommedia",     platform: "omnicommedia",      env: "us-e-6", clientId: 354, name: "Omnicom Media" },
  { slug: "omnicomoac",       platform: "omnicomoac",        env: "us-e-6", clientId: 356, name: "Omnicom OAC" },
  { slug: "omnicomprecision", platform: "omnicomprecision",  env: "us-e-6", clientId: 372, name: "Omnicom Precision" },
  { slug: "omnicomprg",       platform: "omnicomprg",        env: "us-e-6", clientId: 353, name: "Omnicom PRG" },
  { slug: "omnicomtbwa",      platform: "omnicomtbwa",       env: "us-e-6", clientId: 365, name: "Omnicom TBWA" },
];

const CONFIG_SECTIONS = [
  { id: "data-sources", label: "Data Sources", icon: "📡" },
  { id: "sync", label: "Sync Schedule", icon: "🔄" },
  { id: "thresholds", label: "Matching Thresholds", icon: "🎯" },
  { id: "aliases", label: "Agency Aliases", icon: "🏷️" },
  { id: "tenants", label: "Tenants", icon: "🏢" },
  { id: "accounts", label: "MoneyCorp Accounts", icon: "💳" },
  { id: "conventions", label: "Key Conventions", icon: "📋" },
  { id: "display", label: "Display Preferences", icon: "🎨" },
];

function ConfigurationPanel({ onClose }: { onClose: () => void }) {
  const [tenants, setTenants] = useState<TenantInfo[]>([]);
  const [config, setConfig] = useState<ConfigData | null>(null);
  const [syncState, setSyncState] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeSection, setActiveSection] = useState("data-sources");
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    Promise.all([
      api.tenants().catch(() => ({ tenants: [], count: 0 })),
      api.config().catch(() => null),
      api.syncStatus().catch(() => ({ sources: [] })),
    ]).then(([t, c, s]) => {
      setTenants(t.tenants);
      setConfig(c);
      setSyncState(s.sources || []);
    }).catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const filteredSections = searchQuery
    ? CONFIG_SECTIONS.filter((s) => s.label.toLowerCase().includes(searchQuery.toLowerCase()))
    : CONFIG_SECTIONS;

  const groups: Record<string, TenantInfo[]> = {};
  tenants.forEach((t) => {
    const g = t.group || "Other";
    if (!groups[g]) groups[g] = [];
    groups[g].push(t);
  });

  const handleTriggerSync = () => {
    api.health().then(() => {
      fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/sync/trigger`, { method: "POST" })
        .then(() => api.syncStatus().then((s) => setSyncState(s.sources || [])))
        .catch(() => {});
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-8" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[85vh] flex overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        style={{ animation: "fadeIn 0.15s ease-out" }}
      >
        {/* Left Sidebar — macOS System Settings style */}
        <div className="w-64 border-r border-[var(--color-ws-gray)] bg-gray-50/50 flex flex-col">
          <div className="p-4 border-b border-[var(--color-ws-gray)]">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-base font-bold">Configuration</h2>
              <button className="w-6 h-6 rounded-full hover:bg-gray-200 flex items-center justify-center text-gray-400 text-xs" onClick={onClose}>✕</button>
            </div>
            <input
              type="text"
              className="w-full border border-[var(--color-ws-gray)] rounded-lg px-3 py-1.5 text-sm bg-white"
              placeholder="Search settings..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
          <nav className="flex-1 overflow-auto py-2">
            {filteredSections.map((s) => (
              <button
                key={s.id}
                className={`w-full text-left px-4 py-2.5 text-sm flex items-center gap-3 transition-colors ${
                  activeSection === s.id ? "bg-[var(--color-ws-orange)]/10 text-[var(--color-ws-orange)] font-semibold" : "text-gray-600 hover:bg-gray-100"
                }`}
                onClick={() => setActiveSection(s.id)}
              >
                <span className="text-base">{s.icon}</span>
                {s.label}
              </button>
            ))}
          </nav>
        </div>

        {/* Right Content */}
        <div className="flex-1 overflow-auto p-8">
          {loading ? <LoadingSkeleton rows={6} /> : (
            <>
              {error && <ErrorBox message={error} />}

              {activeSection === "data-sources" && config && (
                <div className="space-y-6">
                  <h3 className="text-lg font-bold">Data Sources</h3>
                  <div className="space-y-4">
                    <div className="card">
                      <p className="section-label mb-3">Email Sources (Gmail)</p>
                      <div className="space-y-3">
                        {Object.entries(config.email_sources || {}).map(([key, query]) => (
                          <div key={key} className="flex items-start gap-3">
                            <span className="badge badge-gray min-w-[80px] text-center">{key}</span>
                            <code className="text-xs text-gray-500 bg-gray-50 px-2 py-1 rounded break-all">{query}</code>
                          </div>
                        ))}
                      </div>
                      <p className="text-xs text-gray-400 mt-3">Service account: {config.gmail_user}</p>
                    </div>
                    <div className="card">
                      <p className="section-label mb-2">Worksuite Database</p>
                      <p className="text-sm text-gray-600">Database: {config.db_name}</p>
                      <p className="text-xs text-gray-400 mt-1">Connection via SSH tunnel to aggregate DB</p>
                    </div>
                    <div className="card">
                      <p className="section-label mb-2">MoneyCorp API</p>
                      <p className="text-sm text-gray-600">Endpoints: payments, receivedPayments, accounts, balances</p>
                      <p className="text-xs text-gray-400 mt-1">Token refreshes automatically every 13 minutes</p>
                    </div>
                  </div>
                </div>
              )}

              {activeSection === "sync" && (
                <div className="space-y-6">
                  <div className="flex items-center justify-between">
                    <h3 className="text-lg font-bold">Sync Schedule</h3>
                    <button className="btn btn-orange text-sm" onClick={handleTriggerSync}>Trigger Sync Now</button>
                  </div>
                  <p className="text-sm text-gray-500">All sources sync automatically every 5 minutes.</p>
                  <div className="card">
                    {syncState.length > 0 ? (
                      <div className="space-y-4">
                        {syncState.map((s: any) => (
                          <div key={s.source} className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0">
                            <div className="flex items-center gap-3">
                              <StatusDot status={s.status === "ok" ? "ok" : "error"} />
                              <span className="text-sm font-semibold capitalize">{s.source.replace(/_/g, " ")}</span>
                            </div>
                            <div className="flex items-center gap-4 text-sm text-gray-500">
                              <span>{s.last_count || 0} records</span>
                              <span>{s.last_sync_at ? new Date(s.last_sync_at).toLocaleString() : "Never"}</span>
                              <span className={`badge ${s.status === "ok" ? "badge-green" : "badge-orange"}`}>{s.status}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-gray-400">No sync data available yet.</p>
                    )}
                  </div>
                </div>
              )}

              {activeSection === "thresholds" && (
                <div className="space-y-6">
                  <h3 className="text-lg font-bold">Matching Thresholds</h3>
                  <div className="card space-y-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-semibold">Amount Tolerance</p>
                        <p className="text-xs text-gray-400">Max difference for amount matching</p>
                      </div>
                      <span className="text-sm font-mono bg-gray-100 px-3 py-1 rounded">± $0.01</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-semibold">Funding Match — Date Window</p>
                        <p className="text-xs text-gray-400">Max days between received payment and remittance</p>
                      </div>
                      <span className="text-sm font-mono bg-gray-100 px-3 py-1 rounded">± 3 days</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-semibold">Auto-Match Confidence</p>
                        <p className="text-xs text-gray-400">Min score to auto-link received payment to email</p>
                      </div>
                      <span className="text-sm font-mono bg-gray-100 px-3 py-1 rounded">≥ 0.80</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-semibold">Suggest Threshold</p>
                        <p className="text-xs text-gray-400">Min score to suggest a match for manual review</p>
                      </div>
                      <span className="text-sm font-mono bg-gray-100 px-3 py-1 rounded">≥ 0.50</span>
                    </div>
                  </div>
                  <p className="text-xs text-gray-400">These thresholds are currently read-only. Edit config.json to change.</p>
                </div>
              )}

              {activeSection === "aliases" && (
                <div className="space-y-6">
                  <h3 className="text-lg font-bold">Agency Aliases</h3>
                  <p className="text-sm text-gray-500">Maps payer names from MoneyCorp received payments to agency names from remittance emails.</p>
                  <div className="card">
                    <div className="space-y-2">
                      {[
                        ["THE SCIENOMICS", "Scienomics"],
                        ["ADELPHI RESEARCH", "Adelphi Research Global"],
                        ["DDB CHICAGO INC.", "DDB Chicago, DDB"],
                        ["BBDO USA LLC", "BBDO"],
                        ["ENERGY BBDO", "Energy BBDO"],
                        ["FLEISHMANHILLARD", "FleishmanHillard"],
                        ["TBWA WORLDWIDE", "TBWA"],
                        ["OMNICOM MEDIA", "Omnicom Media Group, OMG"],
                        ["OMNICOM HEALTH", "Omnicom Health Group, OHG"],
                      ].map(([payer, aliases]) => (
                        <div key={payer} className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0">
                          <span className="text-sm font-mono font-semibold">{payer}</span>
                          <span className="text-xs text-gray-500">→ {aliases}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <p className="text-xs text-gray-400">Aliases are built iteratively from data. Edit AGENCY_ALIASES in recon_db.py to add more.</p>
                </div>
              )}

              {activeSection === "tenants" && (
                <div className="space-y-6">
                  <h3 className="text-lg font-bold">Tenants ({tenants.length})</h3>
                  <p className="text-sm text-gray-500">Omnicom tenants in Worksuite with direct Happy Place links.</p>
                  <div className="card p-0 overflow-hidden">
                    <table className="ws-table text-xs">
                      <thead>
                        <tr><th>Tenant</th><th>Group</th><th>Domain</th><th>Env</th><th>Client ID</th><th></th></tr>
                      </thead>
                      <tbody>
                        {HAPPYPLACE_TENANTS.map((t) => {
                          const tenantInfo = tenants.find((ti) => ti.slug === t.slug);
                          const domain = `${t.platform}.${t.env}.platform.production.worksuite.tech`;
                          const authUrl = `https://happyplace.production.worksuite.tech/staff-redirect?environment=${t.env}&client=${t.clientId}&domain=${domain}`;
                          return (
                            <tr key={t.slug}>
                              <td className="font-semibold">{t.name}</td>
                              <td className="text-gray-500">{tenantInfo?.group?.replace("Omnicom ", "") || "—"}</td>
                              <td className="font-mono text-gray-400">{tenantInfo?.domain || `${t.slug}.worksuite.com`}</td>
                              <td><span className="badge badge-gray">{t.env}</span></td>
                              <td className="font-mono text-gray-400">{t.clientId}</td>
                              <td className="text-right">
                                <a href={authUrl} target="_blank" rel="noopener noreferrer" className="text-[var(--color-ws-orange)] hover:underline font-semibold">Open</a>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {activeSection === "accounts" && (
                <div className="space-y-6">
                  <h3 className="text-lg font-bold">MoneyCorp Account IDs</h3>
                  <div className="card">
                    <p className="text-sm text-gray-500 mb-3">12 sub-accounts across OMC tenants.</p>
                    <div className="grid grid-cols-2 gap-2">
                      {[
                        { id: "859133", tenant: "omcbbdo" },
                        { id: "859134", tenant: "omnicomddb" },
                        { id: "859135", tenant: "omnicomtbwa" },
                        { id: "859136", tenant: "omcflywheel" },
                        { id: "859137", tenant: "omnicommedia" },
                        { id: "859138", tenant: "omnicomprecision" },
                        { id: "859139", tenant: "omnicomoac" },
                        { id: "859140", tenant: "omnicombranding" },
                        { id: "859141", tenant: "omnicomprg" },
                        { id: "859142", tenant: "omcohg" },
                        { id: "859143", tenant: "Omnicom Production" },
                        { id: "859149", tenant: "Specialty Marketing (inactive)" },
                      ].map((a) => (
                        <div key={a.id} className="flex items-center gap-2 text-sm">
                          <span className="font-mono text-xs text-gray-400">{a.id}</span>
                          <span className="text-gray-600">{a.tenant}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {activeSection === "conventions" && (
                <div className="space-y-6">
                  <h3 className="text-lg font-bold">Key Conventions</h3>
                  <div className="card space-y-3 text-sm">
                    <div className="flex items-start gap-3">
                      <span className="font-semibold min-w-[140px]">NVC Code</span>
                      <span className="text-gray-600">Universal join key across all 4 systems. Format: <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">NVC7Kxxxxxxx</code></span>
                    </div>
                    <div className="flex items-start gap-3">
                      <span className="font-semibold min-w-[140px]">MoneyCorp Ref</span>
                      <span className="text-gray-600">Payment reference: <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">tenant.NVC_CODE</code></span>
                    </div>
                    <div className="flex items-start gap-3">
                      <span className="font-semibold min-w-[140px]">4-Way Match</span>
                      <span className="text-gray-600">Remittance (email) → Funding (received) → Invoice (DB) → Payment (outbound)</span>
                    </div>
                  </div>
                </div>
              )}

              {activeSection === "display" && (
                <div className="space-y-6">
                  <h3 className="text-lg font-bold">Display Preferences</h3>
                  <div className="card space-y-4">
                    <div className="flex items-center justify-between">
                      <div><p className="text-sm font-semibold">Records Per Page</p></div>
                      <span className="text-sm font-mono bg-gray-100 px-3 py-1 rounded">200</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <div><p className="text-sm font-semibold">Currency Format</p></div>
                      <span className="text-sm font-mono bg-gray-100 px-3 py-1 rounded">USD ($)</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <div><p className="text-sm font-semibold">Timezone</p></div>
                      <span className="text-sm font-mono bg-gray-100 px-3 py-1 rounded">America/New_York</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <div><p className="text-sm font-semibold">Theme</p></div>
                      <span className="text-sm font-mono bg-gray-100 px-3 py-1 rounded">Light (Worksuite)</span>
                    </div>
                  </div>
                  <p className="text-xs text-gray-400">Display preferences are currently read-only.</p>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── MoneyCorp Sub-Accounts Tab ──────────────────────────────────────── */

function MoneyCorpTab() {
  const [accounts, setAccounts] = useState<MoneyCorpAccount[]>([]);
  const [totalCurrencies, setTotalCurrencies] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [hideZeroBalance, setHideZeroBalance] = useState(true);

  useEffect(() => {
    api.moneyCorpAccounts()
      .then((res) => {
        setAccounts(res.accounts);
        setTotalCurrencies(res.total_currencies);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <LoadingSkeleton rows={6} />;
  if (error) return (
    <div className="space-y-4">
      <ErrorBox message={error} />
      <p className="text-sm text-gray-400">MoneyCorp sub-account data is pulled from Worksuite&rsquo;s account balance records.</p>
    </div>
  );

  const totalBalance = accounts.reduce(
    (s, a) => s + a.currencies.filter((c) => c.currency === "USD").reduce((ss, c) => ss + (c.balance || 0), 0), 0
  );
  const totalProcessing = accounts.reduce(
    (s, a) => s + a.currencies.filter((c) => c.currency === "USD").reduce((ss, c) => ss + (c.processing || 0), 0), 0
  );

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-500">
        Funding sub-accounts (MoneyCorp) linked to OMC tenants in Worksuite. Balances from the latest operational fetch.
      </p>

      <div className="grid grid-cols-4 gap-4">
        <MetricCard label="Sub-Accounts" value={accounts.length} />
        <MetricCard label="Currency Pairs" value={totalCurrencies} />
        <MetricCard label="Total USD Balance" value={formatCurrencyFull(totalBalance)} />
        <MetricCard label="USD Processing" value={formatCurrencyFull(totalProcessing)} />
      </div>

      <div className="flex items-center gap-2">
        <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={hideZeroBalance}
            onChange={(e) => setHideZeroBalance(e.target.checked)}
            className="rounded border-gray-300"
          />
          Hide zero-balance currencies
        </label>
      </div>

      <div className="space-y-4">
        {accounts.map((acct) => {
          const visibleCurrencies = hideZeroBalance
            ? acct.currencies.filter((c) => (c.balance || 0) !== 0 || (c.scheduled || 0) !== 0 || (c.processing || 0) !== 0)
            : acct.currencies;
          const hiddenCount = acct.currencies.length - visibleCurrencies.length;
          return (
            <div key={acct.tenant} className="card">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <span className="font-semibold">{acct.tenant}</span>
                  <span className="text-xs text-gray-400 font-mono ml-3">{acct.processor_id}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="badge badge-lavender">{visibleCurrencies.length} currencies</span>
                  {hiddenCount > 0 && <span className="text-xs text-gray-400">{hiddenCount} zero hidden</span>}
                </div>
              </div>
              {visibleCurrencies.length > 0 ? (
                <table className="ws-table">
                  <thead>
                    <tr>
                      <th>Currency</th>
                      <th>Balance</th>
                      <th>Scheduled</th>
                      <th>Processing</th>
                      <th>Last Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleCurrencies.map((c) => (
                      <tr key={c.currency}>
                        <td><span className="badge badge-gray">{c.currency}</span></td>
                        <td className={`text-sm font-medium ${(c.balance || 0) > 0 ? "text-[var(--color-ws-green)]" : ""}`}>
                          {(c.balance || 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
                        </td>
                        <td className="text-sm text-gray-500">
                          {(c.scheduled || 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
                        </td>
                        <td className={`text-sm ${(c.processing || 0) > 0 ? "font-medium" : "text-gray-500"}`}>
                          {(c.processing || 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
                        </td>
                        <td className="text-sm text-gray-400">
                          {c.last_updated ? new Date(c.last_updated).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="text-sm text-gray-400 py-2">All currencies have zero balance</p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── Funding Tab (Incoming — Received Payments) ──────────────────────── */

function FundingIncomingTab() {
  const [records, setRecords] = useState<ReceivedPayment[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [summary, setSummary] = useState<any>(null);
  const [filterStatus, setFilterStatus] = useState("");
  const [filterPayer, setFilterPayer] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    api.receivedPayments({ match_status: filterStatus || undefined, payer: filterPayer || undefined, limit: 200 })
      .then((res) => { setRecords(res.records); setTotal(res.total); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [filterStatus, filterPayer]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.receivedPaymentsSummary().then(setSummary).catch(() => {}); }, []);

  const matchBadge = (s: string) => {
    if (s === "matched") return "badge-green";
    if (s === "suggested") return "bg-yellow-100 text-yellow-700";
    return "badge-gray";
  };

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-500">
        Inbound USD payments from customers into MoneyCorp sub-accounts. Matched to remittance emails by amount + date + payer name.
      </p>

      {summary && (
        <div className="grid grid-cols-4 gap-4">
          <MetricCard label="Total Received" value={summary.total} />
          <MetricCard label="Total USD" value={formatCurrency(summary.total_amount)} />
          <MetricCard label="Matched" value={summary.by_status?.matched?.count || 0} />
          <MetricCard label="Unmatched" value={summary.by_status?.unmatched?.count || 0} deltaColor="red" />
        </div>
      )}

      <div className="flex items-end gap-4 flex-wrap">
        <div>
          <label className="section-label block mb-2">Match Status</label>
          <select className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm" value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            <option value="">All</option>
            <option value="matched">Matched</option>
            <option value="suggested">Suggested</option>
            <option value="unmatched">Unmatched</option>
          </select>
        </div>
        <div className="flex-1 min-w-[200px]">
          <label className="section-label block mb-2">Payer</label>
          <input type="text" className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm w-full" placeholder="Search payer name..." value={filterPayer} onChange={(e) => setFilterPayer(e.target.value)} />
        </div>
      </div>

      {error && <ErrorBox message={error} />}
      {loading && <LoadingSkeleton rows={6} />}

      {!loading && records.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <table className="ws-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Payer</th>
                <th>Amount (USD)</th>
                <th>Sub-Account</th>
                <th>Status</th>
                <th>Match</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {records.map((rp) => (
                <tr key={rp.id}>
                  <td className="text-sm text-gray-500 whitespace-nowrap">{(rp.payment_date || "").slice(0, 10)}</td>
                  <td className="text-sm font-medium max-w-[200px] truncate">{rp.payer_name || "—"}</td>
                  <td className="text-sm font-bold">{formatCurrencyFull(rp.amount)}</td>
                  <td className="text-xs text-gray-400 max-w-[180px] truncate" title={rp.account_name}>{rp.account_name?.replace("Worksuite Inc. re: ", "") || rp.account_id}</td>
                  <td><span className="badge badge-gray">{rp.payment_status}</span></td>
                  <td><span className={`badge ${matchBadge(rp.match_status)}`}>{rp.match_status}</span></td>
                  <td className="text-xs text-gray-400">{rp.match_confidence ? `${(rp.match_confidence * 100).toFixed(0)}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && records.length === 0 && !error && (
        <div className="py-12 text-center">
          <p className="text-lg font-semibold text-gray-400">No received payments found</p>
          <p className="text-sm text-gray-300 mt-1">Sync will fetch MoneyCorp receivedPayments automatically.</p>
        </div>
      )}
    </div>
  );
}

/* ── Payments Tab (Outgoing to Contractors) ──────────────────────────── */

function PaymentsOutgoingTab() {
  const [records, setRecords] = useState<ReconRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filterTenant, setFilterTenant] = useState("");
  const [filterSearch, setFilterSearch] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    const params: Record<string, any> = { limit: 500 };
    if (filterTenant) params.tenant = filterTenant;
    if (filterSearch) params.search = filterSearch;
    api.crossSearch({ q: filterSearch || "", source: "funding", tenant: filterTenant, limit: 500 })
      .then((res) => { setRecords(res.results); setTotal(res.count); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [filterTenant, filterSearch]);

  useEffect(() => { load(); }, [load]);

  const totalValue = records.reduce((s, r) => s + (r.payment_amount || 0), 0);

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-500">
        Outbound payments from MoneyCorp to contractors. Each payment is linked to an NVC code via the payment reference.
      </p>

      <div className="grid grid-cols-3 gap-4">
        <MetricCard label="Outbound Payments" value={total} />
        <MetricCard label="Total Value" value={formatCurrencyFull(totalValue)} />
        <MetricCard label="Currencies" value="Multi" delta="USD, PHP, EUR, GBP, MXN..." />
      </div>

      <div className="flex items-end gap-4 flex-wrap">
        <div className="flex-1 min-w-[200px]">
          <label className="section-label block mb-2">Search NVC</label>
          <input type="text" className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm w-full" placeholder="NVC7K..." value={filterSearch} onChange={(e) => setFilterSearch(e.target.value)} />
        </div>
        <div>
          <label className="section-label block mb-2">Group</label>
          <select className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm" value={filterTenant} onChange={(e) => setFilterTenant(e.target.value)}>
            <option value="">All</option>
            {["omcbbdo","omcflywheel","omcohg","omnicom","omnicombranding","omnicomddb","omnicommedia","omnicomoac","omnicomprecision","omnicomprg","omnicomtbwa"].map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
      </div>

      {error && <ErrorBox message={error} />}
      {loading && <LoadingSkeleton rows={6} />}

      {!loading && records.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <table className="ws-table">
            <thead>
              <tr>
                <th>NVC Code</th>
                <th>Recipient</th>
                <th>Amount</th>
                <th>Currency</th>
                <th>Status</th>
                <th>Date</th>
                <th>Group</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r: any, i: number) => (
                <tr key={r.nvc_code || i}>
                  <td><NvcLink nvcCode={r.nvc_code} tenant={r.invoice_tenant} /></td>
                  <td className="text-sm text-gray-500 max-w-[180px] truncate">{r.payment_recipient || "—"}</td>
                  <td className="text-sm font-medium">{r.payment_amount ? formatCurrencyFull(r.payment_amount) : "—"}</td>
                  <td><span className="badge badge-gray">{r.payment_currency || "USD"}</span></td>
                  <td className="text-xs text-gray-500">{r.payment_status || "—"}</td>
                  <td className="text-sm text-gray-500 whitespace-nowrap">{(r.payment_date || "").slice(0, 10)}</td>
                  <td className="text-sm text-gray-400">{r.invoice_tenant || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && records.length === 0 && !error && (
        <div className="py-12 text-center">
          <p className="text-lg font-semibold text-gray-400">No outbound payments found</p>
          <p className="text-sm text-gray-300 mt-1">Data syncs from MoneyCorp every 5 minutes.</p>
        </div>
      )}
    </div>
  );
}

/* ── Shared Components ───────────────────────────────────────────────── */

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="card border-[var(--color-ws-orange)] bg-orange-50">
      <p className="text-sm text-[var(--color-ws-orange)]">{message}</p>
    </div>
  );
}

function LoadingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-4">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton h-12 w-full" />
      ))}
    </div>
  );
}

/* ── Main Page ───────────────────────────────────────────────────────── */

export default function Home() {
  const [activeTab, setActiveTab] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<any>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  const handleSearch = useCallback((query: string) => {
    setSearchQuery(query);
    if (query.toUpperCase().startsWith("NVC") || query.includes(",")) {
      setSearchLoading(true);
      setActiveTab(-1);
      api.lookupNVC(query)
        .then(setSearchResults)
        .catch(() => setSearchResults(null))
        .finally(() => setSearchLoading(false));
    } else {
      setActiveTab(-1);
      setSearchResults({ type: "text", query });
    }
  }, []);

  return (
    <div className="max-w-7xl mx-auto px-6 py-6">
      <Header onSearch={handleSearch} onOpenSettings={() => setShowSettings(true)} />
      <Tabs tabs={TAB_NAMES} active={activeTab} onChange={(i) => { setActiveTab(i); setSearchQuery(""); setSearchResults(null); }} />

      {activeTab === -1 && searchQuery && (
        <SearchResultsView query={searchQuery} results={searchResults} loading={searchLoading} />
      )}
      {activeTab === 0 && <OverviewTab />}
      {activeTab === 1 && <QueueTab />}
      {activeTab === 2 && <InvoicesTab />}
      {activeTab === 3 && <FundingEmailsTab />}
      {activeTab === 4 && <PaymentsOutgoingTab />}
      {activeTab === 5 && <FundingIncomingTab />}
      {activeTab === 6 && <PayRunsTab />}

      {showSettings && <ConfigurationPanel onClose={() => setShowSettings(false)} />}
    </div>
  );
}

/* ── Search Results ──────────────────────────────────────────────────── */

function SearchResultsView({ query, results, loading }: { query: string; results: any; loading: boolean }) {
  if (loading) return <LoadingSkeleton rows={3} />;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <p className="section-label">Search Results</p>
        <span className="text-sm text-gray-500">for &ldquo;{query}&rdquo;</span>
      </div>

      {results?.found?.length > 0 && (
        <div className="space-y-3">
          {results.found.map((code: string) => (
            <div key={code} className="card">
              <div className="flex items-center gap-2 mb-3">
                <span className="font-semibold">{code}</span>
                <span className="badge badge-green">Found in Worksuite</span>
              </div>
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div>
                  <span className="section-label">Tenant</span>
                  <p className="mt-1">{(results.results[code]?.tenant || "").replace(".worksuite.com", "")}</p>
                </div>
                <div>
                  <span className="section-label">Amount</span>
                  <p className="mt-1 font-medium">{formatCurrencyFull(results.results[code]?.total_amount || 0)}</p>
                </div>
                <div>
                  <span className="section-label">Currency</span>
                  <p className="mt-1">{results.results[code]?.currency || "USD"}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {results?.missing?.length > 0 && (
        <div className="card border-[var(--color-ws-orange)]">
          <p className="text-sm font-semibold text-[var(--color-ws-orange)] mb-1">Not found in Worksuite</p>
          <p className="text-sm text-gray-600">{results.missing.join(", ")}</p>
        </div>
      )}

      {results?.type === "text" && (
        <p className="text-sm text-gray-400">
          Text search coming soon. Try searching for NVC codes (e.g., NVC7KTPCPVVV) for instant lookup.
        </p>
      )}

      {!results && !loading && (
        <p className="text-sm text-gray-400">No results found.</p>
      )}
    </div>
  );
}
