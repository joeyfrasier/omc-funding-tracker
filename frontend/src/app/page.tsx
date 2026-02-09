"use client";

import { useState, useEffect, useCallback } from "react";
import Header from "@/components/Header";
import Tabs from "@/components/Tabs";
import MetricCard from "@/components/MetricCard";
import StatusDot from "@/components/StatusDot";
import { api, OverviewData, EmailItem, PayRun, ReconcileResult, ProcessedEmail, StatsData, ConfigData } from "@/lib/api";

const TAB_NAMES = ["Overview", "Funding Emails", "Pay Runs", "Reconcile", "History"];

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

  useEffect(() => {
    api.overview(7)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <LoadingSkeleton rows={4} />;
  if (error) return <ErrorBox message={error} />;
  if (!data) return null;

  return (
    <div className="space-y-8">
      <div>
        <p className="section-label mb-4">Reconciliation Health</p>
        <div className="grid grid-cols-4 gap-4">
          <MetricCard label="Match Rate" value={`${data.match_rate}%`} />
          <MetricCard label="Matched" value={data.matched} />
          <MetricCard
            label="Issues"
            value={data.mismatched + data.not_found}
            delta={data.mismatched + data.not_found > 0 ? `${data.mismatched} mismatch, ${data.not_found} missing` : undefined}
            deltaColor="red"
          />
          <MetricCard label="Total Value" value={formatCurrency(data.total_value)} />
        </div>
      </div>

      <div className="grid grid-cols-5 gap-8">
        <div className="col-span-3">
          <p className="section-label mb-4">By Agency</p>
          {data.agencies.length > 0 ? (
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
          ) : (
            <p className="text-sm text-gray-400">No payment data available</p>
          )}
        </div>

        <div className="col-span-2">
          <p className="section-label mb-4">System Status</p>
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <StatusDot status={data.errors.db ? "error" : "ok"} />
              <span className="text-sm">
                {data.errors.db ? `Database: ${data.errors.db.slice(0, 50)}` : `Database: ${data.payments_count} payments (7d)`}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <StatusDot status={data.errors.gmail ? "error" : "ok"} />
              <span className="text-sm">
                {data.errors.gmail ? `Gmail: ${data.errors.gmail.slice(0, 50)}` : `Gmail: ${data.processed_count} processed`}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <StatusDot status="warn" />
              <span className="text-sm">MoneyCorp: Token needs refresh</span>
            </div>
          </div>

          <div className="mt-6">
            <p className="section-label mb-3">Quick Stats</p>
            <div className="grid grid-cols-2 gap-3">
              <MetricCard label="Emails" value={data.total_emails} />
              <MetricCard label="Remittances" value={data.total_remittances} />
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
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState("all");
  const [maxResults, setMaxResults] = useState(10);
  const [error, setError] = useState("");

  const fetchData = useCallback(() => {
    setLoading(true);
    setError("");
    api.fetchEmails(source, maxResults)
      .then((res) => setEmails(res.emails))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [source, maxResults]);

  return (
    <div className="space-y-6">
      <div className="flex items-end gap-4">
        <div>
          <label className="section-label block mb-2">Source</label>
          <select
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          >
            <option value="all">All Sources</option>
            <option value="oasys">OASYS</option>
            <option value="d365_ach">D365 ACH</option>
            <option value="ldn_gss">LDN GSS</option>
          </select>
        </div>
        <div>
          <label className="section-label block mb-2">Max Emails</label>
          <input
            type="number"
            className="border border-[var(--color-ws-gray)] rounded-lg px-3 py-2 text-sm w-24"
            value={maxResults}
            min={1}
            max={100}
            onChange={(e) => setMaxResults(Number(e.target.value))}
          />
        </div>
        <button className="btn btn-black" onClick={fetchData} disabled={loading}>
          {loading ? "Fetching…" : "Fetch Emails"}
        </button>
      </div>

      {error && <ErrorBox message={error} />}

      {emails.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <table className="ws-table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Date</th>
                <th>Subject</th>
                <th>From</th>
                <th>Attachments</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {emails.map((e) => (
                <tr key={e.id}>
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
    </div>
  );
}

/* ── Pay Runs Tab ────────────────────────────────────────────────────── */

function PayRunsTab() {
  const [payruns, setPayruns] = useState<PayRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [days, setDays] = useState(30);
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

  const totalValue = payruns.reduce((s, p) => s + (p.total_amount || 0), 0);
  const totalPayments = payruns.reduce((s, p) => s + p.payment_count, 0);

  return (
    <div className="space-y-6">
      <div className="flex items-end gap-4">
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
          <div className="grid grid-cols-3 gap-4">
            <MetricCard label="Pay Runs" value={payruns.length} />
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
                {payruns.map((p, i) => (
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
  if (error) return <ErrorBox message={error} />;
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

  return (
    <div className="max-w-7xl mx-auto px-6 py-6">
      <Header />
      <Tabs tabs={TAB_NAMES} active={activeTab} onChange={setActiveTab} />

      {activeTab === 0 && <OverviewTab />}
      {activeTab === 1 && <FundingEmailsTab />}
      {activeTab === 2 && <PayRunsTab />}
      {activeTab === 3 && <ReconcileTab />}
      {activeTab === 4 && <HistoryTab />}
    </div>
  );
}
