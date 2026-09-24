"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AppShell, useMe } from "@/components/AppShell";
import { Button, Card, Empty, ErrorBox, Loading, StatusBadge, cx, inputCls } from "@/components/ui";
import { get, send, type Portfolio } from "@/lib/api";
import { monthLabel } from "@/lib/format";

const TODO_LABELS: Record<string, string> = {
  missing_data: "kunder saknar data för förra månaden",
  high_findings: "kunder har allvarliga (High) fynd",
  changed_after_approval: "kunder har ändringar efter godkännande",
  connection_errors: "kopplingar behöver förnyas",
  unanswered_questions: "kundfrågor obesvarade > 7 dagar",
  margin_drops: "kunder har marginalfall > 5 procentenheter",
  needs_review: "perioder väntar på granskning",
};

export default function PortfolioPage() {
  return (
    <AppShell>
      <PortfolioView />
    </AppShell>
  );
}

function PortfolioView() {
  const me = useMe();
  const [data, setData] = useState<Portfolio | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [filter, setFilter] = useState("");
  const [system, setSystem] = useState("");
  const [brief, setBrief] = useState<{ claims: { rendered: string }[]; source: string } | null>(null);
  const [newName, setNewName] = useState("");
  const [newOrg, setNewOrg] = useState("");

  const load = () => get<Portfolio>("/api/portfolio").then(setData).catch(setError);
  useEffect(() => {
    load();
  }, []);

  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const systems = Array.from(new Set(data.companies.map((c) => c.source_system ?? "okänt")));
  const rows = data.companies.filter(
    (c) => (!filter || c.name.toLowerCase().includes(filter.toLowerCase())) && (!system || (c.source_system ?? "okänt") === system),
  );
  const todo = Object.entries(data.todo).filter(([, n]) => n > 0);

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_320px]">
      <Card
        title={`Mina kunder (${data.companies.length})`}
        actions={
          <>
            <input className={cx(inputCls, "w-48")} placeholder="Sök kund" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <select className={cx(inputCls, "w-36")} value={system} onChange={(e) => setSystem(e.target.value)}>
              <option value="">Alla system</option>
              {systems.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </>
        }
      >
        {rows.length === 0 ? (
          <Empty>Inga kunder ännu. Lägg till en kund till höger och ladda upp en SIE-fil.</Empty>
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>Kund</th>
                <th>System</th>
                <th>Period</th>
                <th>Status</th>
                <th className="num">Fynd (H/M/L)</th>
                <th>Varför högt upp?</th>
                <th className="num">Prio</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link href={`/clients/${c.id}`} className="font-medium text-brand hover:underline">{c.name}</Link>
                    <div className="text-[11px] text-muted">{c.org_number}</div>
                  </td>
                  <td className="text-muted">
                    {c.source_system ?? "–"}
                    {!c.connection_ok && <div className="text-[11px] text-high">Kopplingsfel</div>}
                  </td>
                  <td>{monthLabel(c.latest_period)}</td>
                  <td><StatusBadge status={c.changed_after_approval ? "CHANGED_AFTER_APPROVAL" : c.status} /></td>
                  <td className="num">
                    <span className={c.open_findings.high ? "font-semibold text-high" : ""}>{c.open_findings.high}</span>
                    {" / "}{c.open_findings.medium}{" / "}{c.open_findings.low}
                  </td>
                  <td className="text-[12px] text-muted">{c.priority.reasons.slice(0, 3).map((r) => r.text).join(" · ") || "–"}</td>
                  <td className="num font-semibold">{c.priority.score}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <div className="space-y-5">
        <Card title="Att göra">
          {todo.length === 0 ? (
            <p className="text-muted">Inget akut. 🎉</p>
          ) : (
            <ul className="space-y-2">
              {todo.map(([k, n]) => (
                <li key={k} className="flex gap-2"><span className="w-6 text-right font-semibold">{n}</span><span>{TODO_LABELS[k] ?? k}</span></li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Veckans prioriteringar" actions={<Button variant="secondary" onClick={() => get<{ data: { claims: { rendered: string }[] }; source: string }>("/api/portfolio/brief").then((r) => setBrief({ claims: r.data.claims, source: r.source })).catch(setError)}>Skapa</Button>}>
          {brief ? (
            <ul className="list-disc space-y-1 pl-4">{brief.claims.map((c, i) => <li key={i}>{c.rendered}</li>)}</ul>
          ) : (
            <p className="text-muted">Sammanfattning av var du behövs mest den här veckan.</p>
          )}
        </Card>
        {me?.permissions.write && (
          <Card title="Lägg till kund">
            <div className="space-y-2">
              <input className={inputCls} placeholder="Bolagsnamn" value={newName} onChange={(e) => setNewName(e.target.value)} />
              <input className={inputCls} placeholder="Organisationsnummer" value={newOrg} onChange={(e) => setNewOrg(e.target.value)} />
              <Button
                disabled={!newName}
                onClick={() =>
                  send<{ id: string }>("/api/companies", "POST", { name: newName, org_number: newOrg || null })
                    .then((r) => (window.location.href = `/clients/${r.id}?tab=data`))
                    .catch(setError)
                }
              >
                Skapa kund
              </Button>
              <p className="text-[11px] text-muted">Tips: ladda upp många SIE-filer på en gång som zip under en kunds flik Data – filerna kopplas till rätt kund via organisationsnumret.</p>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
