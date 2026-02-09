"use client";

import { useState, useEffect, useCallback } from "react";
import Header from "@/components/Header";
import Tabs from "@/components/Tabs";
import MetricCard from "@/components/MetricCard";
import StatusDot from "@/components/StatusDot";
import { api, OverviewData, EmailItem, PayRun, ReconcileResult, ProcessedEmail, StatsData, ConfigData, TenantInfo, MoneyCorpAccount } from "@/lib/api";

const TAB_NAMES = ["Overview", "Funding Emails", "Pay Runs", "Worksuite Tenants", "MoneyCorp", "Reconcile", "History"];

function formatCurrency(n: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(n);
}

function formatCurrencyFull(n: number) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 }).format(n);
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
            delta={data && data.match_rate_2way > 0 ? `${data.match_rate_2way}% Worksuite verified` : undefined}
            deltaColor={data && data.match_rate > 0 ? "green" : "gray"}
          />
          <MetricCard
            label="Verified"
            value={data?.matched_3way ?? "—"}
            delta={data ? `of ${data.total_lines} remittance lines` : undefined}
          />
          <MetricCard
            label="Issues"
            value={data ? data.unverified : "—"}
            delta={data && data.unverified > 0 ? `${data.mismatched} mismatch · ${data.not_found} invoice only · ${data.matched_2way} 2-way matched (no remittance)` : undefined}
            deltaColor="red"
          />
          <MetricCard label="Total Value" value={data ? formatCurrency(data.total_value) : "—"} />
        </div>
      </div>

      <div className="grid grid-cols-5 gap-8">
        <div className="col-span-3">
          <p className="section-label mb-4">By Agency</p>
          {data && data.agencies.length > 0 ? (
            <div className="space-y-2">
              {data.agencies.slice(0, 10).map((a) => (
                <div key={a.name} className="flex items-center justify-between py-2 px-3 rounded-lg hover:bg-gray-50">
                  <span className="font-semibold text-sm">{a.name}</span>
                  <div className="flex items-center gap-6">
                    <span className="text-sm text-gray-500">{a.count} payments</span>
                    <span className="text-sm font-medium">{formatCurrency(a.total)}</span>
                  </div>
                </div>
              ))}
              {data.agencies.length > 10 && (
                <p className="text-xs text-gray-400 pl-3">+{data.agencies.length - 10} more agencies</p>
              )}
            </div>
          ) : hasDbError ? (
            <div className="py-6 text-center">
              <p className="text-sm text-gray-400 mb-1">Agency data requires database connection</p>
              <p className="text-xs text-gray-300">Connect to VPN and retry</p>
            </div>
          ) : (
            <p className="text-sm text-gray-400">No payment data available</p>
          )}
        </div>

        <div className="col-span-2">
          <p className="section-label mb-4">System Status</p>
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <StatusDot status={hasDbError ? "error" : "ok"} />
              <span className="text-sm">
                {hasDbError
                  ? "Worksuite: Unreachable"
                  : `Worksuite: ${data?.payments_count ?? 0} payments (7d)`}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <StatusDot status={hasGmailError ? "error" : "ok"} />
              <span className="text-sm">
                {hasGmailError
                  ? "PayOps Email: Unavailable"
                  : `PayOps Email: ${data?.processed_count ?? 0} processed`}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <StatusDot status={data?.sync?.funding === 'ok' ? 'ok' : 'warn'} />
              <span className="text-sm">MoneyCorp: {data?.sync?.funding === 'ok' ? `${data.funding_count || 0} payments synced` : 'Token needs refresh'}</span>
            </div>
          </div>

          <div className="mt-6">
            <p className="section-label mb-3">Quick Stats</p>
            <div className="grid grid-cols-2 gap-3">
              <MetricCard label="Emails" value={data?.total_emails ?? 0} />
              <MetricCard label="Remittances" value={data?.total_remittances ?? 0} />
            </div>
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
  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-6" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-xl max-w-3xl w-full max-h-[80vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
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
        <div className="p-6 space-y-6">
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
                  <div key={i} className="flex items-center gap-2 text-sm">
                    <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
                    </svg>
                    <span>{a}</span>
                  </div>
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

          <div className="border-t border-[var(--color-ws-gray)] pt-4">
            <p className="section-label mb-2">Reconciliation Status</p>
            <p className="text-sm text-gray-500">
              Remittance lines from this email are tracked in the reconciliation engine.
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-6 border-t border-[var(--color-ws-gray)]">
          <button className="btn btn-outline" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

