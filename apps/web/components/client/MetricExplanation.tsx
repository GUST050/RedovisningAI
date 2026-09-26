"use client";

import { useState } from "react";
import { Card, ErrorBox, Loading, cx } from "@/components/ui";
import { type AnalysisFinding, type AnalysisFindings, type MetricComparison, type MetricComparisons, type MetricEvidence, type MetricEvidenceRow, type MetricExplanation, useLoad } from "@/lib/api";
import { pct, sek } from "@/lib/format";
import { VoucherLink, useClient } from "./shared";

type Mode = "yoy" | "previous";

const STATUS: Record<string, string> = {
  CALCULATED: "Beräknad",
  PARTIAL: "Preliminär – underlaget är ofullständigt",
  INSUFFICIENT_DATA: "Jämförelse saknas",
  NOT_APPLICABLE: "Ej tillämpligt",
  ERROR: "Kunde inte beräknas",
};

function amount(value: string | null, unit: string, signed = false, isChange = false) {
  if (value === null) return "–";
  if (unit === "SEK") return sek(value, { signed });
  if (unit === "percent") return isChange ? `${pct(value, signed)} p.e.` : pct(value, signed);
  if (unit === "pp") return `${pct(value, signed)}${isChange ? " p.e." : ""}`;
  return value;
}

export function MetricExplanationPanel() {
  const { base, spec } = useClient();
  const [mode, setMode] = useState<Mode>("yoy");
  const [selectedCode, setSelectedCode] = useState<string | null>(null);

  return (
    <Card
      title="Nyckeltalsanalys"
      actions={
        <label className="flex items-center gap-2 text-[12px] text-muted">
          Jämför med
          <select
            aria-label="Jämförelseperiod"
            value={mode}
            onChange={(event) => {
              setMode(event.target.value as Mode);
              setSelectedCode(null);
            }}
            className="focus-ring rounded border border-line bg-white px-2 py-1 text-ink"
          >
            <option value="yoy">Samma period föregående år</option>
            <option value="previous">Föregående period</option>
          </select>
        </label>
      }
    >
      <MetricSummary
        key={`${base}-${spec}-${mode}`}
        base={base}
        spec={spec}
        mode={mode}
        selectedCode={selectedCode}
        onSelect={setSelectedCode}
      />
    </Card>
  );
}

function MetricSummary({
  base,
  spec,
  mode,
  selectedCode,
  onSelect,
}: {
  base: string;
  spec: string;
  mode: Mode;
  selectedCode: string | null;
  onSelect: (code: string | null) => void;
}) {
  const path = `${base}/metric-comparisons?period=${encodeURIComponent(spec)}&mode=${mode}`;
  const summary = useLoad<MetricComparisons>(path);
  const findings = useLoad<AnalysisFindings>(`${base}/analysis-findings?period=${encodeURIComponent(spec)}&mode=${mode}`);
  const selected = selectedCode ? summary.data?.metrics[selectedCode] : null;

  return (
    <>
      <ErrorBox error={summary.error} />
      {(summary.loading || (!summary.data && !summary.error)) && <Loading />}
      {summary.data && !summary.loading && !summary.error && (
        <div className="space-y-4">
          <p className="text-[12px] text-muted">
            {summary.data.periods.current} jämfört med {summary.data.periods.previous}. Beräkningar kommer från redovisningsmotorn.
          </p>
          {(summary.data.warnings.length > 0 || summary.data.status === "PARTIAL") && (
            <ul className="rounded-md bg-medium-soft px-3 py-2 text-[12px] text-medium">
              {[...summary.data.warnings, ...(summary.data.status === "PARTIAL" ? ["Jämförelseperioden är ofullständig."] : [])].map((warning, i) => <li key={`${i}-${warning}`}>{warning}</li>)}
            </ul>
          )}
          <ErrorBox error={findings.error} />
          {findings.data && <FindingCandidates data={findings.data} metrics={summary.data.metrics} onSelectMetric={onSelect} />}
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {Object.values(summary.data.metrics).map((metric) => (
              <MetricTile
                key={metric.code}
                metric={metric}
                selected={selectedCode === metric.code}
                onSelect={() => onSelect(selectedCode === metric.code ? null : metric.code)}
              />
            ))}
          </div>
          {selected && selectedCode && (
            <MetricDetail key={`${selectedCode}-${mode}-${spec}`} code={selectedCode} period={spec} mode={mode} summary={selected} base={base} />
          )}
        </div>
      )}
    </>
  );
}

