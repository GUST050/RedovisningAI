"use client";

import { useEffect, useState } from "react";
import { AiBadge, Button, Card, Claims, ErrorBox, Field, Loading, StatusBadge, cx, inputCls } from "@/components/ui";
import { ApiError, type Claim, send, useLoad } from "@/lib/api";
import { dateTime, monthLabel } from "@/lib/format";
import { useClient } from "./shared";

type AiDoc<T> = { task: string; data: T; source: string; ai_note?: string | null; created_at?: string; by?: string; approved?: boolean; edited_by?: string; stale?: boolean; compare_period?: string };
type Meeting = { summary: Claim[]; questions: Claim[]; case_questions?: { case_key: string; question: string }[] };
type DecisionDraft = { decision: "approve" | "reject" | "correct" | ""; reason: string; corrected_text: string };

type PeriodDetail = {
  period: string;
  status: string;
  approved_by: string | null;
  approved_at: string | null;
  reported_at: string | null;
  override_note: string | null;
  changes: { voucher: string; kind: string; date: string; text: string }[];
  commentary: AiDoc<{ claims: Claim[] }> | null;
  client_report: AiDoc<Meeting> | null;
};

const CHANGE_SV: Record<string, string> = { added: "ny", changed: "ändrad", removed: "borttagen" };

export function ReportsTab() {
  const { base, month, me, months } = useClient();
  const detail = useLoad<PeriodDetail>(`${base}/periods/${month}`);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [comparePeriod, setComparePeriod] = useState("");
  const compareQuery = comparePeriod ? `?compare=${encodeURIComponent(comparePeriod)}` : "";

  const run = (key: string, p: Promise<unknown>) => {
    setBusy(key);
    setErr(null);
    p.then(detail.reload)
      .catch(setErr)
      .finally(() => setBusy(null));
  };

  if (detail.error instanceof ApiError && detail.error.status === 404) {
    return (
      <Card title={`Rapporter – ${monthLabel(month)}`}>
        <p className="text-muted">Perioden har inte granskats än. Kör granskningen under fliken Ärenden.</p>
      </Card>
    );
  }
  if (detail.error) return <ErrorBox error={detail.error} />;
  if (!detail.data) return <Loading />;
  const d = detail.data;
  const write = me.permissions.write;

  return (
    <div className="space-y-4">
      <ErrorBox error={err} />
      <ApprovalCard d={d} onDone={detail.reload} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12px] text-muted">Välj jämförelse för nya utkast och rapporter. Standard är föregående år; ett valt datum jämför den granskade månaden.</p>
        <label className="flex items-center gap-2 text-[12px] text-muted">
          Jämför med
          <select aria-label="Jämförelseperiod för rapport" className="focus-ring rounded border border-line bg-white px-2 py-1 text-ink" value={comparePeriod} onChange={(event) => setComparePeriod(event.target.value)}>
            <option value="">Standard – samma period föregående år</option>
            {months.filter((candidate) => candidate !== month).map((candidate) => <option key={candidate} value={candidate}>{monthLabel(candidate)}</option>)}
          </select>
        </label>
      </div>

      <Card
        title="Periodkommentar (intern)"
        actions={
          write && (
            <Button variant="secondary" disabled={busy === "commentary"} onClick={() => run("commentary", send(`${base}/periods/${month}/commentary${compareQuery}`, "POST"))}>
              {busy === "commentary" ? "Skriver…" : d.commentary ? "Skapa ny" : "Skapa kommentar"}
            </Button>
          )
        }
      >
        {d.commentary ? (
          <div className="space-y-2">
            {d.commentary.stale && <p className="rounded bg-medium-soft px-3 py-2 text-[12px] text-medium">Kommentaren bygger på ändrad bokföring, jämförelse eller promptversion och är inaktuell. Skapa en ny innan den används.</p>}
            <div className="flex items-center gap-2 text-[12px] text-muted">
              <AiBadge source={d.commentary.source === "ai" ? "ai" : "rules"} note={d.commentary.ai_note} />
              {d.commentary.created_at && <span>{dateTime(d.commentary.created_at)} · {d.commentary.by}{d.commentary.compare_period ? ` · jämför med ${monthLabel(d.commentary.compare_period)}` : ""}</span>}
            </div>
            <Claims claims={d.commentary.data.claims} />
          </div>
        ) : (
          <p className="text-muted">En kort analys av perioden med hypoteser och frågor. Alla siffror hämtas från beräkningsmotorn.</p>
        )}
      </Card>

      <MeetingCard d={d} busy={busy === "meeting"} onGenerate={() => run("meeting", send(`${base}/periods/${month}/meeting${compareQuery}`, "POST"))} onSaved={detail.reload} />

      <Card title="Ladda ner">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <Download
            title="Kundrapport"
            text="Nyckeltal, resultaträkning och godkänt mötesunderlag. Innehåller aldrig interna fynd."
            links={[
              ["PDF", `${base}/reports/client?period=${month}${comparePeriod ? `&compare=${encodeURIComponent(comparePeriod)}` : ""}&format=pdf`],
              ["Word", `${base}/reports/client?period=${month}${comparePeriod ? `&compare=${encodeURIComponent(comparePeriod)}` : ""}&format=docx`],
            ]}
            warning={d.client_report?.stale ? "Mötesunderlaget är inaktuellt; skapa, granska och godkänn ett nytt innan export." : d.client_report && !d.client_report.approved ? "Mötesunderlaget är inte godkänt och tas därför inte med i kundrapporten." : undefined}
          />
          <Download
            title="Intern granskningsrapport"
            text="Periodkommentar, periodmognad, ärenden och fynd."
            links={[
              ["PDF", `${base}/reports/internal?period=${month}&format=pdf`],
              ["Word", `${base}/reports/internal?period=${month}&format=docx`],
            ]}
          />
          <Download
            title="Granskningsdokumentation (Reko)"
            text="Utförda kontroller, bedömningar, godkännande och händelselogg för perioden."
            links={[["PDF", `${base}/reports/reko?period=${month}`]]}
          />
        </div>
      </Card>
    </div>
  );
}

