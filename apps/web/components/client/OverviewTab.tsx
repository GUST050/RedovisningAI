"use client";

import { useMemo, useState } from "react";
import { TrendChart } from "@/components/Chart";
import { Card, ErrorBox, Loading, cx } from "@/components/ui";
import { type Fact, type MetricEntry, type Overview, useLoad } from "@/lib/api";
import { monthLabel, pct, sek } from "@/lib/format";
import { MetricExplanationPanel } from "./MetricExplanation";
import { Amount, Diff, VoucherLink, useClient } from "./shared";

type Trend = { months: string[]; net_sales: string[]; costs: string[]; operating_result: string[] };

type Bridge = {
  target: string;
  target_label: string;
  current_total: string;
  previous_total: string;
  change: string;
  period: string;
  compare_period: string;
  components: { code: string; label: string; current: string; previous: string; effect: string; share_of_worsening: string | null }[];
};

export type Drilldown = {
  accounts: { account: number; name: string; current: string; previous: string; diff: string }[];
  counterparties: { key: string; name: string; current: string; previous: string; diff: string; is_new: boolean }[];
  top_vouchers: { voucher: string; date: string; text: string; account: number; amount: string; counterparty: string | null }[];
};

type Explain = { kind: "bridge"; bridge: Bridge } | { kind: "drilldown"; target: string; period: string; compare: string; drilldown: Drilldown };

const LOWER_IS_BETTER = new Set(["personnel_share"]);

const MATURITY_LABEL: Record<string, string> = { invoice: "Fakturametoden", cash: "Kontantmetoden", unknown: "Okänd" };