/* ── Pay Runs Tab ────────────────────────────────────────────────────── */

function PayRunsTab() {
  const [payruns, setPayruns] = useState<PayRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [days, setDays] = useState(30);
  const [filterTenant, setFilterTenant] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterMinAmount, setFilterMinAmount] = useState("");
  const [nvcInput, setNvcInput] = useState("");
  const [lookupResults, setLookupResults] = useState<any>(null);
  const [error, setError] = useState("");

  const loadPayruns = useCallback(() => {
    setLoading(true);
    setError("");
    api.payruns(days)
      .then((res) => setPayruns(res.payruns))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [days]);

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

  return (
    <div className="space-y-6">
      <div className="flex items-end gap-4 flex-wrap">
        <div>
          <label className="section-label block mb-2">Days Back</label>
          <input
            type="number"
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm w-24"
            value={days}
            min={7}
            max={180}
            onChange={(e) => setDays(Number(e.target.value))}
          />
        </div>
        <button className="btn btn-black" onClick={loadPayruns} disabled={loading}>
          {loading ? "Loading…" : "Load Pay Runs"}
        </button>
      </div>

      {error && <ErrorBox message={error} />}

      {payruns.length > 0 && (
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

/* ── Reconcile Tab ───────────────────────────────────────────────────── */

function ReconcileTab() {
  const [maxEmails, setMaxEmails] = useState(20);
  const [includeProcessed, setIncludeProcessed] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ReconcileResult | null>(null);
  const [error, setError] = useState("");

  const runRecon = useCallback(() => {
    setRunning(true);
    setError("");
    setResult(null);
    api.reconcile(maxEmails, includeProcessed)
      .then(setResult)
      .catch((e) => setError(e.message))
      .finally(() => setRunning(false));
  }, [maxEmails, includeProcessed]);

  const totalMatched = result?.reports.reduce((s, r) => s + r.matched, 0) || 0;
  const totalMismatched = result?.reports.reduce((s, r) => s + r.mismatched, 0) || 0;
  const totalNotFound = result?.reports.reduce((s, r) => s + r.not_found, 0) || 0;
  const totalLines = result?.reports.reduce((s, r) => s + r.total_lines, 0) || 0;

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-500">
        Fetch emails → Parse CSVs → Match NVC codes against DB → Report discrepancies
      </p>

      <div className="flex items-end gap-4">
        <div>
          <label className="section-label block mb-2">Max Emails / Source</label>
          <input
            type="number"
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm w-24"
            value={maxEmails}
            min={5}
            max={100}
            onChange={(e) => setMaxEmails(Number(e.target.value))}
          />
        </div>
        <label className="flex items-center gap-2 text-sm pb-2">
          <input
            type="checkbox"
            checked={includeProcessed}
            onChange={(e) => setIncludeProcessed(e.target.checked)}
          />
          Include processed emails
        </label>
        <button className="btn btn-orange" onClick={runRecon} disabled={running}>
          {running ? "Running…" : "Run Reconciliation"}
        </button>
      </div>

      {error && <ErrorBox message={error} />}

      {running && (
        <div className="card flex items-center gap-3">
          <div className="w-5 h-5 border-2 border-[var(--color-ws-orange)] border-t-transparent rounded-full animate-spin" />
          <span className="text-sm">Running reconciliation…</span>
        </div>
      )}

      {result && (
        <>
          {result.message && !result.reports.length && (
            <div className="card bg-gray-50"><p className="text-sm">{result.message}</p></div>
          )}

          {result.reports.length > 0 && (
            <>
              <div className="grid grid-cols-5 gap-4">
                <MetricCard label="Remittances" value={result.reports.length} />
                <MetricCard label="Total Lines" value={totalLines} />
                <MetricCard label="Matched" value={totalMatched} />
                <MetricCard label="Mismatched" value={totalMismatched} deltaColor="red" />
                <MetricCard label="Not Found" value={totalNotFound} deltaColor="red" />
              </div>

              {totalLines > 0 && (
                <MetricCard label="Match Rate" value={`${(totalMatched / totalLines * 100).toFixed(1)}%`} />
              )}

              <div className="space-y-4">
                {result.reports.map((report, i) => {
                  const hasIssues = report.mismatched > 0 || report.not_found > 0;
                  return (
                    <details key={i} className="card group">
                      <summary className="cursor-pointer flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <span className={`status-dot ${hasIssues ? "status-error" : "status-ok"}`} />
                          <span className="font-semibold text-sm">{report.agency}</span>
                          <span className="text-sm text-gray-400">{report.total_lines} lines</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="badge badge-green">{report.matched} matched</span>
                          {report.mismatched > 0 && <span className="badge badge-orange">{report.mismatched} mismatch</span>}
                          {report.not_found > 0 && <span className="badge badge-lavender">{report.not_found} missing</span>}
                        </div>
                      </summary>
                      <div className="mt-4 overflow-x-auto">
                        <table className="ws-table">
                          <thead>
                            <tr>
                              <th>NVC Code</th>
                              <th>Contractor</th>
                              <th>Remit $</th>
                              <th>DB $</th>
                              <th>Diff</th>
                              <th>Status</th>
                              <th>Tenant</th>
                              <th>Notes</th>
                            </tr>
                          </thead>
                          <tbody>
                            {report.matches.map((m, j) => (
                              <tr key={j}>
                                <td className="text-sm font-mono">{m.nvc_code}</td>
                                <td className="text-sm max-w-[200px] truncate">{m.contractor}</td>
                                <td className="text-sm">{formatCurrencyFull(m.remittance_amount)}</td>
                                <td className="text-sm">{m.db_amount != null ? formatCurrencyFull(m.db_amount) : "—"}</td>
                                <td className={`text-sm ${m.difference && m.difference !== 0 ? "text-[var(--color-ws-orange)] font-semibold" : ""}`}>
                                  {m.difference != null ? formatCurrencyFull(m.difference) : ""}
                                </td>
                                <td>
                                  <span className={`badge ${m.status === "matched" ? "badge-green" : m.status === "not_found" ? "badge-lavender" : "badge-orange"}`}>
                                    {m.status}
                                  </span>
                                </td>
                                <td className="text-sm">{m.tenant}</td>
                                <td className="text-sm text-gray-500 max-w-[200px] truncate">{m.notes}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  );
                })}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

/* ── History Tab ─────────────────────────────────────────────────────── */

function HistoryTab() {
  const [data, setData] = useState<{ emails: ProcessedEmail[]; stats: StatsData } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api.processedEmails(100)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <LoadingSkeleton rows={6} />;
  if (error) return (
    <div className="space-y-4">
      <ErrorBox message={error} />
      <p className="text-sm text-gray-400">History is stored locally — this should work even without external connections.</p>
    </div>
  );
  if (!data || !data.emails.length) return <p className="text-sm text-gray-400">No emails processed yet. Run a reconciliation first.</p>;

  const { emails, stats } = data;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-4 gap-4">
        <MetricCard label="Total Emails" value={stats.total_emails} />
        <MetricCard label="Total Value" value={formatCurrencyFull(stats.total_value)} />
        <MetricCard label="Matched" value={stats.matched} />
        <MetricCard label="Issues" value={stats.mismatched + stats.not_found} deltaColor="red" />
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="ws-table">
          <thead>
            <tr>
              <th>Source</th>
              <th>Subject</th>
              <th>Date</th>
              <th>Attachments</th>
              <th>Matched</th>
              <th>Issues</th>
              <th>Amount</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {emails.map((e, i) => (
              <tr key={i}>
                <td><span className="badge badge-gray">{e.source}</span></td>
                <td className="text-sm max-w-md truncate">{e.subject}</td>
                <td className="text-sm text-gray-500 whitespace-nowrap">{(e.email_date || "").slice(0, 25)}</td>
                <td className="text-sm">{e.attachment_count}</td>
                <td className="text-sm">{e.total_matched || 0}</td>
                <td className="text-sm">{(e.total_mismatched || 0) + (e.total_not_found || 0)}</td>
                <td className="text-sm font-medium">{formatCurrencyFull(e.total_amount || 0)}</td>
                <td>{e.manual_review && <span className="badge badge-orange">Manual</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Worksuite Tenants Tab ────────────────────────────────────────────── */

function WorksuiteTenantsTab() {
  const [tenants, setTenants] = useState<TenantInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api.tenants()
      .then((res) => setTenants(res.tenants))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <LoadingSkeleton rows={6} />;
  if (error) return <ErrorBox message={error} />;

  // Group by group
  const groups: Record<string, TenantInfo[]> = {};
  tenants.forEach((t) => {
    const g = t.group || "Other";
    if (!groups[g]) groups[g] = [];
    groups[g].push(t);
  });

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-500">
        Omnicom tenants configured in Worksuite with OMC invoices. Edit <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">config.json</code> to update.
      </p>

      <div className="grid grid-cols-3 gap-4">
        <MetricCard label="Total Tenants" value={tenants.length} />
        <MetricCard label="Groups" value={Object.keys(groups).length} />
        <MetricCard label="Funding Method" value={tenants[0]?.funding_method || "—"} />
      </div>

      {Object.entries(groups).sort().map(([group, items]) => (
        <div key={group}>
          <p className="section-label mb-3">{group}</p>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {items.map((t) => (
              <div key={t.domain} className="card card-hover">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-semibold text-sm">{t.display_name}</span>
                  <span className="badge badge-gray">{t.funding_method}</span>
                </div>
                <p className="text-xs text-gray-400 font-mono">{t.domain}</p>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ── MoneyCorp Sub-Accounts Tab ──────────────────────────────────────── */

function MoneyCorpTab() {
  const [accounts, setAccounts] = useState<MoneyCorpAccount[]>([]);
  const [totalCurrencies, setTotalCurrencies] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

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
        MoneyCorp sub-accounts linked to OMC tenants in Worksuite. Balances from the latest operational fetch.
      </p>

      <div className="grid grid-cols-4 gap-4">
        <MetricCard label="Sub-Accounts" value={accounts.length} />
        <MetricCard label="Currency Pairs" value={totalCurrencies} />
        <MetricCard label="Total USD Balance" value={formatCurrencyFull(totalBalance)} />
        <MetricCard label="USD Processing" value={formatCurrencyFull(totalProcessing)} />
      </div>

      <div className="space-y-4">
        {accounts.map((acct) => (
          <div key={acct.tenant} className="card">
            <div className="flex items-center justify-between mb-4">
              <div>
                <span className="font-semibold">{acct.tenant}</span>
                <span className="text-xs text-gray-400 font-mono ml-3">{acct.processor_id}</span>
              </div>
              <span className="badge badge-lavender">{acct.currencies.length} currencies</span>
            </div>
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
                {acct.currencies.map((c) => (
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
          </div>
        ))}
      </div>
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

  const handleSearch = useCallback((query: string) => {
    setSearchQuery(query);
    // If it looks like NVC codes, do a lookup
    if (query.toUpperCase().startsWith("NVC") || query.includes(",")) {
      setSearchLoading(true);
      setActiveTab(-1); // Show search results
      api.lookupNVC(query)
        .then(setSearchResults)
        .catch(() => setSearchResults(null))
        .finally(() => setSearchLoading(false));
    } else {
      // Generic search — switch to appropriate tab
      setActiveTab(-1);
      setSearchResults({ type: "text", query });
    }
  }, []);

  return (
    <div className="max-w-7xl mx-auto px-6 py-6">
      <Header onSearch={handleSearch} />
      <Tabs tabs={TAB_NAMES} active={activeTab} onChange={(i) => { setActiveTab(i); setSearchQuery(""); setSearchResults(null); }} />

      {activeTab === -1 && searchQuery && (
        <SearchResultsView query={searchQuery} results={searchResults} loading={searchLoading} />
      )}
      {activeTab === 0 && <OverviewTab />}
      {activeTab === 1 && <FundingEmailsTab />}
      {activeTab === 2 && <PayRunsTab />}
      {activeTab === 3 && <WorksuiteTenantsTab />}
      {activeTab === 4 && <MoneyCorpTab />}
      {activeTab === 5 && <ReconcileTab />}
      {activeTab === 6 && <HistoryTab />}
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