function FindingCandidates({ data, metrics, onSelectMetric }: { data: AnalysisFindings; metrics: Record<string, MetricComparison>; onSelectMetric: (code: string) => void }) {
  return (
    <section aria-label="Prioriterade analyskandidater" className="rounded-md border border-line bg-canvas p-3">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-[13px] font-semibold">Prioriterade förändringar</h3>
        <span className="text-[11px] text-muted">Regelbaserade kandidater – inte automatiska slutsatser · {data.count} totalt</span>
      </div>
      {data.warnings.map((warning, i) => <p key={`${i}-${warning}`} className="mb-2 text-[12px] text-medium">{warning}</p>)}
      {data.top.length === 0 ? <p className="text-[12px] text-muted">Inga kandidater med tillräckligt underlag för perioden.</p> : (
        <ul className="space-y-1">
          {data.top.map((item) => <FindingRow key={item.group_key} item={item} metrics={metrics} onSelectMetric={onSelectMetric} />)}
        </ul>
      )}
      {data.others.length > 0 && (
        <details className="mt-2 text-[12px]">
          <summary className="focus-ring cursor-pointer text-brand">Visa {data.others.length} övriga kandidater och nedprioriteringsskäl</summary>
          <ul className="mt-2 space-y-1">
            {data.others.map((item) => <FindingRow key={item.group_key} item={item} metrics={metrics} onSelectMetric={onSelectMetric} />)}
          </ul>
        </details>
      )}
    </section>
  );
}

function FindingRow({ item, metrics, onSelectMetric }: { item: AnalysisFinding; metrics: Record<string, MetricComparison>; onSelectMetric: (code: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <li className="rounded bg-white px-3 py-2">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)} className="focus-ring text-left font-medium text-brand hover:underline">{item.label}</button>
        <div className="text-[11px] text-muted">Nyckeltal: {item.metric_codes.join(", ")} · evidens: {item.source_level} · prioritet {item.priority_score}</div>
        {item.warnings.map((warning, i) => <div key={`${i}-${warning}`} className="text-[11px] text-medium">{warning}</div>)}
        {item.demotion_reasons.map((reason, i) => <div key={`${i}-${reason}`} className="text-[11px] text-muted">{reason}</div>)}
      </div>
      <span className="whitespace-nowrap text-[12px] tabular-nums">{amount(item.amount_effect, item.unit, true)}</span>
      </div>
      {expanded && (
        <div className="mt-3 space-y-2 border-t border-line pt-3 text-[12px]">
          <p className="text-muted">Jämförelse: {item.period_pair[0]} mot {item.period_pair[1]}. Kontrollera konton och verifikationer innan du drar en slutsats.</p>
          {item.sources.map((source, sourceIndex) => (
            <div key={`${item.group_key}-${sourceIndex}`} className="rounded border border-line bg-canvas p-2">
              <p>Konton: {source.accounts || "Inga konton angivna"}</p>
              {source.references && source.references.length > 0 ? (
                <ul className="mt-1 space-y-1">
                  {source.references.map((reference, referenceIndex) => (
                    <li key={`${reference.period}-${reference.voucher}-${referenceIndex}`}>
                      {reference.period} · {reference.date ?? "datum saknas"} · {reference.voucher ? <VoucherLink v={reference.voucher} hint={reference.date ?? reference.period} /> : "verifikation saknas"}
                      {reference.source_line != null && <span className="text-muted"> · SIE-rad {reference.source_line}</span>}
                    </li>
                  ))}
                </ul>
              ) : <p className="mt-1 text-muted">Inga verifikationsreferenser finns för denna kandidat.</p>}
            </div>
          ))}
          {item.metric_codes.filter((code) => metrics[code]).map((code) => (
            <button key={code} type="button" onClick={() => onSelectMetric(code)} className="focus-ring mr-3 text-brand hover:underline">
              Visa nyckeltal: {metrics[code].label}
            </button>
          ))}
        </div>
      )}
    </li>
  );
}

function MetricTile({ metric, selected, onSelect }: { metric: MetricComparison; selected: boolean; onSelect: () => void }) {
  // Färgen följer nyckeltalets riktning: för t.ex. personalkostnadsandel är en minskning positiv.
  const n = metric.change === null ? 0 : Number(metric.change);
  const trend = n === 0 || metric.better === "neutral" ? "text-muted" : (metric.better === "lower" ? n < 0 : n > 0) ? "text-ok" : "text-high";
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-expanded={selected}
      className={cx("focus-ring rounded-md border p-3 text-left transition hover:border-brand", selected ? "border-brand bg-brand-soft" : "border-line bg-white")}
    >
      <span className="block text-[12px] text-muted">{metric.label}</span>
      <span className="mt-1 block text-lg font-semibold tabular-nums">{amount(metric.current, metric.unit)}</span>
      <span className={cx("mt-0.5 block text-[12px]", trend)}>
        Förändring: {amount(metric.change, metric.unit, true, true)}
      </span>
      <span className="mt-1 block text-[11px] text-muted">{STATUS[metric.status] ?? metric.status} · Visa underlag</span>
    </button>
  );
}

