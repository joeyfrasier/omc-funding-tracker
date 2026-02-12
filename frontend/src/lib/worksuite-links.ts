/**
 * Build deep links into Worksuite via Happy Place auth redirect.
 *
 * URL pattern:
 *   Auth:   https://happyplace.production.worksuite.tech/staff-redirect?environment={env}&client={id}&domain={platform_domain}
 *   Direct: https://{platform_slug}.{env}.platform.production.worksuite.tech/payments/?keyword={nvc_code}
 *
 * The auth redirect sets a session cookie, then redirects to the platform domain.
 * After first auth, direct platform URLs work for the browser session.
 */

const HAPPYPLACE_BASE = "https://happyplace.production.worksuite.tech";

interface TenantPlatformConfig {
  platform_slug: string;
  environment: string;
  client_id: number | null;
}

// Mapping from DB tenant slug (stripped of .worksuite.com) to platform config
const TENANT_MAP: Record<string, TenantPlatformConfig> = {
  omcbbdo:           { platform_slug: "omcbbdo",           environment: "us-e-2", client_id: 89 },
  omcflywheel:       { platform_slug: "omcflywheel",       environment: "us-e-2", client_id: 91 },
  omcohg:            { platform_slug: "omcohg",            environment: "us-e-2", client_id: 92 },
  omnicom:           { platform_slug: "omnicom",           environment: "us-e-2", client_id: 97 },
  omnicombranding:   { platform_slug: "omnicombranding",   environment: "us-e-2", client_id: 90 },
  omnicomddb:        { platform_slug: "omnicomddb",        environment: "us-e-5", client_id: 212 },
  omnicommedia:      { platform_slug: "omnicommedia",      environment: "us-e-2", client_id: 65 },
  omnicomoac:        { platform_slug: "omnicomoac",        environment: "us-e-2", client_id: 93 },
  omnicomprecision:  { platform_slug: "omnicomprecision",  environment: "us-e-2", client_id: 94 },
  omnicomprg:        { platform_slug: "omnicomprg",        environment: "us-e-2", client_id: 96 },
  omnicomtbwa:       { platform_slug: "omnicomtbwa",       environment: "us-e-2", client_id: 108 },
};

/**
 * Get the direct platform URL for an invoice/payment search by NVC code.
 * Returns null if tenant is unknown.
 */
export function getInvoiceUrl(tenant: string, nvcCode?: string): string | null {
  const cfg = TENANT_MAP[tenant];
  if (!cfg) return null;
  const base = `https://${cfg.platform_slug}.${cfg.environment}.platform.production.worksuite.tech/payments/`;
  if (nvcCode) return `${base}?keyword=${encodeURIComponent(nvcCode)}`;
  return base;
}

/**
 * Get the Happy Place auth redirect URL that lands on the payments page.
 * Use this for first-time access (sets auth cookie).
 */
export function getHappyPlaceAuthUrl(tenant: string): string | null {
  const cfg = TENANT_MAP[tenant];
  if (!cfg || !cfg.client_id) return null;
  const domain = `${cfg.platform_slug}.${cfg.environment}.platform.production.worksuite.tech`;
  return `${HAPPYPLACE_BASE}/staff-redirect?environment=${cfg.environment}&client=${cfg.client_id}&domain=${domain}`;
}

/**
 * Check if a tenant has a valid platform configuration.
 */
export function hasPlatformConfig(tenant: string): boolean {
  return tenant in TENANT_MAP;
}
