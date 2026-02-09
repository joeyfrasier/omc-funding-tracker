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
  matched: number;
  mismatched: number;
  not_found: number;
  total_value: number;
  total_emails: number;
  total_remittances: number;
  agencies: { name: string; count: number; total: number }[];
  errors: Record<string, string>;
  services: Record<string, string>;
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
};
