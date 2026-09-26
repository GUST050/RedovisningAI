"use client";

import { useState } from "react";
import { useMe } from "@/components/AppShell";
import { AiBadge, Button, Card, Empty, ErrorBox, Field, Loading, cx, inputCls } from "@/components/ui";
import { send, useLoad } from "@/lib/api";
import type { Company } from "./shared";

type Resolution = {
  id: string;
  rule_code: string;
  description: string;
  decision: string;
  rationale: string | null;
  decided_by: string;
  decided_at: string;
  valid_until: string | null;
  times_reused: number;
  example_title: string | null;
};

type MappingSuggestion = { account: number; legal_line: string; category: string; confidence: number; rationale: string };

const numList = (s: string) => s.split(/[\s,;]+/).map(Number).filter((n) => Number.isInteger(n) && n > 0);
const strList = (s: string) => s.split(/[,;\n]+/).map((x) => x.trim()).filter(Boolean);

export function SettingsTab({ base, company, onSaved }: { base: string; company: Company; onSaved: () => void }) {
  const me = useMe();
  const write = !!me?.permissions.write;
  const [form, setForm] = useState({
    name: company.name,
    org_number: company.org_number ?? "",
    legal_form: company.legal_form ?? "AB",
    industry: company.industry ?? "",
    vat_period: company.vat_period ?? "quarter",
    food_retail: company.food_retail,
    materiality: company.materiality,
    has_overdraft: company.has_overdraft,
    accounting_method_override: company.accounting_method_override ?? "",
    manual_series: (company.settings?.manual_series ?? []).join(", "),
    suspense_accounts: (company.settings?.suspense_accounts ?? []).join(", "),
    person_names: (company.settings?.person_names ?? []).join(", "),
  });
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const set = (k: keyof typeof form, v: string | boolean) => {
    setSaved(false);
    setForm((f) => ({ ...f, [k]: v }));
  };

  const save = () =>
    send(`${base.replace(/\/$/, "")}`, "PATCH", {
      name: form.name,
      org_number: form.org_number || null,
      legal_form: form.legal_form,
      industry: form.industry || null,
      vat_period: form.vat_period,
      food_retail: form.food_retail,
      materiality: form.materiality,
      has_overdraft: form.has_overdraft,
      accounting_method_override: form.accounting_method_override,
      manual_series: strList(form.manual_series),
      suspense_accounts: numList(form.suspense_accounts),
      person_names: strList(form.person_names),
    })
      .then(() => {
        setSaved(true);
        onSaved();
      })
      .catch(setErr);

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <Card title="Kunduppgifter och kontrollinställningar">
        <fieldset disabled={!write} className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Field label="Namn"><input className={inputCls} value={form.name} onChange={(e) => set("name", e.target.value)} /></Field>
          <Field label="Organisationsnummer"><input className={inputCls} value={form.org_number} onChange={(e) => set("org_number", e.target.value)} /></Field>
          <Field label="Bolagsform">
            <select className={inputCls} value={form.legal_form} onChange={(e) => set("legal_form", e.target.value)}>
              {["AB", "EF", "HB", "KB", "EK", "IDEELL"].map((x) => <option key={x}>{x}</option>)}
            </select>
          </Field>
          <Field label="Bransch"><input className={inputCls} value={form.industry} onChange={(e) => set("industry", e.target.value)} /></Field>
          <Field label="Momsperiod">
            <select className={inputCls} value={form.vat_period} onChange={(e) => set("vat_period", e.target.value)}>
              <option value="month">Månad</option>
              <option value="quarter">Kvartal</option>
              <option value="year">År</option>
            </select>
          </Field>
          <Field label="Bokföringsmetod" hint="Normalt identifieras metoden automatiskt.">
            <select className={inputCls} value={form.accounting_method_override} onChange={(e) => set("accounting_method_override", e.target.value)}>
              <option value="">Automatiskt</option>
              <option value="invoice">Fakturametoden</option>
              <option value="cash">Kontantmetoden</option>
            </select>
          </Field>
          <Field label="Väsentlighetsbelopp (kr)" hint="Fynd under beloppet får lägre allvarlighet.">
            <input className={inputCls} value={form.materiality} onChange={(e) => set("materiality", e.target.value)} />
          </Field>
          <Field label="Manuella verifikationsserier" hint="T.ex. A, M – används av kontrollen för luckor i nummerserier.">
            <input className={inputCls} value={form.manual_series} onChange={(e) => set("manual_series", e.target.value)} />
          </Field>
          <Field label="Avräknings-/OBS-konton" hint="Konton som ska vara noll vid periodens slut, t.ex. 1790, 2999.">
            <input className={inputCls} value={form.suspense_accounts} onChange={(e) => set("suspense_accounts", e.target.value)} />
          </Field>
          <Field label="Personnamn att maskera för AI" hint="Namn på ägare/anställda som förekommer i verifikationstexter. Ersätts med koder innan AI-anrop.">
            <input className={inputCls} value={form.person_names} onChange={(e) => set("person_names", e.target.value)} />
          </Field>
          <label className="flex items-center gap-2 text-[13px]">
            <input type="checkbox" checked={form.food_retail} onChange={(e) => set("food_retail", e.target.checked)} /> Säljer livsmedel (kontroll av sänkt matmoms)
          </label>
          <label className="flex items-center gap-2 text-[13px]">
            <input type="checkbox" checked={form.has_overdraft} onChange={(e) => set("has_overdraft", e.target.checked)} /> Har checkräkningskredit
          </label>
        </fieldset>
        <div className="mt-3 flex items-center gap-3">
          {write && <Button onClick={save}>Spara</Button>}
          {saved && <span className="text-[13px] text-ok">Sparat. Kör granskningen igen för att tillämpa ändringarna.</span>}
        </div>
        <div className="mt-2"><ErrorBox error={err} /></div>
      </Card>
      <div className="space-y-4">
        <MemoryCard base={base} write={write} />
        <MappingCard base={base} write={write} />
      </div>
    </div>
  );
}