function ApprovalCard({ d, onDone }: { d: PeriodDetail; onDone: () => void }) {
  const { base, month, me } = useClient();
  const [needsOverride, setNeedsOverride] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const approve = (override?: string) =>
    send(`${base}/periods/${month}/approve`, "POST", { override_note: override ?? null })
      .then(() => {
        setNeedsOverride(null);
        onDone();
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 409 && !override) setNeedsOverride(e.message);
        else setErr(e);
      });
  return (
    <Card title={`Period ${monthLabel(month)}`} actions={<StatusBadge status={d.status} />}>
      <div className="space-y-2 text-[13px]">
        {d.approved_at ? (
          <p>
            Godkänd {dateTime(d.approved_at)} av {d.approved_by}.
            {d.override_note && <span className="text-muted"> Motivering: {d.override_note}</span>}
            {d.reported_at && <span> Rapporterad {dateTime(d.reported_at)}.</span>}
          </p>
        ) : (
          <p className="text-muted">Perioden är inte godkänd. När du godkänner låses en ögonblicksbild av data, regelversioner och beräkningsversion.</p>
        )}
        {d.changes.length > 0 && (
          <div className="rounded-md border border-high/30 bg-high-soft p-3">
            <div className="font-semibold text-high">Bokföringen har ändrats efter godkännandet</div>
            <ul className="mt-1 space-y-0.5 text-[12px]">
              {d.changes.slice(0, 20).map((c, i) => (
                <li key={i}>
                  {c.voucher} ({CHANGE_SV[c.kind] ?? c.kind}) {c.date} {c.text}
                </li>
              ))}
            </ul>
          </div>
        )}
        <ErrorBox error={err} />
        {me.permissions.write && (!d.approved_at || d.status === "CHANGED_AFTER_APPROVAL") && (
          needsOverride ? (
            <div className="space-y-2 rounded-md border border-medium/30 bg-medium-soft p-3">
              <p className="text-medium">{needsOverride}</p>
              <Field label="Motivering för att godkänna ändå (sparas i granskningsdokumentationen)">
                <textarea className={cx(inputCls, "h-20")} value={note} onChange={(e) => setNote(e.target.value)} />
              </Field>
              <div className="flex gap-2">
                <Button disabled={note.trim().length < 3} onClick={() => approve(note)}>Godkänn med motivering</Button>
                <Button variant="secondary" onClick={() => setNeedsOverride(null)}>Avbryt</Button>
              </div>
            </div>
          ) : (
            <Button onClick={() => approve()}>{d.approved_at ? "Godkänn igen" : "Godkänn perioden"}</Button>
          )
        )}
      </div>
    </Card>
  );
}

