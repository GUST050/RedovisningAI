"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { Button, Card, ErrorBox, Field, Loading, cx, inputCls } from "@/components/ui";
import {
  type DifferenceItem,
  type MetricComparisons,
  type MetricStructure,
  type Overview,
  type ReportItems,
  downloadPost,
  useLoad,
} from "@/lib/api";
import { monthLabel, pct, sek } from "@/lib/format";
import { useClient } from "./shared";

type Kind = "month" | "quarter" | "ytd" | "fy" | "r12";
type Mode = "yoy" | "previous" | "custom";
type Audience = "internal" | "client";
type Format = "pdf" | "docx" | "xlsx";
type Picked = { title: string; comment: string; series?: string; count?: number };

const KINDS: { id: Kind; label: string }[] = [
  { id: "month", label: "Månad" },
  { id: "quarter", label: "Kvartal" },
  { id: "ytd", label: "Hittills i år" },
  { id: "fy", label: "Räkenskapsår" },
  { id: "r12", label: "Rullande 12 mån" },
];

const SERIES: { kind: string; label: string; max: number; default: number }[] = [
  { kind: "months", label: "Månader i följd", max: 36, default: 12 },
  { kind: "quarters", label: "Kvartal i följd", max: 16, default: 8 },
  { kind: "fiscal_years", label: "Räkenskapsår", max: 10, default: 3 },
  { kind: "same_month", label: "Samma månad varje år", max: 10, default: 3 },
  { kind: "ytd", label: "Hittills i år, år för år", max: 10, default: 3 },
  { kind: "r12", label: "Rullande 12 månader", max: 36, default: 12 },
];

const KIND_BADGE: Record<string, string> = { metric: "Nyckeltal", line: "Rad", category: "Kostnad", finding: "Analysfynd" };

// ------------------------------------------------------------------------ hjälpfunktioner

function quarterOf(m: string): string {
  const [y, mm] = m.split("-").map(Number);
  return `${y}-Q${Math.floor((mm - 1) / 3) + 1}`;
}

type FiscalYear = { start: string; end: string };

function fiscalYearOf(m: string, years: FiscalYear[]): FiscalYear | null {
  const day = `${m}-01`;
  return years.find((fy) => fy.start <= day && day <= fy.end) ?? null;
}

function fyLabel(fy: FiscalYear): string {
  const s = fy.start.slice(0, 4);
  const e = fy.end.slice(0, 4);
  return fy.start.slice(5, 10) === "01-01" && s === e ? s : `${s}/${e.slice(2)}`;
}

function currentSpec(kind: Kind, month: string, years: FiscalYear[]): string | null {
  switch (kind) {
    case "month":
      return month;
    case "quarter":
      return quarterOf(month);
    case "ytd":
      return `YTD:${month}`;
    case "fy": {
      const fy = fiscalYearOf(month, years);
      return fy ? `FY:${fy.start.slice(0, 7)}` : null;
    }
    case "r12":
      return `R12:${month}`;
  }
}

function candidates(kind: Kind, spec: string, month: string, months: string[], years: FiscalYear[]): { spec: string; label: string }[] {
  const others = (list: { spec: string; label: string }[]) => list.filter((c) => c.spec !== spec);
  switch (kind) {
    case "month":
      return others(months.map((m) => ({ spec: m, label: monthLabel(m) })));
    case "quarter": {
      const seen = Array.from(new Set(months.map(quarterOf)));
      return others(seen.map((q) => ({ spec: q, label: q.replace(/^(\d{4})-Q(\d)$/, "Q$2 $1") })));
    }
    case "ytd":
      return others(months.filter((m) => m.slice(5) === month.slice(5)).map((m) => ({ spec: `YTD:${m}`, label: `Hittills i år t.o.m. ${monthLabel(m)}` })));
    case "fy":
      return others(years.map((fy) => ({ spec: `FY:${fy.start.slice(0, 7)}`, label: `Räkenskapsår ${fyLabel(fy)}` }))).reverse();
    case "r12":
      return others(months.map((m) => ({ spec: `R12:${m}`, label: `12 mån t.o.m. ${monthLabel(m)}` })));
  }
}