function MemoryCard({ base, write }: { base: string; write: boolean }) {
  const mem = useLoad<Resolution[]>(`${base}/memory`);
  return (
    <Card title="Kundminne">
      <p className="mb-2 text-[13px] text-muted">Bedömningar som återanvänds som förslag när liknande fynd dyker upp igen. Förslagen fattar aldrig beslut själva.</p>
      <ErrorBox error={mem.error} />
      {!mem.data ? (
        <Loading />
      ) : mem.data.length === 0 ? (
        <Empty>Inga sparade bedömningar.</Empty>
      ) : (
        <table className="data">
          <thead>
            <tr><th>Mönster</th><th>Bedömning</th><th>Av</th><th className="num">Återanvänd</th><th>Gäller till</th><th></th></tr>
          </thead>
          <tbody>
            {mem.data.map((r) => (
              <tr key={r.id}>
                <td>
                  {r.example_title ?? r.description}
                  <div className="font-mono text-[11px] text-muted">{r.rule_code}</div>
                </td>
                <td>{r.rationale ?? (r.decision === "ACCEPTED_OK" ? "Bedömt OK" : "Åtgärdat")}</td>
                <td className="text-[12px] text-muted">{r.decided_by}</td>
                <td className="num">{r.times_reused}</td>
                <td className="text-[12px]">{r.valid_until ?? "tills vidare"}</td>
                <td>
                  {write && (
                    <button className="text-[12px] text-high hover:underline" onClick={() => send(`${base}/memory/${r.id}`, "DELETE").then(mem.reload)}>
                      Glöm
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function MappingCard({ base, write }: { base: string; write: boolean }) {
  const [data, setData] = useState<{ suggestions: MappingSuggestion[]; source: string; ai_note?: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [done, setDone] = useState<Set<number>>(new Set());
  return (
    <Card
      title="Kontomappning"
      actions={
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            fetch(`${base}/mapping/suggestions`, { credentials: "same-origin" })
              .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
              .then(setData)
              .catch(setErr)
              .finally(() => setBusy(false));
          }}
        >
          {busy ? "Analyserar…" : "Hitta okända konton"}
        </Button>
      }
    >
      <p className="mb-2 text-[13px] text-muted">
        Konton utanför BAS-planen mappas till rätt rad i resultat- och balansräkningen. AI föreslår, du bekräftar – inget ändras utan ditt klick.
      </p>
      <ErrorBox error={err} />
      {data && (data.suggestions.length === 0 ? (
        <p className="text-[13px] text-ok">Alla konton som används följer BAS-planen.</p>
      ) : (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2"><AiBadge source={data.source === "ai" ? "ai" : "rules"} note={data.ai_note} /></div>
          <table className="data">
            <thead>
              <tr><th>Konto</th><th>Föreslagen rad</th><th>Kategori</th><th>Säkerhet</th><th></th></tr>
            </thead>
            <tbody>
              {data.suggestions.map((s) => (
                <tr key={s.account}>
                  <td>{s.account}</td>
                  <td>{s.legal_line}<div className="text-[11px] text-muted">{s.rationale}</div></td>
                  <td>{s.category}</td>
                  <td className={cx("num", s.confidence < 0.7 && "text-medium")}>{Math.round(s.confidence * 100)} %</td>
                  <td>
                    {write && !done.has(s.account) && (
                      <Button
                        variant="ghost"
                        onClick={() =>
                          Promise.all([
                            send(`${base}/mapping`, "POST", { kind: "statement", account: s.account, target: s.legal_line, source: "ai_confirmed" }),
                            send(`${base}/mapping`, "POST", { kind: "category", account: s.account, target: s.category, source: "ai_confirmed" }),
                          ])
                            .then(() => setDone((d) => new Set(d).add(s.account)))
                            .catch(setErr)
                        }
                      >
                        Bekräfta
                      </Button>
                    )}
                    {done.has(s.account) && <span className="text-[12px] text-ok">Sparad</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ))}
    </Card>
  );
}
