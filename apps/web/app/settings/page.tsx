"use client";

import { useState } from "react";
import { AppShell, useMe } from "@/components/AppShell";
import { AiBadge, Button, Card, Empty, ErrorBox, Field, Loading, SeverityBadge, Tabs, cx, inputCls } from "@/components/ui";
import { send, useLoad } from "@/lib/api";
import { dateTime } from "@/lib/format";

type Rule = {
  code: string;
  version: string;
  category: string;
  title: string;
  description: string;
  legal_basis: string;
  valid_from: string;
  valid_to: string | null;
  severity: string;
  visibility: string;
  owner: string;
  reviewed_at: string;
  params: Record<string, unknown>;
};
type Rate = { code: string; value: string; valid_from: string; valid_to: string | null; source: string; note: string };
type Health = { rule_code: string; total: number; actioned: number; not_actioned: number; open: number; precision: string | number | null };
type Suppression = { id: string; rule_code: string; reason: string; company_id: string | null; expires_at: string | null; accounts: number[]; text_contains: string | null; created_by: string };
type AuditRow = { at: string; user: string | null; company_id: string | null; action: string; details: Record<string, unknown> };
type AiStatus = {
  enabled: boolean;
  platform: string;
  region: string;
  secondary: string | null;
  models: Record<string, string>;
  tokens_used_this_month: number;
  monthly_budget: number | null;
  notice: string;
};
type Proposal = { target: string; code: string; change: string; value: string | null; valid_from: string | null; valid_to: string | null; source_url: string; rationale: string };

const CATEGORY_SV: Record<string, string> = {
  integrity: "Dataintegritet",
  vat: "Moms",
  payroll: "Lön",
  tax: "Skatt",
  closing: "Periodisering och bokslut",
  analytics: "Analys",
  aml: "PTL",
  reconciliation: "Avstämning",
};

export default function SettingsPage() {
  const [tab, setTab] = useState("rules");
  return (
    <AppShell>
      <div className="space-y-4">
        <h1 className="text-xl font-semibold">Regler och inställningar</h1>
        <Tabs
          tabs={[
            { id: "rules", label: "Regelkatalog" },
            { id: "rates", label: "Satser" },
            { id: "health", label: "Regelhälsa" },
            { id: "suppressions", label: "Undertryckningar" },
            { id: "audit", label: "Händelselogg" },
            { id: "ai", label: "AI" },
            { id: "watch", label: "Regelbevakning" },
          ]}
          active={tab}
          onChange={setTab}
        />
        {tab === "rules" && <RulesView />}
        {tab === "rates" && <RatesView />}
        {tab === "health" && <HealthView />}
        {tab === "suppressions" && <SuppressionsView />}
        {tab === "audit" && <AuditView />}
        {tab === "ai" && <AiView />}
        {tab === "watch" && <WatchView />}
      </div>
    </AppShell>
  );
}