function value(v: string | null, unit: string): string {
  if (v === null) return "–";
  if (unit === "SEK") return sek(v);
  if (unit === "percent" || unit === "pp") return pct(v, false);
  return v;
}

function change(v: string | null, unit: string): string {
  if (v === null) return "–";
  if (unit === "SEK") return sek(v, { signed: true });
  if (unit === "percent" || unit === "pp") return pct(v).replace(" %", " p.e.");
  return Number(v) > 0 ? `+${v}` : v;
}

function tone(v: string | null, better: string | null | undefined): string {
  if (v === null || better === "neutral" || !better) return "text-ink";
  const n = Number(v);
  if (n === 0) return "text-muted";
  const good = better === "lower" ? n < 0 : n > 0;
  return good ? "text-ok" : "text-high";
}

// ------------------------------------------------------------------------ fliken

export function ComparisonTab() {
  const { base, month, months, view } = useClient();
  const overview = useLoad<Overview>(`${base}/overview?period=${month}`);
  const years = useMemo(() => (overview.data?.fiscal_years ?? []).map((y) => ({ start: y.start, end: y.end })), [overview.data]);
  const [kind, setKind] = useState<Kind>(view === "YTD" ? "ytd" : view === "R12" ? "r12" : "month");
  const [mode, setMode] = useState<Mode>("yoy");
  const [compare, setCompare] = useState("");
  const [audience, setAudience] = useState<Audience>("internal");
  const [picked, setPicked] = useState<Record<string, Picked>>({});

  // Vyväljaren i sidhuvudet (månad / hittills i år / R12) styr även den här fliken.
  useEffect(() => {
    setKind(view === "YTD" ? "ytd" : view === "R12" ? "r12" : "month");
    setCompare("");
  }, [view]);

  const spec = currentSpec(kind, month, years);
  const options = spec ? candidates(kind, spec, month, months, years) : [];
  const compareSpec = mode === "custom" ? compare || options[0]?.spec || "" : "";
  const query = spec
    ? `period=${encodeURIComponent(spec)}&mode=${mode === "custom" ? "yoy" : mode}${compareSpec ? `&compare=${encodeURIComponent(compareSpec)}` : ""}`
    : null;

  if (overview.error) return <ErrorBox error={overview.error} />;
  if (!overview.data) return <Loading />;

  return (
    <div className="space-y-4">
      <Card title="Välj jämförelse">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
          <div>
            <span className="mb-1 block text-[12px] font-medium text-muted">Period</span>
            <div className="flex flex-wrap overflow-hidden rounded-md border border-line bg-white text-[12px]" role="group" aria-label="Periodtyp">
              {KINDS.map((k) => (
                <button
                  key={k.id}
                  type="button"
                  aria-pressed={kind === k.id}
                  onClick={() => {
                    setKind(k.id);
                    setCompare("");
                  }}
                  className={cx("focus-ring whitespace-nowrap px-2.5 py-1.5", kind === k.id ? "bg-brand text-white" : "text-muted hover:bg-canvas")}
                >
                  {k.label}
                </button>
              ))}
            </div>
          </div>
          <Field label="Jämför med">
            <select aria-label="Jämförelseläge" className={cx(inputCls, "lg:w-64")} value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
              <option value="yoy">Samma period föregående år</option>
              <option value="previous">Föregående period</option>
              <option value="custom">Välj period …</option>
            </select>
          </Field>
          {mode === "custom" && (
            <Field label="Jämförelseperiod">
              <select aria-label="Jämförelseperiod" className={cx(inputCls, "lg:w-64")} value={compareSpec} onChange={(e) => setCompare(e.target.value)}>
                {options.map((o) => (
                  <option key={o.spec} value={o.spec}>{o.label}</option>
                ))}
              </select>
            </Field>
          )}
          <p className="text-[12px] text-muted lg:ml-auto lg:max-w-sm">
            Månaden väljs uppe till höger. Alla belopp räknas fram ur bokföringen; saknad data visas som saknad, aldrig som noll.
          </p>
        </div>
      </Card>

      {!spec || !query ? (
        <Card><p className="text-muted">Räkenskapsåret för vald månad finns inte i bokföringen.</p></Card>
      ) : (
        <>
          <KeyFigures key={`kf-${query}`} base={base} query={query} />
          <StructurePanel
            base={base}
            month={month}
            onAdd={(id, item) => setPicked((old) => ({ ...old, [id]: item }))}
            picked={picked}
          />
          <DifferencesPanel
            key={`diff-${query}-${audience}`}
            base={base}
            query={query}
            audience={audience}
            picked={picked}
            setPicked={setPicked}
          />
          <ReportPanel
            base={base}
            spec={spec}
            mode={mode}
            compareSpec={compareSpec}
            audience={audience}
            setAudience={setAudience}
            picked={picked}
            setPicked={setPicked}
          />
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------ nyckeltal

function KeyFigures({ base, query }: { base: string; query: string }) {
  const data = useLoad<MetricComparisons>(`${base}/metric-comparisons?${query}`);
  const d = data.data;
  return (
    <Card title={d?.labels ? `Nyckeltal – ${d.labels.current} mot ${d.labels.previous}` : "Nyckeltal"}>
      <ErrorBox error={data.error} />
      {!d && !data.error && <Loading />}
      {d && (
        <div className="space-y-2">
          <Notes warnings={d.warnings} notices={d.notices ?? []} />
          <div className="overflow-x-auto">
            <table className="data min-w-[640px]">
              <thead>
                <tr>
                  <th>Nyckeltal</th>
                  <th className="num">{d.labels?.current ?? d.periods.current}</th>
                  <th className="num">{d.labels?.previous ?? d.periods.previous}</th>
                  <th className="num">Förändring</th>
                  <th>Underlag</th>
                </tr>
              </thead>
              <tbody>
                {Object.values(d.metrics).map((m) => (
                  <tr key={m.code}>
                    <td title={m.formula}>{m.label}</td>
                    <td className="num">{value(m.current, m.unit)}</td>
                    <td className="num">{value(m.previous, m.unit)}</td>
                    <td className={cx("num", tone(m.change, m.better))}>{change(m.change, m.unit)}</td>
                    <td className="text-[12px] text-muted">{m.status === "CALCULATED" ? "" : m.status === "PARTIAL" ? "preliminärt" : "saknas"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );
}

function Notes({ warnings, notices }: { warnings: string[]; notices: string[] }) {
  if (!warnings.length && !notices.length) return null;
  return (
    <ul className="space-y-1 rounded-md bg-medium-soft px-3 py-2 text-[12px] text-medium">
      {[...warnings, ...notices].map((w, i) => (
        <li key={`${i}-${w}`}>{w}</li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------------------ uppbyggnad över tid

const STRUCTURE_METRICS: { code: string; label: string }[] = [
  { code: "operating_margin", label: "Rörelsemarginal" },
  { code: "gross_margin", label: "Bruttomarginal" },
  { code: "profit_margin", label: "Vinstmarginal" },
  { code: "operating_result", label: "Rörelseresultat" },
  { code: "ebitda", label: "EBITDA" },
  { code: "result_after_financial", label: "Resultat efter finansiella poster" },
  { code: "net_sales", label: "Nettoomsättning" },
  { code: "gross_profit", label: "Bruttovinst" },
  { code: "personnel_share", label: "Personalkostnader i % av omsättningen" },
  { code: "external_cost_share", label: "Externa kostnader i % av omsättningen" },
  { code: "equity_ratio", label: "Soliditet" },
  { code: "quick_ratio", label: "Kassalikviditet" },
  { code: "current_ratio", label: "Balanslikviditet" },
  { code: "working_capital", label: "Rörelsekapital" },
  { code: "cash", label: "Kassa och bank" },
  { code: "receivables", label: "Kundfordringar" },
  { code: "payables", label: "Leverantörsskulder" },
];

function StructurePanel({
  base,
  month,
  picked,
  onAdd,
}: {
  base: string;
  month: string;
  picked: Record<string, Picked>;
  onAdd: (id: string, item: Picked) => void;
}) {
  const [code, setCode] = useState("operating_margin");
  const [series, setSeries] = useState("months");
  const [count, setCount] = useState(12);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const seriesDef = SERIES.find((s) => s.kind === series) ?? SERIES[0];
  const path = `${base}/metric-structure/${code}?end=${encodeURIComponent(month)}&series=${series}&count=${Math.min(count, seriesDef.max)}`;
  const data = useLoad<MetricStructure>(path);
  const d = data.data;
  // En post per nyckeltal och serie, så att t.ex. både månader och räkenskapsår kan tas med.
  const id = `structure:${code}@${series}`;
  const already = picked[id]?.count === count;

  return (
    <Card
      title="Uppbyggnad över tid"
      actions={
        d && (
          <Button
            variant="secondary"
            disabled={already}
            onClick={() => onAdd(id, { title: `${d.label} – ${d.series_label.toLowerCase()}`, comment: picked[id]?.comment ?? "", series, count })}
          >
            {already ? "Tillagd i rapporten" : "Lägg till i rapporten"}
          </Button>
        )
      }
    >
      <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <Field label="Nyckeltal">
          <select aria-label="Nyckeltal för uppbyggnad" className={inputCls} value={code} onChange={(e) => setCode(e.target.value)}>
            {STRUCTURE_METRICS.map((m) => (
              <option key={m.code} value={m.code}>{m.label}</option>
            ))}
          </select>
        </Field>
        <Field label="Perioder">
          <select
            aria-label="Periodserie"
            className={inputCls}
            value={series}
            onChange={(e) => {
              const next = SERIES.find((s) => s.kind === e.target.value) ?? SERIES[0];
              setSeries(next.kind);
              setCount(next.default);
            }}
          >
            {SERIES.map((s) => (
              <option key={s.kind} value={s.kind}>{s.label}</option>
            ))}
          </select>
        </Field>
        <Field label="Antal">
          <select aria-label="Antal perioder" className={inputCls} value={count} onChange={(e) => setCount(Number(e.target.value))}>
            {Array.from({ length: seriesDef.max - 1 }, (_, i) => i + 2).map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </Field>
      </div>
      <ErrorBox error={data.error} />
      {!d && !data.error && <Loading />}
      {d && (
        <div className="space-y-3">
          <p className="text-[12px] text-muted">
            {d.formula}
            {d.base_label && ` Inom parentes: andel av ${d.base_label}.`} Klicka på en rad för att se kontona.
          </p>
          <Notes warnings={d.warnings} notices={[]} />
          <div className="overflow-x-auto">
            <table className="data min-w-[720px]">
              <thead>
                <tr>
                  <th>Post</th>
                  {d.periods.map((p) => (
                    <th key={p.spec} className="num" title={p.note ?? p.label}>
                      {p.short}
                      {p.open ? " *" : ""}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr className="font-semibold">
                  <td>{d.label}</td>
                  {d.periods.map((p) => (
                    <td key={p.spec} className="num">{p.status === "INSUFFICIENT_DATA" ? "saknas" : p.display}</td>
                  ))}
                </tr>
                {d.rows.map((row) => (
                  <Fragment key={row.code}>
                    <tr className="cursor-pointer" onClick={() => setOpen((o) => ({ ...o, [row.code]: !o[row.code] }))}>
                      <td>
                        <button type="button" aria-expanded={!!open[row.code]} className="focus-ring text-left text-brand hover:underline">
                          {open[row.code] ? "▾" : "▸"} {row.label}
                          {row.role === "denominator" && <span className="text-muted"> (nämnare)</span>}
                        </button>
                      </td>
                      {row.values.map((v, i) => (
                        <td key={i} className="num">
                          {v === null ? "–" : sek(v)}
                          {row.shares[i] !== null && <span className="block text-[11px] text-muted">{pct(row.shares[i], false)}</span>}
                        </td>
                      ))}
                    </tr>
                    {open[row.code] &&
                      row.accounts.map((a, k) => (
                        <tr key={`${row.code}-${a.account ?? k}`} className="text-[12px] text-muted">
                          <td className="pl-8">{a.account ? `${a.account} ${a.name}` : a.name}</td>
                          {a.values.map((v, i) => (
                            <td key={i} className="num">{v === null ? "–" : sek(v)}</td>
                          ))}
                        </tr>
                      ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          {d.periods.some((p) => p.open) && <p className="text-[11px] text-muted">* = perioden är inte avslutad.</p>}
          <Steps structure={d} />
        </div>
      )}
    </Card>
  );
}

function Steps({ structure }: { structure: MetricStructure }) {
  const unit = structure.unit === "SEK" ? "SEK" : "pp";
  const steps = structure.overall ? [...structure.steps, structure.overall] : structure.steps;
  return (
    <div>
      <h3 className="mb-1 text-[13px] font-semibold">Vad förändrade nyckeltalet mellan perioderna?</h3>
      <ul className="space-y-1 text-[12px]">
        {steps.map((s, i) => {
          const top = [...s.components].sort((a, b) => Math.abs(Number(b.effect)) - Math.abs(Number(a.effect))).filter((c) => Number(c.effect) !== 0).slice(0, 3);
          return (
            <li key={`${s.previous}-${s.current}-${i}`} className={cx(i === structure.steps.length && "border-t border-line pt-1 font-medium")}>
              <span className="text-muted">{s.previous_label} → {s.current_label}:</span>{" "}
              {s.change === null ? (
                <span className="text-medium">jämförelse saknas</span>
              ) : (
                <>
                  <span className={tone(s.change, structure.better)}>{change(s.change, unit)}</span>
                  {top.length > 0 && <span className="text-muted"> – störst: {top.map((c) => `${c.label.toLowerCase()} ${change(c.effect, unit)}`).join(", ")}</span>}
                </>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------------ viktigaste skillnader

function DifferencesPanel({
  base,
  query,
  audience,
  picked,
  setPicked,
}: {
  base: string;
  query: string;
  audience: Audience;
  picked: Record<string, Picked>;
  setPicked: (fn: (old: Record<string, Picked>) => Record<string, Picked>) => void;
}) {
  const data = useLoad<ReportItems>(`${base}/report-items?${query}&audience=${audience}`);
  const [showAll, setShowAll] = useState(false);
  const d = data.data;

  // Föreslagna poster förväljs när jämförelsen byts; tillagda serier över tid behålls.
  useEffect(() => {
    if (!d) return;
    setPicked((old) => {
      const kept: Record<string, Picked> = Object.fromEntries(Object.entries(old).filter(([id]) => id.startsWith("structure:")));
      for (const item of d.items) {
        if (d.recommended.includes(item.id)) kept[item.id] = { title: item.title, comment: old[item.id]?.comment ?? "" };
      }
      return kept;
    });
  }, [d, setPicked]);

  const visible = d ? d.items.filter((i) => showAll || i.recommended || picked[i.id]) : [];
  const toggle = (item: DifferenceItem) =>
    setPicked((old) => {
      const next = { ...old };
      if (next[item.id]) delete next[item.id];
      else next[item.id] = { title: item.title, comment: "" };
      return next;
    });

  return (
    <Card
      title="Viktigaste skillnaderna"
      actions={d && <span className="text-[12px] text-muted">{d.items.filter((i) => i.selectable).length} jämförbara poster</span>}
    >
      <ErrorBox error={data.error} />
      {!d && !data.error && <Loading />}
      {d && (
        <div className="space-y-3">
          <p className="text-[12px] text-muted">
            Rangordnat efter väsentlighet (förändring i förhållande till omsättning eller balansomslutning, belopp och relativ förändring).
            Högst en post per område föreslås. Kryssa i det som ska med i rapporten och skriv gärna en kommentar.
          </p>
          <Notes warnings={d.warnings} notices={d.notices} />
          <ul className="space-y-2">
            {visible.map((item) => (
              <li key={item.id} className={cx("rounded-md border p-3", picked[item.id] ? "border-brand bg-brand-soft/40" : "border-line bg-white")}>
                <div className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    className="mt-1 h-4 w-4"
                    aria-label={`Ta med ${item.title}`}
                    checked={!!picked[item.id]}
                    disabled={!item.selectable}
                    onChange={() => toggle(item)}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{item.title}</span>
                      <span className="rounded bg-low-soft px-1.5 py-0.5 text-[10px] text-low">{KIND_BADGE[item.kind] ?? item.kind}</span>
                      {item.recommended && <span className="rounded bg-ok-soft px-1.5 py-0.5 text-[10px] font-semibold text-ok">Föreslagen</span>}
                      {item.audience === "internal" && <span className="rounded bg-medium-soft px-1.5 py-0.5 text-[10px] text-medium">Bara intern</span>}
                      <span className="ml-auto text-[11px] text-muted" title={Object.entries(item.score_parts).map(([k, v]) => `${k}: ${v}`).join(", ")}>
                        poäng {item.score}
                      </span>
                    </div>
                    <p className="mt-1 text-[13px]">{item.summary}</p>
                    <p className="text-[11px] text-muted">{item.reason}{item.not_recommended ? ` · ${item.not_recommended}` : ""}</p>
                    {item.warnings.map((w, i) => (
                      <p key={`${i}-${w}`} className="text-[11px] text-medium">{w}</p>
                    ))}
                    {picked[item.id] && (
                      <textarea
                        aria-label={`Kommentar till ${item.title}`}
                        className={cx(inputCls, "mt-2 h-16")}
                        placeholder="Kommentar i rapporten (valfritt)"
                        maxLength={2000}
                        value={picked[item.id].comment}
                        onChange={(e) => setPicked((old) => ({ ...old, [item.id]: { ...old[item.id], comment: e.target.value } }))}
                      />
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
          <button type="button" className="focus-ring text-[12px] text-brand hover:underline" onClick={() => setShowAll((v) => !v)}>
            {showAll ? "Visa bara föreslagna och valda" : `Visa alla ${d.items.length} poster`}
          </button>
        </div>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------------ rapport

function ReportPanel({
  base,
  spec,
  mode,
  compareSpec,
  audience,
  setAudience,
  picked,
  setPicked,
}: {
  base: string;
  spec: string;
  mode: Mode;
  compareSpec: string;
  audience: Audience;
  setAudience: (a: Audience) => void;
  picked: Record<string, Picked>;
  setPicked: (fn: (old: Record<string, Picked>) => Record<string, Picked>) => void;
}) {
  const [format, setFormat] = useState<Format>("pdf");
  const [title, setTitle] = useState("");
  const [intro, setIntro] = useState("");
  const [appendix, setAppendix] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  // Skillnaderna först (i rangordning), utveckling över tid sist.
  const entries = Object.entries(picked).sort(([a], [b]) => Number(a.startsWith("structure:")) - Number(b.startsWith("structure:")));

  const create = () => {
    setBusy(true);
    setErr(null);
    const body = {
      period: spec,
      mode: mode === "custom" ? "yoy" : mode,
      compare: compareSpec || null,
      audience,
      format,
      title: title.trim() || null,
      intro: intro.trim() || null,
      include_key_figures: appendix,
      items: entries.map(([key, p]) => ({ id: key.split("@")[0], comment: p.comment.trim() || null, series: p.series ?? null, count: p.count ?? null })),
    };
    downloadPost(`${base}/reports/comparison`, body, `jamforelse.${format}`)
      .catch(setErr)
      .finally(() => setBusy(false));
  };

  return (
    <Card title="Skapa rapport">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="space-y-3">
          <div>
            <span className="mb-1 block text-[12px] font-medium text-muted">Mottagare</span>
            <div className="flex overflow-hidden rounded-md border border-line bg-white text-[12px]" role="group" aria-label="Mottagare">
              {(
                [
                  ["internal", "Intern (byrån)"],
                  ["client", "Kund"],
                ] as [Audience, string][]
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={audience === id}
                  onClick={() => setAudience(id)}
                  className={cx("focus-ring px-3 py-1.5", audience === id ? "bg-brand text-white" : "text-muted hover:bg-canvas")}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="mt-1 text-[11px] text-muted">
              {audience === "client"
                ? "Kundrapporten innehåller aldrig analysfynd, verifikationer eller enskilda lönekonton."
                : "Den interna rapporten kan innehålla analysfynd och största verifikationer."}
            </p>
          </div>
          <Field label="Rubrik (valfri)">
            <input className={inputCls} maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="t.ex. Kvartalsgenomgång" />
          </Field>
          <Field label="Inledning (valfri)">
            <textarea className={cx(inputCls, "h-24")} maxLength={4000} value={intro} onChange={(e) => setIntro(e.target.value)} />
          </Field>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Format">
              <select aria-label="Format" className={cx(inputCls, "w-36")} value={format} onChange={(e) => setFormat(e.target.value as Format)}>
                <option value="pdf">PDF</option>
                <option value="docx">Word</option>
                <option value="xlsx">Excel</option>
              </select>
            </Field>
            <label className="flex items-center gap-2 pb-2 text-[13px]">
              <input type="checkbox" checked={appendix} onChange={(e) => setAppendix(e.target.checked)} /> Bilaga med alla nyckeltal
            </label>
          </div>
        </div>
        <div className="space-y-2">
          <h3 className="text-[13px] font-semibold">I rapporten ({entries.length})</h3>
          {entries.length === 0 ? (
            <p className="text-[12px] text-muted">Inget valt ännu. Kryssa i skillnader ovan eller lägg till en utveckling över tid.</p>
          ) : (
            <ol className="list-decimal space-y-1 pl-5 text-[13px]">
              {entries.map(([id, p]) => (
                <li key={id}>
                  <span>{p.title}</span>
                  {p.series && <span className="text-muted"> ({p.count} perioder)</span>}
                  <button
                    type="button"
                    className="focus-ring ml-2 text-[11px] text-muted hover:text-high"
                    aria-label={`Ta bort ${p.title}`}
                    onClick={() =>
                      setPicked((old) => {
                        const next = { ...old };
                        delete next[id];
                        return next;
                      })
                    }
                  >
                    ta bort
                  </button>
                </li>
              ))}
            </ol>
          )}
          <ErrorBox error={err} />
          <Button disabled={busy || entries.length === 0} onClick={create}>
            {busy ? "Skapar …" : `Skapa ${audience === "client" ? "kundrapport" : "intern rapport"}`}
          </Button>
          <p className="text-[11px] text-muted">Siffrorna räknas om på servern från bokföringen när rapporten skapas. Ingen text genereras av AI.</p>
        </div>
      </div>
    </Card>
  );
}
