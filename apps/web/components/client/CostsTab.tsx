"use client";

import { Fragment, useState } from "react";
import { Card, ErrorBox, Loading, cx } from "@/components/ui";
import { useLoad } from "@/lib/api";
import { monthLabel, pct, periodLabel, sek } from "@/lib/format";
import { Amount, Diff, useClient } from "./shared";
import { type Drilldown, DrilldownView } from "./OverviewTab";

type Node = {
  code: string;
  name: string;
  amount: string;
  compare: string | null;
  diff: string | null;
  diff_pct: string | null;
  share: string | null;
  accounts: { account: number; name: string; amount: string; compare: string | null }[];
  children: Node[];
};

type Counterparty = {
  key: string;
  name: string;
  amount: string;
  compare: string;
  diff: string;
  share: string | null;
  recurrence_sv: string;
  annualized: string | null;
  accounts: number[];
  confidence: number;
  first_month?: string;
};

type Spend = {
  period: string;
  compare_period: string | null;
  total: string;
  compare_total: string | null;
  counterparties: Counterparty[];
  new_costs: Counterparty[];
  disappeared: Counterparty[];
  level_shifts: { key: string; name: string; month: string; before: string; after: string; change_pct: string }[];
  concentration: { top1: string; top5: string; top10: string };
  recurring_share: string | null;
};

export function CostsTab() {
  const { base, spec } = useClient();
  const tree = useLoad<Node>(`${base}/cost-tree?period=${encodeURIComponent(spec)}`);
  const spendSpec = spec.includes(":") ? spec : `YTD:${spec}`;
  const spend = useLoad<Spend>(`${base}/spend?period=${encodeURIComponent(spendSpec)}`);
  const [category, setCategory] = useState<string | null>(null);
  const drill = useLoad<{ drilldown: Drilldown; period: string }>(
    category ? `${base}/explain?target=${encodeURIComponent(`category:${category}`)}&period=${encodeURIComponent(spec)}` : null,
  );

  return (
    <div className="space-y-4">
      <Card title="Kostnader per kategori" actions={<span className="text-[12px] text-muted">Klicka på en kategori för motparter och verifikationer</span>}>
        <ErrorBox error={tree.error} />
        {!tree.data ? (
          <Loading />
        ) : (
          <table className="data">
            <thead>
              <tr><th>Kategori</th><th className="num">Belopp</th><th className="num">Jämförelse</th><th className="num">Förändring</th><th className="num">Andel</th></tr>
            </thead>
            <tbody>
              {tree.data.children.map((n) => (
                <Fragment key={n.code}>
                  <tr className={cx("cursor-pointer", category === n.code && "bg-brand-soft")} onClick={() => setCategory(category === n.code ? null : n.code)}>
                    <td className="text-brand">{n.name}</td>
                    <Amount value={n.amount} />
                    <Amount value={n.compare} />
                    <Diff value={n.diff} pctValue={n.diff_pct} inverse />
                    <td className="num text-muted">{n.share ? pct(n.share, false) : ""}</td>
                  </tr>
                </Fragment>
              ))}
              <tr className="font-semibold">
                <td>{tree.data.name}</td>
                <Amount value={tree.data.amount} />
                <Amount value={tree.data.compare} />
                <Diff value={tree.data.diff} pctValue={tree.data.diff_pct} inverse />
                <td></td>
              </tr>
            </tbody>
          </table>
        )}
        {category && (
          <div className="mt-4 border-t border-line pt-3">
            <ErrorBox error={drill.error} />
            {drill.data ? <DrilldownView d={drill.data.drilldown} period={drill.data.period} /> : !drill.error && <Loading />}
          </div>
        )}
      </Card>
      <SpendCard spend={spend.data} error={spend.error} />
    </div>
  );
}

function SpendCard({ spend, error }: { spend: Spend | null; error: unknown }) {
  if (error) return <ErrorBox error={error} />;
  if (!spend) return <Loading />;
  return (
    <Card title={`Leverantörer och motparter – ${periodLabel(spend.period)}`}>
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Kostnader via leverantörer" value={sek(spend.total)} sub={spend.compare_total ? `jämfört med ${sek(spend.compare_total)}` : ""} />
        <Kpi label="Största leverantören" value={pct(spend.concentration.top1, false)} sub="av kostnaderna" />
        <Kpi label="Fem största" value={pct(spend.concentration.top5, false)} sub="av kostnaderna" />
        <Kpi label="Återkommande kostnader" value={pct(spend.recurring_share, false)} sub="abonnemang, hyror m.m." />
      </div>
      {(spend.new_costs.length > 0 || spend.level_shifts.length > 0 || spend.disappeared.length > 0) && (
        <div className="mb-4 grid grid-cols-1 gap-3 md:grid-cols-3">
          <Signal title="Nya kostnader" items={spend.new_costs.map((c) => `${c.name}: ${sek(c.amount)}${c.first_month ? ` (från ${monthLabel(c.first_month)})` : ""}`)} />
          <Signal title="Prisförändringar" items={spend.level_shifts.map((l) => `${l.name}: ${sek(l.before)} → ${sek(l.after)}/mån (${pct(l.change_pct)}) från ${monthLabel(l.month)}`)} />
          <Signal title="Upphörda kostnader" items={spend.disappeared.map((c) => `${c.name}: ${sek(c.compare)} i jämförelseperioden`)} />
        </div>
      )}
      <table className="data">
        <thead>
          <tr>
            <th>Motpart</th>
            <th>Mönster</th>
            <th className="num">Belopp</th>
            <th className="num">Jämförelse</th>
            <th className="num">Förändring</th>
            <th className="num">Andel</th>
            <th>Konton</th>
          </tr>
        </thead>
        <tbody>
          {spend.counterparties.map((c) => (
            <tr key={c.key}>
              <td>
                {c.name}
                {c.confidence < 0.7 && <span className="ml-1 text-[11px] text-muted" title="Motparten har identifierats ur verifikationstexten med låg säkerhet">(osäker)</span>}
              </td>
              <td className="text-muted">
                {c.recurrence_sv}
                {c.annualized && <div className="text-[11px]">≈ {sek(c.annualized)}/år</div>}
              </td>
              <Amount value={c.amount} />
              <Amount value={c.compare} />
              <Diff value={c.diff} inverse />
              <td className="num text-muted">{c.share ? pct(c.share, false) : ""}</td>
              <td className="text-[12px] text-muted">{c.accounts.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-md border border-line p-3">
      <div className="text-[12px] text-muted">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      {sub && <div className="text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

function Signal({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-md border border-line p-3">
      <div className="mb-1 text-[12px] font-semibold">{title}</div>
      {items.length === 0 ? (
        <p className="text-[12px] text-muted">Inga.</p>
      ) : (
        <ul className="space-y-0.5 text-[12px]">
          {items.slice(0, 6).map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
