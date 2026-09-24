"use client";

import { Fragment, useState } from "react";
import { Card, ErrorBox, Loading, cx } from "@/components/ui";
import { type Statement, type StatementLine, useLoad } from "@/lib/api";
import { Amount, Diff, useClient } from "./shared";

type Budget = { period: string; has_budget: boolean; lines: { code: string; label: string; actual: string; budget: string; diff: string; diff_pct: string | null }[] };

// Kostnader redovisas med negativt tecken i resultaträkningen, så en positiv förändring är
// alltid bra för resultatet och kan färgas grön utan specialfall.

export function StatementsTab() {
  const { base, spec } = useClient();
  const data = useLoad<{ income: Statement; balance: Statement }>(`${base}/statements?period=${encodeURIComponent(spec)}`);
  const b = useLoad<Budget>(`${base}/budget?period=${encodeURIComponent(spec)}`).data;

  if (data.error) return <ErrorBox error={data.error} />;
  if (!data.data) return <Loading />;
  const { income, balance } = data.data;
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <a className="rounded-md border border-line bg-white px-3 py-1.5 text-[13px] hover:bg-canvas" href={`${base}/export/statements.xlsx?period=${encodeURIComponent(spec)}`}>
          Exportera till Excel
        </a>
      </div>
      <StatementCard title="Resultaträkning" st={income} />
      {b?.has_budget && (
        <Card title={`Budget mot utfall – ${b.period}`}>
          <table className="data">
            <thead>
              <tr><th>Rad</th><th className="num">Utfall</th><th className="num">Budget</th><th className="num">Avvikelse</th></tr>
            </thead>
            <tbody>
              {b.lines.map((l) => (
                <tr key={l.code}>
                  <td>{l.label}</td>
                  <Amount value={l.actual} />
                  <Amount value={l.budget} />
                  <Diff value={l.diff} pctValue={l.diff_pct} />
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <StatementCard title="Balansräkning" st={balance} />
    </div>
  );
}

function StatementCard({ title, st }: { title: string; st: Statement & { missing_months?: string[] } }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (code: string) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(code)) n.delete(code);
      else n.add(code);
      return n;
    });
  return (
    <Card title={title} actions={<span className="text-[12px] text-muted">Klicka på en rad för att se kontona</span>}>
      {!st.complete && (
        <p className="mb-2 text-[12px] text-medium">Perioden saknar data för vissa månader{st.missing_months?.length ? `: ${st.missing_months.join(", ")}` : ""}.</p>
      )}
      <table className="data">
        <thead>
          <tr>
            <th></th>
            <th className="num">{st.period}</th>
            <th className="num">{st.compare ?? ""}</th>
            <th className="num">Förändring</th>
          </tr>
        </thead>
        <tbody>
          {st.lines.map((ln) => (
            <Fragment key={ln.code}>
              <LineRow ln={ln} open={open.has(ln.code)} onToggle={() => toggle(ln.code)} kind={st.kind} />
              {open.has(ln.code) &&
                ln.accounts.map((a) => (
                  <tr key={`${ln.code}-${a.account}`} className="text-[12px] text-muted">
                    <td className="pl-8">{a.account} {a.name}</td>
                    <Amount value={a.amount} />
                    <Amount value={a.compare} />
                    <td></td>
                  </tr>
                ))}
            </Fragment>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function LineRow({ ln, open, onToggle, kind }: { ln: StatementLine; open: boolean; onToggle: () => void; kind: string }) {
  const total = ln.level === 0 || ln.accounts.length === 0;
  return (
    <tr className={cx(total && "font-semibold", ln.accounts.length > 0 && "cursor-pointer")} onClick={ln.accounts.length ? onToggle : undefined}>
      <td>
        {ln.accounts.length > 0 && <span className="mr-1 inline-block w-3 text-muted">{open ? "▾" : "▸"}</span>}
        {ln.label}
      </td>
      <Amount value={ln.amount} />
      <Amount value={ln.compare} />
      {kind === "income" ? <Diff value={ln.diff} pctValue={ln.diff_pct} /> : <Diff value={ln.diff} />}
    </tr>
  );
}
