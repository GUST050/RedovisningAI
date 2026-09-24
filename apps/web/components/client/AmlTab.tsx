"use client";

import { useState } from "react";
import { Button, Card, Empty, ErrorBox, Field, Loading, SeverityBadge, StatusBadge, cx, inputCls } from "@/components/ui";
import { type Finding, send, useLoad } from "@/lib/api";
import { dateTime, kr } from "@/lib/format";

type AmlView = {
  signals: Finding[];
  assessments: { risk_level: string; factors: Record<string, unknown>; notes: string | null; decided_by: string; decided_at: string }[];
  notice: string;
};

const RISK_SV: Record<string, string> = { LOW: "Låg", NORMAL: "Normal", HIGH: "Hög" };

export function AmlTab({ base }: { base: string }) {
  const data = useLoad<AmlView>(`${base}/aml`);
  const [risk, setRisk] = useState("NORMAL");
  const [notes, setNotes] = useState("");
  const [err, setErr] = useState<unknown>(null);
  if (data.error) return <ErrorBox error={data.error} />;
  if (!data.data) return <Loading />;
  const d = data.data;
  return (
    <div className="space-y-4">
      <div className="rounded-md border border-ai/30 bg-ai-soft px-3 py-2 text-[13px] text-ai">{d.notice}</div>
      <Card title="Signaler" actions={<a className="rounded-md border border-line bg-white px-3 py-1.5 text-[13px] hover:bg-canvas" href={`${base}/aml/export.csv`}>Exportera till KYC-verktyg (CSV)</a>}>
        {d.signals.length === 0 ? (
          <Empty>Inga signaler för kunden.</Empty>
        ) : (
          <table className="data">
            <thead>
              <tr><th>Allvar</th><th>Signal</th><th>Period</th><th>Verifikationer</th><th className="num">Belopp</th><th>Status</th></tr>
            </thead>
            <tbody>
              {d.signals.map((s) => (
                <tr key={s.id}>
                  <td><SeverityBadge severity={s.severity} /></td>
                  <td>
                    <div className="font-medium">{s.title}</div>
                    <div className="text-[12px] text-muted">{s.legal_basis}</div>
                  </td>
                  <td>{s.period}</td>
                  <td className="font-mono text-[12px]">{s.vouchers.join(", ")}</td>
                  <td className="num">{s.amount ? kr(s.amount) : ""}</td>
                  <td><StatusBadge status={s.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="mt-2 text-[12px] text-muted">Signalerna är inte en bedömning av misstanke. Beslut om rapportering till Finanspolisen fattas av byråns PTL-ansvarige.</p>
      </Card>
      <Card title="Riskbedömning av kunden">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Field label="Risknivå">
              <select className={inputCls} value={risk} onChange={(e) => setRisk(e.target.value)}>
                {Object.entries(RISK_SV).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
            <Field label="Motivering">
              <textarea className={cx(inputCls, "h-24")} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </Field>
            <ErrorBox error={err} />
            <Button
              disabled={notes.trim().length < 3}
              onClick={() =>
                send(`${base}/aml/assessment`, "POST", { risk_level: risk, notes, factors: { signals: d.signals.length } })
                  .then(() => {
                    setNotes("");
                    data.reload();
                  })
                  .catch(setErr)
              }
            >
              Spara bedömning
            </Button>
          </div>
          <div>
            <h3 className="mb-1 text-[13px] font-semibold">Tidigare bedömningar</h3>
            {d.assessments.length === 0 ? (
              <p className="text-muted">Ingen bedömning registrerad.</p>
            ) : (
              <ul className="space-y-2 text-[13px]">
                {d.assessments.map((a, i) => (
                  <li key={i} className="rounded-md border border-line p-2">
                    <div className="font-medium">Risk: {RISK_SV[a.risk_level] ?? a.risk_level}</div>
                    <div className="text-[12px] text-muted">{dateTime(a.decided_at)} · {a.decided_by}</div>
                    {a.notes && <div className="mt-1">{a.notes}</div>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
