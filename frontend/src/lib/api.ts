const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchAPI<T>(path: string, options?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const { timeoutMs = 15000, ...fetchOptions } = options || {};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...fetchOptions,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...fetchOptions?.headers },
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `API error: ${res.status}`);
    }
    return res.json();
  } catch (e: any) {
    if (e.name === "AbortError") {
      throw new Error("Request timed out — the server may be connecting to external services. Try again.");
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

export interface OverviewData {
  payments_count: number;
  processed_count: number;
  match_rate: number;
  match_rate_2way: number;
  matched_3way: number;
  matched_2way: number;
  matched: number;
  mismatched: number;
  not_found: number;
  unverified: number;
  total_lines: number;
  total_value: number;
  total_emails: number;
  total_remittances: number;
  agencies: {
    name: string; count: number; total: number;
    matchable_count?: number; reconciled_count?: number; full_4way_count?: number;
    unreconciled_count?: number; anomaly_count?: number;
  }[];
  errors: Record<string, string>;
  services: Record<string, string>;
  sync?: Record<string, string>;
  funding_count?: number;
  matchable_total?: number;
  excluded_prematch?: number;
  excluded_terminal?: number;
  anomaly_rejected_with_funding?: number;
}

export interface EmailItem {
  id: string;
  source: string;
  date: string;
  subject: string;
  from: string;
  attachments: string[];
  manual_review: boolean;
}

export interface PayRun {
  reference: string;
  tenant: string;
  status: number;
  payment_count: number;
  total_amount: number;
  created_at: string;
}

export interface ReconcileMatch {
  nvc_code: string;
  contractor: string;
  company: string;
  remittance_amount: number;
  db_amount: number | null;
  difference: number | null;
  status: string;
  notes: string;
  tenant: string;
}

export interface ReconcileReport {
  agency: string;
  subject: string;
  total: number;
  source: string;
  matched: number;
  mismatched: number;
  not_found: number;
  total_lines: number;
  matches: ReconcileMatch[];
}

export interface ReconcileResult {
  success: boolean;
  message?: string;
  emails_fetched: number;
  remittances_parsed: number;
  manual_review?: number;
  reports: ReconcileReport[];
}

export interface ProcessedEmail {
  source: string;
  subject: string;
  email_date: string;
  fetched_at: string;
  attachment_count: number;
  total_matched: number;
  total_mismatched: number;
  total_not_found: number;
  total_amount: number;
  manual_review: boolean;
}

export interface StatsData {
  total_emails: number;
  total_remittances: number;
  matched: number;
  mismatched: number;
  not_found: number;
  total_value: number;
  sources?: Record<string, number>;
}

export interface ConfigData {
  email_sources: Record<string, string>;
  omc_tenants: string[];
  gmail_user: string;
  db_name: string;
}

export interface TenantInfo {
  domain: string;
  slug: string;
  display_name: string;
  group: string;
  funding_method: string;
  config_updated: string | null;
}

export interface MoneyCorpCurrency {
  currency: string;
  balance: number;
  scheduled: number;
  processing: number;
  last_updated: string | null;
}

export interface MoneyCorpAccount {
  tenant: string;
  processor_id: string;
  currencies: MoneyCorpCurrency[];
}

export interface CachedInvoice {
  nvc_code: string;
  payment_id: number | null;
  invoice_number: string;
  total_amount: number;
  currency: string;
  status: number;
  status_label: string;
  paid_date: string;
  processing_date: string;
  in_flight_date: string;
  tenant: string;
  payrun_id: string;
  created_at: string;
  fetched_at: string;
}

export interface ReconRecord {
  nvc_code: string;
  remittance_amount: number | null;
  remittance_date: string | null;
  remittance_source: string | null;
  remittance_email_id: string | null;
  invoice_amount: number | null;
  invoice_status: string | null;
  invoice_tenant: string | null;
  invoice_payrun_ref: string | null;
  invoice_currency: string | null;
  payment_amount: number | null;
  payment_account_id: string | null;
  payment_date: string | null;
  payment_currency: string | null;
  payment_status: string | null;
  payment_recipient: string | null;
  payment_recipient_country: string | null;
  received_payment_id: string | null;
  received_payment_amount: number | null;
  received_payment_date: string | null;
  match_status: string;
  match_flags: string;
  first_seen_at: string;
  last_updated_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  notes: string | null;
  flag: string | null;
  flag_notes: string | null;
}

export interface ReceivedPayment {
  id: string;
  account_id: string;
  account_name: string;
  amount: number;
  currency: string;
  payment_date: string;
  payment_status: string;
  payer_name: string;
  raw_info: string;
  msl_reference: string;
  created_on: string;
  matched_remittance_email_id: string | null;
  match_confidence: number | null;
  match_method: string | null;
  match_status: string;
  matched_at: string | null;
  matched_by: string | null;
  notes: string | null;
  fetched_at: string;
}

export const api = {
  health: () => fetchAPI<{ status: string }>("/api/health"),
  overview: (days = 7) => fetchAPI<OverviewData>(`/api/overview?days=${days}`),
  fetchEmails: (source = "all", maxResults = 10) =>
    fetchAPI<{ count: number; emails: EmailItem[] }>(
      `/api/emails/fetch?source=${source}&max_results=${maxResults}`
    ),
  processedEmails: (limit = 100) =>
    fetchAPI<{ emails: ProcessedEmail[]; stats: StatsData }>(`/api/emails/processed?limit=${limit}`),
  payruns: (days = 30) =>
    fetchAPI<{ count: number; payruns: PayRun[] }>(`/api/payruns?days=${days}`),
  payments: (days = 7) =>
    fetchAPI<{ count: number; payments: any[] }>(`/api/payments?days=${days}`),
  lookupNVC: (codes: string) =>
    fetchAPI<{ results: Record<string, any>; found: string[]; missing: string[] }>(
      `/api/payments/lookup?nvc_codes=${encodeURIComponent(codes)}`
    ),
  reconcile: (maxEmails = 20, includeProcessed = false) =>
    fetchAPI<ReconcileResult>("/api/reconcile", {
      method: "POST",
      body: JSON.stringify({ max_emails: maxEmails, include_processed: includeProcessed }),
    }),
  config: () => fetchAPI<ConfigData>("/api/config"),
  tenants: () => fetchAPI<{ tenants: TenantInfo[]; count: number }>("/api/tenants"),
  moneyCorpAccounts: () =>
    fetchAPI<{ accounts: MoneyCorpAccount[]; count: number; total_currencies: number }>(
      "/api/moneycorp/subaccounts"
    ),

  emailDetail: (emailId: string) =>
    fetchAPI<any>(`/api/emails/${encodeURIComponent(emailId)}`),

  cachedInvoices: (params: {
    tenant?: string; status?: string; search?: string;
    sort_by?: string; sort_dir?: string; limit?: number; offset?: number;
  } = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== "") qs.set(k, String(v)); });
    return fetchAPI<{ invoices: CachedInvoice[]; total: number }>(`/api/invoices/cached?${qs}`);
  },

  cachedPayruns: (params: {
    tenant?: string; status?: number; search?: string;
    sort_by?: string; sort_dir?: string; limit?: number; offset?: number;
  } = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== "") qs.set(k, String(v)); });
    return fetchAPI<{ count: number; payruns: PayRun[] }>(`/api/payruns/cached?${qs}`);
  },

  syncStatus: () => fetchAPI<{ sources: any[] }>("/api/sync/status"),

  // Reconciliation Queue
  reconQueue: (params: {
    status?: string; tenant?: string; flag?: string; search?: string;
    invoice_status?: string; sort_by?: string; sort_dir?: string; limit?: number; offset?: number;
  } = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== "") qs.set(k, String(v)); });
    return fetchAPI<{ records: ReconRecord[]; total: number }>(`/api/recon/queue?${qs}`);
  },

  reconRecord: (nvcCode: string) =>
    fetchAPI<ReconRecord>(`/api/recon/record/${encodeURIComponent(nvcCode)}`),

  reconSummary: () =>
    fetchAPI<Record<string, number>>("/api/recon/summary"),

  crossSearch: (params: { q: string; source: string; amount_min?: number; amount_max?: number; tenant?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== "") qs.set(k, String(v)); });
    return fetchAPI<{ results: any[]; count: number }>(`/api/search/cross?${qs}`);
  },

  reconSuggestions: (nvcCode: string) =>
    fetchAPI<{ suggestions: any[] }>(`/api/recon/suggestions/${encodeURIComponent(nvcCode)}`),

  reconAssociate: (data: { nvc_code: string; associate_with: string; source: string; notes?: string }) =>
    fetchAPI<{ success: boolean; record: ReconRecord }>("/api/recon/associate", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  reconFlag: (data: { nvc_code: string; flag: string; notes?: string }) =>
    fetchAPI<{ success: boolean }>("/api/recon/flag", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Received Payments (Leg 3 — Inbound Funding)
  receivedPayments: (params: {
    account_id?: string; match_status?: string; payer?: string;
    date_from?: string; date_to?: string; limit?: number; offset?: number;
  } = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== "") qs.set(k, String(v)); });
    return fetchAPI<{ records: ReceivedPayment[]; total: number }>(`/api/received-payments?${qs}`);
  },

  receivedPaymentsSummary: () =>
    fetchAPI<{ total: number; total_amount: number; by_status: Record<string, { count: number; amount: number }> }>("/api/received-payments/summary"),

  receivedPaymentDetail: (id: string) =>
    fetchAPI<ReceivedPayment>(`/api/received-payments/${encodeURIComponent(id)}`),

  matchReceivedPayment: (id: string, emailId: string) =>
    fetchAPI<{ success: boolean; linked_nvcs: number }>(`/api/received-payments/${encodeURIComponent(id)}/match`, {
      method: "POST",
      body: JSON.stringify({ email_id: emailId }),
    }),

  unmatchReceivedPayment: (id: string) =>
    fetchAPI<{ success: boolean }>(`/api/received-payments/${encodeURIComponent(id)}/unmatch`, {
      method: "POST",
    }),

  receivedPaymentSuggestions: (id: string) =>
    fetchAPI<{ payment_id: string; suggestions: any[] }>(`/api/received-payments/suggestions/${encodeURIComponent(id)}`),
};