function MeetingCard({ d, busy, onGenerate, onSaved }: { d: PeriodDetail; busy: boolean; onGenerate: () => void; onSaved: () => void }) {
  const { base, month, me } = useClient();
  const report = d.client_report;
  const [summary, setSummary] = useState("");
  const [questions, setQuestions] = useState("");
  const [decisions, setDecisions] = useState<Record<number, DecisionDraft>>({});
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    if (!report) return;
    setSummary(report.data.summary.map((c) => c.rendered ?? c.text).join("\n"));
    setDecisions({});
    setQuestions(
      [...report.data.questions.map((c) => c.rendered ?? c.text), ...(report.data.case_questions ?? []).map((q) => q.question)].join("\n"),
    );
  }, [report]);
  const canApprove = me.permissions.approve_reports || me.role === "ADMIN";
  const summaryLines = summary.split("\n").map((line) => line.trim()).filter(Boolean);
  const canApproveClaims = summaryLines.every((line, index) => {
    const decision = decisions[index];
    return !!decision?.decision && decision.reason.trim().length >= 3 &&
      (decision.decision !== "correct" || decision.corrected_text.trim().length >= 3) && line.length >= 3;
  });
  const save = (approve: boolean) =>
    send(`${base}/periods/${month}/meeting`, "PUT", {
      summary: summaryLines,
      questions: questions.split("\n").map((s) => s.trim()).filter(Boolean),
      ...(approve ? {
        decisions: summaryLines.map((statement, index) => ({
          index,
          statement,
          decision: decisions[index].decision,
          reason: decisions[index].reason,
          corrected_text: decisions[index].decision === "correct" ? decisions[index].corrected_text : undefined,
        })),
      } : {}),
      approve,
    })
      .then(onSaved)
      .catch(setErr);

  return (
    <Card
      title="Kundmötesunderlag"
      actions={
        me.permissions.write && (
          <Button variant="secondary" disabled={busy} onClick={onGenerate}>
            {busy ? "Skriver…" : report ? "Skapa nytt utkast" : "Skapa utkast"}
          </Button>
        )
      }
    >
      {!report ? (
        <p className="text-muted">Ett utkast i klarspråk till kunden: vad som hänt i perioden och frågor att ta upp. Bygger bara på uppgifter som får visas för kunden.</p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
            <AiBadge source={report.source === "ai" ? "ai" : "rules"} note={report.ai_note} />
            {report.edited_by && <span>Redigerad av {report.edited_by}</span>}
            {report.approved ? <StatusBadge status="APPROVED" /> : <span className="text-medium">Utkast – inte godkänt</span>}
          </div>
          {report.stale && <p className="rounded bg-medium-soft px-3 py-2 text-[12px] text-medium">Mötesunderlaget är inaktuellt och får inte godkännas eller exporteras. Skapa ett nytt utkast från aktuell data.</p>}
          <ErrorBox error={err} />
          <Field label="Sammanfattning (en punkt per rad)">
            <textarea className={cx(inputCls, "h-32")} value={summary} onChange={(e) => { setSummary(e.target.value); setDecisions({}); }} disabled={!me.permissions.write} />
          </Field>
          {me.permissions.write && (
            <div className="space-y-2 rounded-md border border-line p-3">
              <h3 className="text-[13px] font-semibold">Konsultens beslut per slutsats</h3>
              <p className="text-[11px] text-muted">Välj godkänn, avvisa eller korrigera för varje rad. Motivering krävs och sparas i granskningsspåret.</p>
              {summaryLines.map((line, index) => {
                const draft = decisions[index] ?? { decision: "" as const, reason: "", corrected_text: "" };
                return (
                  <div key={`${index}-${line}`} className="space-y-2 rounded bg-canvas p-2">
                    <p className="text-[12px]">{index + 1}. {line}</p>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <Field label="Beslut">
                        <select className={cx(inputCls, "h-9")} value={draft.decision} onChange={(e) => setDecisions((old) => ({ ...old, [index]: { ...draft, decision: e.target.value as DecisionDraft["decision"] } }))}>
                          <option value="">Välj beslut</option>
                          <option value="approve">Godkänn</option>
                          <option value="reject">Avvisa</option>
                          <option value="correct">Korrigera</option>
                        </select>
                      </Field>
                      <Field label="Motivering">
                        <input className={inputCls} value={draft.reason} onChange={(e) => setDecisions((old) => ({ ...old, [index]: { ...draft, reason: e.target.value } }))} />
                      </Field>
                    </div>
                    {draft.decision === "correct" && <Field label="Korrigerad slutsats"><input className={inputCls} value={draft.corrected_text} onChange={(e) => setDecisions((old) => ({ ...old, [index]: { ...draft, corrected_text: e.target.value } }))} /></Field>}
                  </div>
                );
              })}
            </div>
          )}
          <Field label="Att diskutera på mötet (en fråga per rad)">
            <textarea className={cx(inputCls, "h-28")} value={questions} onChange={(e) => setQuestions(e.target.value)} disabled={!me.permissions.write} />
          </Field>
          {me.permissions.write && (
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => save(false)}>Spara</Button>
              <Button disabled={!canApprove || report.stale || !canApproveClaims} title={!canApprove ? "Kräver behörighet att godkänna kundrapporter" : report.stale ? "Skapa ett aktuellt utkast före godkännande" : !canApproveClaims ? "Fatta beslut med motivering för varje slutsats" : undefined} onClick={() => save(true)}>
                Spara och godkänn för kund
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function Download({ title, text, links, warning }: { title: string; text: string; links: [string, string][]; warning?: string }) {
  return (
    <div className="rounded-md border border-line p-3">
      <div className="font-medium">{title}</div>
      <p className="mb-2 text-[12px] text-muted">{text}</p>
      {warning && <p className="mb-2 text-[12px] text-medium">{warning}</p>}
      <div className="flex gap-2">
        {links.map(([label, href]) => (
          <a key={label} href={href} className="rounded-md border border-line bg-white px-3 py-1 text-[13px] hover:bg-canvas">
            {label}
          </a>
        ))}
      </div>
    </div>
  );
}