export function OverviewTab() {
  const { base, spec, month, view } = useClient();
  const ov = useLoad<Overview>(`${base}/overview?period=${month}`);
  const trend = useLoad<Trend>(`${base}/trend?months=24`);
  const sectionKey = view === "month" ? "month" : view === "YTD" ? "ytd" : "r12";
  const section = ov.data?.sections[sectionKey] ?? ov.data?.sections.month;

  const chart = useMemo(() => {
    const t = trend.data;
    if (!t) return null;
    return {
      categories: t.months.map((m) => monthLabel(m)),
      series: [
        { name: "Nettoomsättning", data: t.net_sales.map(Number), color: "#1f4e79" },
        { name: "Kostnader", data: t.costs.map(Number), color: "#98a2b3" },
        { name: "Rörelseresultat", data: t.operating_result.map(Number), type: "line" as const, color: "#067647" },
      ],
    };
  }, [trend.data]);

  if (ov.error) return <ErrorBox error={ov.error} />;
  if (!ov.data || !section) return <Loading />;
  const m = ov.data.maturity;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {Object.values(section.metrics).map((e) => (
          <MetricCard key={e.id} entry={e} compareLabel={section.compare.label} />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_380px]">
        <Card title="Utveckling per månad">{chart ? <TrendChart categories={chart.categories} series={chart.series} /> : <Loading />}</Card>
        <Card title={`Periodmognad – ${monthLabel(m.period)}`}>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[13px]">
            <dt className="text-muted">Bokföringsmetod</dt>
            <dd>{MATURITY_LABEL[m.accounting_method] ?? m.accounting_method}</dd>
            <dt className="text-muted">Avskrivningar varje månad</dt>
            <dd>{yesNo(m.monthly_depreciation)}</dd>
            <dt className="text-muted">Semesterskuld varje månad</dt>
            <dd>{yesNo(m.monthly_vacation_accrual)}</dd>
            <dt className="text-muted">Löner bokförda</dt>
            <dd>{yesNo(m.payroll_booked)}</dd>
            <dt className="text-muted" title="Månadens antal verifikationer jämfört med medianen för de sex föregående månaderna.">Verifikationer mot normalt</dt>
            <dd>{m.completeness ? `${Math.round(Number(m.completeness) * 100)} %` : "–"}</dd>
          </dl>
          {m.notes.length > 0 && (
            <ul className="mt-3 list-disc space-y-1 pl-4 text-[13px] text-medium">
              {m.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <MetricExplanationPanel key={`metric-analysis-${spec}`} />
      <ExplainCard key={spec} />
    </div>
  );
}

function yesNo(v: boolean | null) {
  return v === null ? "–" : v ? "Ja" : "Nej";
}

function MetricCard({ entry, compareLabel }: { entry: MetricEntry; compareLabel: string }) {
  const f = entry.fact;
  const [open, setOpen] = useState(false);
  const change = entry.change;
  const isPct = f.unit === "percent" || f.unit === "pp" || f.unit === "ratio";
  // För personalkostnadsandelen är en minskning positiv; övriga nyckeltal är bättre ju högre.
  const sign = (change?.value ? Math.sign(Number(change.value)) : 0) * (LOWER_IS_BETTER.has(String(f.lineage.metric ?? "")) ? -1 : 1);
  const tone = sign > 0 ? "text-ok" : sign < 0 ? "text-high" : "text-muted";
  return (
    <div className="rounded-lg border border-line bg-white p-3">
      <div className="flex items-start justify-between gap-2">
        <span className="text-[12px] text-muted">{f.label}</span>
        <button className="focus-ring text-[11px] text-muted hover:text-brand" onClick={() => setOpen((o) => !o)} title="Visa hur talet räknats fram">
          ⓘ
        </button>
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums">{f.display}</div>
      {f.status !== "CALCULATED" && <div className="text-[11px] text-medium">{statusText(f)}</div>}
      {entry.previous && (
        <div className="mt-0.5 text-[12px]">
          <span className={tone}>{change?.display ?? "–"}</span>
          <span className="text-muted"> mot {compareLabel}{isPct ? "" : ` (${entry.previous.display})`}</span>
        </div>
      )}
      {open && (
        <div className="mt-2 border-t border-line pt-2 text-[11px] text-muted">
          <div>{String((f.lineage as { formula?: string }).formula ?? "")}</div>
          <div className="font-mono">{f.id}</div>
        </div>
      )}
    </div>
  );
}

function statusText(f: Fact) {
  return (
    {
      PARTIAL: "Preliminärt – perioden är inte komplett",
      INSUFFICIENT_DATA: "Underlag saknas",
      NOT_APPLICABLE: "Ej tillämpligt",
      ERROR: "Kunde inte beräknas",
    }[f.status] ?? f.status
  );
}

export function ExplainCard() {
  const { base, spec } = useClient();
  const [target, setTarget] = useState("operating_result");
  const data = useLoad<Explain>(`${base}/explain?target=${encodeURIComponent(target)}&period=${encodeURIComponent(spec)}`);
  const d = data.data;
  return (
    <Card
      title="Förklara förändringen"
      actions={
        <div className="flex gap-1 text-[12px]">
          {[
            ["operating_result", "Rörelseresultat"],
            ["net_result", "Resultat efter fin."],
            ["costs", "Kostnader"],
          ].map(([t, l]) => (
            <button
              key={t}
              onClick={() => setTarget(t)}
              className={cx("focus-ring rounded px-2 py-1", target === t ? "bg-brand-soft text-brand" : "text-muted hover:text-ink")}
            >
              {l}
            </button>
          ))}
        </div>
      }
    >
      <ErrorBox error={data.error} />
      {!d && !data.error && <Loading />}
      {d?.kind === "bridge" && (
        <div className="space-y-2">
          <p className="text-[13px]">
            {d.bridge.target_label} {d.bridge.period}: <strong>{sek(d.bridge.current_total)}</strong> mot {sek(d.bridge.previous_total)} ({d.bridge.compare_period}),
            förändring <strong className={Number(d.bridge.change) < 0 ? "text-high" : "text-ok"}>{sek(d.bridge.change, { signed: true })}</strong>.
            Klicka på en rad för att se konton, motparter och verifikationer.
          </p>
          <table className="data">
            <thead>
              <tr>
                <th>Post</th>
                <th className="num">Nu</th>
                <th className="num">Jämförelse</th>
                <th className="num">Effekt på resultatet</th>
                <th className="num">Andel av försämringen</th>
              </tr>
            </thead>
            <tbody>
              {d.bridge.components.map((c) => (
                <tr key={c.code} className="cursor-pointer" onClick={() => setTarget(target === "costs" ? `category:${c.code}` : `line:${c.code}`)}>
                  <td className="text-brand">{c.label}</td>
                  <Amount value={c.current} />
                  <Amount value={c.previous} />
                  <Diff value={c.effect} />
                  <td className="num text-muted">{c.share_of_worsening ? pct(c.share_of_worsening, false) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {d?.kind === "drilldown" && (
        <div className="space-y-3">
          <button className="text-[12px] text-brand hover:underline" onClick={() => setTarget("operating_result")}>
            ← Tillbaka till resultatbryggan
          </button>
          <DrilldownView d={d.drilldown} period={d.period} />
        </div>
      )}
    </Card>
  );
}

export function DrilldownView({ d, period }: { d: Drilldown; period: string }) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div>
        <h3 className="mb-1 text-[13px] font-semibold">Konton</h3>
        <table className="data">
          <thead>
            <tr><th>Konto</th><th className="num">Nu</th><th className="num">Jämförelse</th><th className="num">Förändring</th></tr>
          </thead>
          <tbody>
            {d.accounts.map((a) => (
              <tr key={a.account}>
                <td>{a.account} {a.name}</td>
                <Amount value={a.current} />
                <Amount value={a.previous} />
                <Diff value={a.diff} inverse />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <h3 className="mb-1 text-[13px] font-semibold">Motparter</h3>
        {d.counterparties.length === 0 ? (
          <p className="text-muted">Inga motparter kunde identifieras.</p>
        ) : (
          <table className="data">
            <thead>
              <tr><th>Motpart</th><th className="num">Nu</th><th className="num">Jämförelse</th><th className="num">Förändring</th></tr>
            </thead>
            <tbody>
              {d.counterparties.map((c) => (
                <tr key={c.key}>
                  <td>{c.name}{c.is_new && <span className="ml-1 rounded bg-medium-soft px-1 text-[10px] text-medium">ny</span>}</td>
                  <Amount value={c.current} />
                  <Amount value={c.previous} />
                  <Diff value={c.diff} inverse />
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="lg:col-span-2">
        <h3 className="mb-1 text-[13px] font-semibold">Största verifikationerna</h3>
        <table className="data">
          <thead>
            <tr><th>Ver</th><th>Datum</th><th>Text</th><th>Konto</th><th className="num">Belopp</th></tr>
          </thead>
          <tbody>
            {d.top_vouchers.map((v, i) => (
              <tr key={i}>
                <td><VoucherLink v={v.voucher} hint={v.date || period} /></td>
                <td>{v.date}</td>
                <td>{v.text}</td>
                <td>{v.account}</td>
                <Amount value={v.amount} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
