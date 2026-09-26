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
type AiProvider = { role: string; platform: string; label: string; models: Record<string, string>; ready: boolean; problem: string | null };
type AiStatus = {
  enabled: boolean;
  requested: boolean;
  switched_off: boolean;
  problem: string | null;
  platform: string | null;
  region: string | null;
  secondary: string | null;
  models: Record<string, string>;
  providers: AiProvider[];
  keys: { anthropic: boolean; openai: boolean };
  tokens_used_this_month: number;
  monthly_budget: number | null;
  test_mode: boolean;
  test_max_output_tokens: number | null;
  test_max_tool_calls: number | null;
  notice: string;
};
type AiCheckStep = { name: string; ok: boolean; model?: string; answer?: string; error?: string; tokens: number; seconds: number };
type AiCheck = { ok: boolean; results: (Omit<AiProvider, "ready" | "problem"> & { ok: boolean; error: string | null; checks: AiCheckStep[] })[] };
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

const TIER_SV: Record<string, string> = { strong: "analys", medium: "texter", small: "klassning" };

function AiView() {
  const me = useMe();
  const data = useLoad<AiStatus>("/api/ai/status");
  const [check, setCheck] = useState<AiCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  if (data.error) return <ErrorBox error={data.error} />;
  if (!data.data) return <Loading />;
  const d = data.data;
  const used = d.monthly_budget ? Math.round((d.tokens_used_this_month / d.monthly_budget) * 100) : null;
  const runCheck = () => {
    setChecking(true);
    setErr(null);
    send<AiCheck>("/api/ai/check", "POST")
      .then((r) => {
        setCheck(r);
        data.reload();
      })
      .catch(setErr)
      .finally(() => setChecking(false));
  };
  return (
    <div className="space-y-4">
      <Card
        title="AI-tjänst"
        actions={me?.role === "ADMIN" && d.requested ? (
          <Button variant="secondary" disabled={checking} onClick={runCheck}>{checking ? "Testar…" : "Testa AI"}</Button>
        ) : undefined}
      >
        <dl className="grid max-w-2xl grid-cols-[minmax(0,12rem)_1fr] gap-x-3 gap-y-1 text-[13px]">
          <dt className="text-muted">Status</dt>
          <dd>
            {d.enabled ? "Aktiverad – AI används som standard" : d.switched_off ? "Avstängd (RAI_AI_ENABLED=false) – regelbaserade texter används" : `Kan inte starta – ${d.problem ?? "kontrollera AI-nyckel och serverkonfiguration"}`}
          </dd>
          {d.providers.map((p) => (
            <div key={p.role} className="contents">
              <dt className="text-muted">{p.role === "primär" ? "Används först" : "Reserv vid tillfälligt fel"}</dt>
              <dd>
                {p.label}
                {Object.keys(p.models).length > 0 && <span className="ml-1 font-mono text-[12px] text-muted">({Array.from(new Set(Object.values(p.models))).join(", ")})</span>}
                {!p.ready && <span className="ml-1 text-high">– {p.problem}</span>}
              </dd>
            </div>
          ))}
          <dt className="text-muted">Nycklar</dt>
          <dd>Anthropic: {d.keys.anthropic ? "finns" : "saknas"} · OpenAI: {d.keys.openai ? "finns" : "saknas"}</dd>
          <dt className="text-muted">Läge</dt>
          <dd>{d.test_mode ? "Testläge – korta svar, ingen reserv" : "Full drift"}</dd>
          {Object.entries(d.models).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted">Modell ({TIER_SV[k] ?? k})</dt>
              <dd className="font-mono text-[12px]">{v}</dd>
            </div>
          ))}
          <dt className="text-muted">Förbrukning denna månad</dt>
          <dd>
            {d.tokens_used_this_month.toLocaleString("sv-SE")} tokens
            {d.monthly_budget !== null && ` av ${d.monthly_budget.toLocaleString("sv-SE")}`}
            {used !== null && ` (${used} %)`}
          </dd>
          {d.test_mode && <>
            <dt className="text-muted">Testgränser</dt>
            <dd>{d.test_max_output_tokens?.toLocaleString("sv-SE")} utdata-tokens/anrop · {d.test_max_tool_calls} verktygsanrop</dd>
          </>}
        </dl>
        {!d.keys.anthropic && !d.keys.openai && (
          <p className="mt-3 text-[12px] text-muted">Lägg <span className="font-mono">ANTHROPIC_API_KEY</span> och/eller <span className="font-mono">OPENAI_API_KEY</span> i <span className="font-mono">.env</span> på servern och starta om – då används AI automatiskt.</p>
        )}
        <p className="mt-3 flex items-center gap-2 text-[12px] text-muted"><AiBadge /> {d.notice}</p>
        <p className="mt-1 text-[12px] text-muted">Personnamn maskeras innan anrop, lönerader och PTL skickas aldrig. Kontrollera respektive leverantörs datavillkor före användning med riktiga kunduppgifter. Tokenbudgeten är inte ett exakt kostnadstak; sätt även en utgiftsgräns hos leverantören.</p>
      </Card>
      <ErrorBox error={err} />
      {check && (
        <Card title={check.ok ? "AI-test: allt fungerar" : "AI-test: något fungerar inte"}>
          <p className="mb-2 text-[12px] text-muted">Korta provanrop utan kunddata: ett svar i JSON-format och ett anrop till ett läsverktyg (samma väg som AI-analytikern).</p>
          <div className="space-y-3">
            {check.results.map((r) => (
              <div key={r.role} className="rounded-md border border-line p-3">
                <div className="mb-1 font-medium">{r.ok ? "✓" : "✗"} {r.label} <span className="text-[12px] font-normal text-muted">({r.role})</span></div>
                {r.error && <p className="text-[13px] text-high">{r.error}</p>}
                <ul className="space-y-0.5 text-[13px]">
                  {r.checks.map((c) => (
                    <li key={c.name} className={c.ok ? "text-ok" : "text-high"}>
                      {c.ok ? "✓" : "✗"} {c.name}: {c.ok ? `${c.model ?? ""} · ${c.seconds} s · ${c.tokens.toLocaleString("sv-SE")} tokens${c.answer ? ` – ”${c.answer}”` : ""}` : c.error ?? `oväntat svar: ${c.answer ?? ""}`}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
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