function MetricDetail({ code, period, mode, summary, base }: { code: string; period: string; mode: Mode; summary: MetricComparison; base: string }) {
  const detail = useLoad<MetricExplanation>(`${base}/metric-explanations/${encodeURIComponent(code)}?period=${encodeURIComponent(period)}&mode=${mode}`);
  const explanation = detail.data;
  return (
    <section aria-label={`Underlag för ${summary.label}`} className="rounded-md border border-line bg-canvas p-3 sm:p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">{summary.label}: {amount(summary.current, summary.unit)} mot {amount(summary.previous, summary.unit)}</h3>
        <span className="text-[12px] text-muted">Förändring {amount(summary.change, summary.unit, true, true)}</span>
      </div>
      <ErrorBox error={detail.error} />
      {!explanation && !detail.error && <Loading />}
      {explanation && (
        <div className="space-y-4">
          {explanation.warnings.map((warning, i) => <p key={`${i}-${warning}`} className="text-[12px] text-medium">{warning}</p>)}
          {explanation.components.map((component) => (
            <div key={component.code} className="rounded border border-line bg-white p-3">
              <div className="flex flex-wrap justify-between gap-2 text-[13px]">
                <strong>{component.label}{component.role === "denominator" && <span className="font-normal text-muted"> (nämnare)</span>}</strong>
                <span className="tabular-nums">Bidrag {amount(component.effect, component.unit, true, true)}</span>
              </div>
              {component.note && <p className="mt-1 text-[12px] text-muted">{component.note}</p>}
              <Evidence evidence={component.evidence} />
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function Evidence({ evidence }: { evidence: MetricEvidence }) {
  const sourceLabel: Record<string, string> = {
    account_voucher: "Konton och verifikationer",
    account: "Konton – verifikationsrader saknas",
    period_balance: "Periodsaldo – verifikationer saknas",
    insufficient_data: "Otillräckligt underlag",
    parameter: "Beräkningsparameter – inga verifikationer",
  };
  return (
    <div className="mt-3 space-y-3">
      <p className="text-[11px] font-medium text-muted">Källnivå: {sourceLabel[evidence.source_level] ?? evidence.source_level}. Kontosummor stäms av mot respektive period.</p>
      {evidence.accounts.length > 0 && (
        <div className="overflow-x-auto">
          <table className="data min-w-[480px]">
            <thead><tr><th>Konto</th><th className="num">Nu</th><th className="num">Jämförelse</th></tr></thead>
            <tbody>{evidence.accounts.map((account) => (
              <tr key={account.account}><td>{account.account} {account.name}</td><td className="num">{sek(account.current)}</td><td className="num">{sek(account.previous)}</td></tr>
            ))}</tbody>
            <tfoot><tr><th>Summa</th><td className="num">{sek(evidence.current_total)}</td><td className="num">{sek(evidence.previous_total)}</td></tr></tfoot>
          </table>
        </div>
      )}
      {evidence.current_rows.length > 0 || evidence.previous_rows.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          <EvidenceRows title="Nuvarande period" rows={evidence.current_rows} other={evidence.other_current} />
          <EvidenceRows title="Jämförelseperiod" rows={evidence.previous_rows} other={evidence.other_previous} />
        </div>
      ) : <p className="text-[12px] text-muted">Inga verifikationsrader finns att visa. Inga rader har skapats från periodsaldon.</p>}
      {evidence.warnings.map((warning, i) => <p key={`${i}-${warning}`} className="text-[12px] text-medium">{warning}</p>)}
    </div>
  );
}

function EvidenceRows({ title, rows, other }: { title: string; rows: MetricEvidenceRow[]; other: string }) {
  return (
    <div>
      <h4 className="mb-1 text-[12px] font-semibold">{title}</h4>
      <div className="overflow-x-auto">
        <table className="data min-w-[520px]">
          <thead><tr><th>Datum / verifikation</th><th>Händelse</th><th className="num">Belopp</th></tr></thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={`${row.period}-${row.voucher ?? "saldo"}-${row.source_line ?? i}`}>
                <td className="whitespace-nowrap">{row.date ?? row.period}{row.voucher && <><br /><VoucherLink v={row.voucher} hint={row.date ?? row.period} /></>}</td>
                <td className="max-w-[280px] truncate" title={row.text}>{row.account} · {row.text || row.source}</td>
                <td className="num whitespace-nowrap">{sek(row.amount, { signed: true })}</td>
              </tr>
            ))}
            <tr><td colSpan={2} className="text-muted">Övriga rader eller beräkning</td><td className="num">{sek(other, { signed: true })}</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
