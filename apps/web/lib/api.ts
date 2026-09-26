import { useCallback, useEffect, useState } from "react";

// Tunn klient mot RedovisningAI:s API. Anropen går via Next.js-proxyn (/api → API-servern),
// så att inloggningskakan skickas med utan CORS.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/q/")) {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Inte inloggad");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignorera */
    }
    throw new ApiError(res.status, msg);
  }
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export async function get<T>(path: string): Promise<T> {
  return handle<T>(await fetch(path, { credentials: "same-origin", cache: "no-store" }));
}

export async function send<T>(path: string, method: "POST" | "PUT" | "PATCH" | "DELETE", body?: unknown): Promise<T> {
  return handle<T>(
    await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

export function download(path: string) {
  window.location.href = path;
}

/** POST som svarar med en fil (t.ex. en rapport) – sparas med filnamnet från servern. */
export async function downloadPost(path: string, body: unknown, fallbackName: string): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    await handle<unknown>(res);
    return;
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  // Serverns ASCII-namn ("jamforelse" i stället för "jämförelse"): alla webbläsare sparar det
  // oförändrat, medan namn med å/ä/ö från blob-länkar ibland ersätts med "download".
  link.download = filenameFromDisposition(res.headers.get("content-disposition"), { ascii: true }) ?? fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function filenameFromDisposition(header: string | null, opts: { ascii?: boolean } = {}): string | null {
  if (!header) return null;
  if (opts.ascii) {
    const ascii = /filename="([^"]+)"/i.exec(header);
    if (ascii) return ascii[1];
  }
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1]);
    } catch {
      /* fall tillbaka på ASCII-namnet */
    }
  }
  const plain = /filename="([^"]+)"/i.exec(header);
  return plain ? plain[1] : null;
}

// ------------------------------------------------------------------------ typer

export type Me = {
  user: { id: string; email: string; name: string };
  org: { id: string; name: string };
  role: "ADMIN" | "CONSULTANT" | "VIEWER";
  permissions: { payroll: boolean; aml: boolean; approve_reports: boolean; write: boolean; admin: boolean };
  memberships: { org_id: string; org_name: string; role: string }[];
};

export type Reason = { code: string; points: number; text: string };

export type PortfolioCompany = {
  id: string;
  name: string;
  org_number: string | null;
  source_system: string | null;
  latest_period: string | null;
  status: string;
  open_findings: { high: number; medium: number; low: number };
  changed_after_approval: boolean;
  connection_ok: boolean;
  unanswered_questions: number;
  latest_data_month: string | null;
  priority: { score: number; reasons: Reason[] };
};

export type Portfolio = {
  companies: PortfolioCompany[];
  todo: Record<string, number>;
};

export type Fact = {
  id: string;
  label: string;
  display: string;
  status: string;
  lineage: Record<string, unknown>;
  unit: string;
  value: string | null;
};

export type MetricEntry = { id: string; fact: Fact; previous?: Fact; change?: Fact; change_pct?: Fact };

export type MetricComparison = {
  code: string;
  label: string;
  unit: string;
  better?: "higher" | "lower" | "neutral";
  formula?: string;
  current: string | null;
  previous: string | null;
  change: string | null;
  status: string;
  warnings: string[];
  fact_ids: string[];
};

export type MetricComparisons = {
  periods: { current: string; previous: string };
  labels?: { current: string; previous: string };
  status: string;
  warnings: string[];
  notices?: string[];
  metrics: Record<string, MetricComparison>;
};

export type DifferenceItem = {
  id: string;
  kind: "metric" | "line" | "category" | "finding";
  code: string;
  title: string;
  summary: string;
  unit: string;
  current: string | null;
  previous: string | null;
  change: string | null;
  change_pct: string | null;
  status: string;
  score: number;
  score_parts: Record<string, number>;
  reason: string;
  audience: "client" | "internal";
  better: string | null;
  warnings: string[];
  selectable: boolean;
  recommended: boolean;
  family: string | null;
  not_recommended: string | null;
};

export type SeriesKind = { kind: string; label: string; max: number; default: number };

export type ReportItems = {
  periods: { current: string; previous: string };
  labels: { current: string; previous: string };
  status: string;
  warnings: string[];
  notices: string[];
  audience: "internal" | "client";
  items: DifferenceItem[];
  recommended: string[];
  series: SeriesKind[];
};

export type StructurePoint = {
  spec: string;
  label: string;
  short: string;
  open: boolean;
  status: string;
  value: string | null;
  display: string;
  missing_months: string[];
  note: string | null;
};

export type StructureRow = {
  code: string;
  label: string;
  role: "component" | "numerator" | "denominator";
  values: (string | null)[];
  shares: (string | null)[];
  accounts: { account: number | null; name: string; values: (string | null)[] }[];
  note: string | null;
};

export type StructureStep = {
  current: string;
  previous: string;
  current_label: string;
  previous_label: string;
  status: string;
  change: string | null;
  components: { code: string; label: string; effect: string }[];
  warnings: string[];
};

