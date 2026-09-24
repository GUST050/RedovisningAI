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

export type Overview = {
  company: { id: string; name: string; org_number: string | null };
  period: { spec: string; label: string };
  maturity: Maturity;
  recommended_view: string;
  sections: Record<string, { period: { spec: string; label: string }; compare: { spec: string; label: string }; metrics: Record<string, MetricEntry> }>;
  months_with_data: string[];
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