function RulesView() {
  const data = useLoad<{ rules: Rule[] }>("/api/rules");
  const me = useMe();
  const [edit, setEdit] = useState<Rule | null>(null);
  if (data.error) return <ErrorBox error={data.error} />;
  if (!data.data) return <Loading />;
  const byCat = data.data.rules.reduce<Record<string, Rule[]>>((acc, r) => ((acc[r.category] ??= []).push(r), acc), {});
  return (
    <div className="space-y-4">
      {Object.entries(byCat).map(([cat, rules]) => (
        <Card key={cat} title={CATEGORY_SV[cat] ?? cat}>
          <table className="data">
            <thead>
              <tr><th>Kontroll</th><th>Allvar</th><th>Lagstöd</th><th>Giltig</th><th>Parametrar</th><th></th></tr>
            </thead>
            <tbody>
              {rules.map((r) => (
                <tr key={r.code}>
                  <td>
                    <div className="font-medium">{r.title}</div>
                    <div className="text-[12px] text-muted">{r.description}</div>
                    <div className="font-mono text-[11px] text-muted">{r.code} v{r.version} · granskad {r.reviewed_at} av {r.owner}</div>
                  </td>
                  <td><SeverityBadge severity={r.severity} /></td>
                  <td className="text-[12px]">{r.legal_basis}</td>
                  <td className="whitespace-nowrap text-[12px]">{r.valid_from}{r.valid_to ? ` – ${r.valid_to}` : " –"}</td>
                  <td className="font-mono text-[11px] text-muted">{Object.entries(r.params).map(([k, v]) => `${k}=${String(v)}`).join(", ")}</td>
                  <td>{me?.role === "ADMIN" && Object.keys(r.params).length > 0 && <Button variant="ghost" onClick={() => setEdit(r)}>Ändra</Button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}
      {edit && <ParamsEditor rule={edit} onClose={() => setEdit(null)} />}
    </div>
  );
}

function ParamsEditor({ rule, onClose }: { rule: Rule; onClose: () => void }) {
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(Object.entries(rule.params).map(([k, v]) => [k, String(v)])));
  const [err, setErr] = useState<unknown>(null);
  const [ok, setOk] = useState(false);
  return (
    <Card title={`Parametrar – ${rule.title}`} actions={<Button variant="secondary" onClick={onClose}>Stäng</Button>}>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {Object.keys(values).map((k) => (
          <Field key={k} label={k}>
            <input className={inputCls} value={values[k]} onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
          </Field>
        ))}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <Button
          onClick={() =>
            send(`/api/rules/${rule.code}/params`, "PUT", { params: values })
              .then(() => setOk(true))
              .catch(setErr)
          }
        >
          Spara för hela byrån
        </Button>
        {ok && <span className="text-[13px] text-ok">Sparat. Gäller från nästa granskning.</span>}
      </div>
      <div className="mt-2"><ErrorBox error={err} /></div>
    </Card>
  );
}

function RatesView() {
  const data = useLoad<{ rates: Rate[] }>("/api/rules");
  if (data.error) return <ErrorBox error={data.error} />;
  if (!data.data) return <Loading />;
  const today = new Date().toISOString().slice(0, 10);
  return (
    <Card title="Skattesatser och avgifter">
      <p className="mb-2 text-[13px] text-muted">Satserna har giltighetsdatum, så att tidigare perioder alltid granskas mot de regler som gällde då.</p>
      <table className="data">
        <thead>
          <tr><th>Sats</th><th className="num">Värde</th><th>Giltig från</th><th>Giltig till</th><th>Källa</th></tr>
        </thead>
        <tbody>
          {data.data.rates.map((r, i) => {
            const active = r.valid_from <= today && (!r.valid_to || r.valid_to >= today);
            return (
              <tr key={i} className={active ? "" : "text-muted"}>
                <td>
                  {r.note}
                  <div className="font-mono text-[11px] text-muted">{r.code}{active && " · gäller nu"}</div>
                </td>
                <td className="num">{(Number(r.value) * 100).toLocaleString("sv-SE", { maximumFractionDigits: 2 })} %</td>
                <td>{r.valid_from}</td>
                <td>{r.valid_to ?? "tills vidare"}</td>
                <td className="text-[12px]"><a className="text-brand hover:underline" href={r.source} target="_blank" rel="noreferrer noopener">Källa</a></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}

function HealthView() {
  const data = useLoad<Health[]>("/api/rules/health");
  if (data.error) return <ErrorBox error={data.error} />;
  if (!data.data) return <Loading />;
  return (
    <Card title="Regelhälsa – hur ofta leder fynden till åtgärd?">
      <p className="mb-2 text-[13px] text-muted">
        Precision = andel avgjorda fynd som ledde till åtgärd eller kundfråga. Regler med låg precision skapar brus och bör justeras eller undertryckas.
      </p>
      <table className="data">
        <thead>
          <tr><th>Regel</th><th className="num">Fynd</th><th className="num">Åtgärdade</th><th className="num">Bedömda OK</th><th className="num">Öppna</th><th className="num">Precision</th></tr>
        </thead>
        <tbody>
          {data.data.map((h) => {
            const p = h.precision === null ? null : Number(h.precision);
            return (
              <tr key={h.rule_code}>
                <td className="font-mono text-[12px]">{h.rule_code}</td>
                <td className="num">{h.total}</td>
                <td className="num">{h.actioned}</td>
                <td className="num">{h.not_actioned}</td>
                <td className="num">{h.open}</td>
                <td className={cx("num", p !== null && p < 0.3 && "text-high")}>{p === null ? "–" : `${Math.round((p <= 1 ? p * 100 : p))} %`}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}

function SuppressionsView() {
  const data = useLoad<Suppression[]>("/api/suppressions");
  const rules = useLoad<{ rules: Rule[] }>("/api/rules");
  const me = useMe();
  const [form, setForm] = useState({ rule_code: "", reason: "", expires_at: "", accounts: "", text_contains: "" });
  const [err, setErr] = useState<unknown>(null);
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_380px]">
      <Card title="Aktiva undertryckningar">
        <ErrorBox error={data.error} />
        {!data.data ? (
          <Loading />
        ) : data.data.length === 0 ? (
          <Empty>Inga undertryckningar.</Empty>
        ) : (
          <table className="data">
            <thead>
              <tr><th>Regel</th><th>Villkor</th><th>Motivering</th><th>Gäller till</th><th>Av</th></tr>
            </thead>
            <tbody>
              {data.data.map((s) => (
                <tr key={s.id}>
                  <td className="font-mono text-[12px]">{s.rule_code}</td>
                  <td className="text-[12px]">
                    {s.company_id ? "En kund" : "Hela byrån"}
                    {s.accounts.length > 0 && ` · konton ${s.accounts.join(", ")}`}
                    {s.text_contains && ` · text innehåller "${s.text_contains}"`}
                  </td>
                  <td>{s.reason}</td>
                  <td>{s.expires_at ?? "–"}</td>
                  <td className="text-[12px] text-muted">{s.created_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {me?.permissions.write && (
        <Card title="Ny undertryckning">
          <div className="space-y-2">
            <Field label="Regel">
              <select className={inputCls} value={form.rule_code} onChange={(e) => setForm({ ...form, rule_code: e.target.value })}>
                <option value="">Välj regel</option>
                {rules.data?.rules.filter((r) => r.category !== "aml").map((r) => <option key={r.code} value={r.code}>{r.title}</option>)}
              </select>
            </Field>
            <Field label="Motivering (krävs)"><textarea className={cx(inputCls, "h-20")} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></Field>
            <Field label="Gäller till (krävs)" hint="Undertryckningar måste ha ett slutdatum så att de omprövas."><input type="date" className={inputCls} value={form.expires_at} onChange={(e) => setForm({ ...form, expires_at: e.target.value })} /></Field>
            <Field label="Bara dessa konton (valfritt)"><input className={inputCls} value={form.accounts} onChange={(e) => setForm({ ...form, accounts: e.target.value })} /></Field>
            <Field label="Bara när texten innehåller (valfritt)"><input className={inputCls} value={form.text_contains} onChange={(e) => setForm({ ...form, text_contains: e.target.value })} /></Field>
            <ErrorBox error={err} />
            <Button
              disabled={!form.rule_code || form.reason.trim().length < 3 || !form.expires_at}
              onClick={() =>
                send("/api/suppressions", "POST", {
                  rule_code: form.rule_code,
                  reason: form.reason,
                  expires_at: form.expires_at,
                  accounts: form.accounts.split(/[\s,;]+/).map(Number).filter((n) => n > 0),
                  text_contains: form.text_contains || null,
                })
                  .then(() => {
                    setForm({ rule_code: "", reason: "", expires_at: "", accounts: "", text_contains: "" });
                    data.reload();
                  })
                  .catch(setErr)
              }
            >
              Spara
            </Button>
            <p className="text-[11px] text-muted">PTL-signaler kan inte undertryckas.</p>
          </div>
        </Card>
      )}
    </div>
  );
}

function AuditView() {
  const data = useLoad<AuditRow[]>("/api/audit?limit=300");
  if (data.error) return <ErrorBox error={data.error} />;
  if (!data.data) return <Loading />;
  return (
    <Card title="Händelselogg">
      <p className="mb-2 text-[13px] text-muted">Loggen kan inte ändras eller raderas (skyddas i databasen). Den ingår i granskningsdokumentationen per kund.</p>
      <table className="data">
        <thead>
          <tr><th>Tid</th><th>Användare</th><th>Händelse</th><th>Detaljer</th></tr>
        </thead>
        <tbody>
          {data.data.map((e, i) => (
            <tr key={i}>
              <td className="whitespace-nowrap">{dateTime(e.at)}</td>
              <td className="text-[12px]">{e.user ?? "system"}</td>
              <td className="font-mono text-[12px]">{e.action}</td>
              <td className="max-w-[480px] truncate font-mono text-[11px] text-muted" title={JSON.stringify(e.details)}>{JSON.stringify(e.details)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function AiView() {
  const data = useLoad<AiStatus>("/api/ai/status");
  if (data.error) return <ErrorBox error={data.error} />;
  if (!data.data) return <Loading />;
  const d = data.data;
  const used = d.monthly_budget ? Math.round((d.tokens_used_this_month / d.monthly_budget) * 100) : null;
  return (
    <Card title="AI-tjänst">
      <dl className="grid max-w-xl grid-cols-2 gap-y-1 text-[13px]">
        <dt className="text-muted">Status</dt>
        <dd>{d.enabled ? "Aktiverad" : "Avstängd – regelbaserade texter används"}</dd>
        <dt className="text-muted">Plattform</dt>
        <dd>{d.platform} ({d.region}){d.secondary && ` · reserv: ${d.secondary}`}</dd>
        {Object.entries(d.models).map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">Modell ({k === "strong" ? "analys" : k === "medium" ? "texter" : "klassning"})</dt>
            <dd className="font-mono text-[12px]">{v}</dd>
          </div>
        ))}
        <dt className="text-muted">Förbrukning denna månad</dt>
        <dd>
          {d.tokens_used_this_month.toLocaleString("sv-SE")} tokens
          {used !== null && ` (${used} % av budget)`}
        </dd>
      </dl>
      <p className="mt-3 flex items-center gap-2 text-[12px] text-muted"><AiBadge /> {d.notice}</p>
      <p className="mt-1 text-[12px] text-muted">Personnamn maskeras innan anrop. Data används inte för att träna modeller och behandlas inom vald region.</p>
    </Card>
  );
}

function WatchView() {
  const me = useMe();
  const proposals = useLoad<{ id: string; source_url: string; proposals: Proposal[]; status: string; created_at: string }[]>("/api/rules/proposals");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <div className="space-y-4">
      {me?.role === "ADMIN" && (
        <Card title="Analysera ny information från Skatteverket">
          <p className="mb-2 text-[13px] text-muted">
            Klistra in text från en nyhet eller ett ställningstagande. AI föreslår ändringar i satser och regler – förslagen måste granskas av en domänexpert innan katalogen uppdateras.
          </p>
          <div className="space-y-2">
            <Field label="Källa (URL)"><input className={inputCls} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.skatteverket.se/…" /></Field>
            <Field label="Text"><textarea className={cx(inputCls, "h-32")} value={text} onChange={(e) => setText(e.target.value)} /></Field>
            <ErrorBox error={err} />
            <Button
              disabled={busy || !url || text.length < 20}
              onClick={() => {
                setBusy(true);
                send("/api/rules/watch", "POST", { source_url: url, source_text: text })
                  .then(() => {
                    setText("");
                    proposals.reload();
                  })
                  .catch(setErr)
                  .finally(() => setBusy(false));
              }}
            >
              {busy ? "Analyserar…" : "Analysera"}
            </Button>
          </div>
        </Card>
      )}
      <Card title="Förslag på regeländringar">
        <ErrorBox error={proposals.error} />
        {!proposals.data ? (
          <Loading />
        ) : proposals.data.length === 0 ? (
          <Empty>Inga förslag.</Empty>
        ) : (
          <div className="space-y-3">
            {proposals.data.map((p) => (
              <div key={p.id} className="rounded-md border border-line p-3">
                <div className="flex items-center gap-2 text-[12px] text-muted">
                  <AiBadge /> {dateTime(p.created_at)} · <a className="text-brand hover:underline" href={p.source_url} target="_blank" rel="noreferrer noopener">{p.source_url}</a>
                </div>
                {p.proposals.length === 0 ? (
                  <p className="mt-1 text-muted">Inga ändringar föreslogs.</p>
                ) : (
                  <ul className="mt-1 space-y-1 text-[13px]">
                    {p.proposals.map((x, i) => (
                      <li key={i}>
                        <span className="font-mono text-[12px]">{x.change} {x.target} {x.code}</span>
                        {x.value && ` → ${x.value}`}
                        {(x.valid_from || x.valid_to) && ` (${x.valid_from ?? ""} – ${x.valid_to ?? ""})`}
                        <div className="text-[12px] text-muted">{x.rationale}</div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