export type MetricStructure = {
  code: string;
  label: string;
  unit: string;
  formula: string;
  better: string;
  series: string;
  series_label: string;
  periods: StructurePoint[];
  rows: StructureRow[];
  base_label: string | null;
  steps: StructureStep[];
  overall: StructureStep | null;
  warnings: string[];
};

export type MetricEvidenceRow = {
  period: string;
  account: number;
  amount: string;
  voucher: string | null;
  date: string | null;
  text: string;
  source_line: number | null;
  content_hash: string | null;
  source: string;
  row_status: string | null;
};

export type MetricEvidence = {
  accounts: { account: number; name: string; current: string; previous: string }[];
  current_rows: MetricEvidenceRow[];
  previous_rows: MetricEvidenceRow[];
  current_total: string;
  previous_total: string;
  other_current: string;
  other_previous: string;
  source_level: string;
  warnings: string[];
};

export type MetricExplanation = MetricComparison & {
  components: {
    code: string;
    label: string;
    role?: "component" | "numerator" | "denominator";
    current: string;
    previous: string;
    effect: string;
    unit: string;
    source_level: string;
    current_accounts: { account: number; amount: string }[];
    previous_accounts: { account: number; amount: string }[];
    fact_id: string | null;
    note: string | null;
    evidence: MetricEvidence;
  }[];
  periods: { current: string; previous: string };
  versions: Record<string, string>;
};

export type AnalysisFinding = {
  code: string;
  label: string;
  metric_codes: string[];
  period_pair: [string, string];
  amount_effect: string;
  unit: string;
  fact_ids: string[];
  sources: {
    accounts: string;
    fact_id?: string;
    source_level: string;
    references?: {
      period: string;
      voucher: string | null;
      date: string | null;
      source_line: number | null;
      content_hash: string | null;
    }[];
  }[];
  source_level: string;
  warnings: string[];
  group_key: string;
  versions: Record<string, string>;
  priority_score: number;
  score_parts: Record<string, number>;
  demotion_reasons: string[];
};

export type AnalysisFindings = {
  periods: { current: string; previous: string };
  status: string;
  warnings: string[];
  top: AnalysisFinding[];
  others: AnalysisFinding[];
  count: number;
};

export type Overview = {
  company: { id: string; name: string; org_number: string | null };
  period: { spec: string; label: string };
  maturity: Maturity;
  recommended_view: string;
  sections: Record<string, { period: { spec: string; label: string }; compare: { spec: string; label: string }; metrics: Record<string, MetricEntry> }>;
  months_with_data: string[];
  fiscal_years?: { start: string; end: string; has_vouchers: boolean }[];
};

export type Maturity = {
  period: string;
  status: string;
  accounting_method: string;
  monthly_depreciation: boolean | null;
  monthly_vacation_accrual: boolean | null;
  payroll_booked: boolean | null;
  completeness: string | null;
  closed_signal: boolean;
  low_periodization: boolean;
  recommended_view: string;
  notes: string[];
};

export type Finding = {
  id: string;
  rule_code: string;
  severity: "HIGH" | "MEDIUM" | "LOW";
  title: string;
  description: string;
  description_rendered: string;
  period: string;
  visibility: string;
  status: string;
  legal_basis: string | null;
  vouchers: string[];
  accounts: number[];
  amount: string | null;
  memory_suggestion: { text: string; decision: string } | null;
  resolution_note: string | null;
  resolved_by: string | null;
};

export type Claim = { type: string; text: string; rendered: string; fact_ids: string[] };

export type Case = {
  key: string;
  title: string;
  root_cause: string;
  suggested_action: string;
  ask_client_suggested: boolean;
  severity: "HIGH" | "MEDIUM" | "LOW";
  visibility: string;
  period: string;
  memory_hint: string | null;
  status: string;
  ai: { title?: string; suggestion?: string; root_cause?: Claim[]; rationale?: Claim[]; source?: string } | null;
  findings: Finding[];
  vouchers: string[];
};

export type StatementLine = {
  code: string;
  label: string;
  level: number;
  amount: string;
  compare: string | null;
  diff: string | null;
  diff_pct: string | null;
  accounts: { account: number; name: string; amount: string; compare: string | null }[];
};

export type Statement = { kind: string; period: string; compare: string | null; complete: boolean; lines: StatementLine[] };

export type AIOutcome = { task: string; data: Record<string, unknown>; source: "ai" | "rules"; trace_id: string; ai_generated: boolean };

// ------------------------------------------------------------------------ hook


/** Hämtar `path` (null = vänta) och laddar om när sökvägen ändras. */
export function useLoad<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const reload = useCallback(() => {
    if (!path) return;
    setLoading(true);
    setError(null);
    get<T>(path)
      .then(setData)
      .catch(setError)
      .finally(() => setLoading(false));
  }, [path]);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}
