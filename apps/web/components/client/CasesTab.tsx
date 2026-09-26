"use client";

import { useState } from "react";
import { AiBadge, Button, Card, Claims, Empty, ErrorBox, Field, Loading, Modal, SeverityBadge, StatusBadge, cx, inputCls } from "@/components/ui";
import { type Case, type Finding, send, useLoad } from "@/lib/api";
import { STATUS_SV, kr, monthLabel } from "@/lib/format";
import { VoucherLink, useClient } from "./shared";

export function CasesTab({ onChanged }: { onChanged: () => void }) {
  const { base, month, me } = useClient();
  const [onlyPeriod, setOnlyPeriod] = useState(false);
  const [showClosed, setShowClosed] = useState(false);
  const [severity, setSeverity] = useState("");
  const q = new URLSearchParams();
  if (onlyPeriod) q.set("period", month);
  if (showClosed) q.set("include_closed", "true");
  const cases = useLoad<Case[]>(`${base}/cases?${q.toString()}`);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const reload = () => {
    cases.reload();
    onChanged();
  };
  const rows = (cases.data ?? []).filter((c) => !severity || c.severity === severity);
  const counts = { HIGH: 0, MEDIUM: 0, LOW: 0 } as Record<string, number>;
  (cases.data ?? []).forEach((c) => c.status !== "CLOSED" && (counts[c.severity] += 1));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-[13px]">
        {(["", "HIGH", "MEDIUM", "LOW"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setSeverity(s)}
            className={cx("focus-ring rounded-full border px-3 py-1", severity === s ? "border-brand bg-brand-soft text-brand" : "border-line bg-white text-muted")}
          >
            {s === "" ? "Alla" : `${{ HIGH: "Hög", MEDIUM: "Medel", LOW: "Låg" }[s]} (${counts[s]})`}
          </button>
        ))}
        <label className="flex items-center gap-1.5 text-muted">
          <input type="checkbox" checked={onlyPeriod} onChange={(e) => setOnlyPeriod(e.target.checked)} /> Bara {monthLabel(month)}
        </label>
        <label className="flex items-center gap-1.5 text-muted">
          <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} /> Visa stängda
        </label>
        <div className="ml-auto flex gap-2">
          <a className="rounded-md border border-line bg-white px-3 py-1.5 text-[13px] hover:bg-canvas" href={`${base}/export/findings.xlsx?period=${month}`}>Exportera fynd (Excel)</a>
          {me.permissions.write && (
            <Button
              variant="secondary"
              disabled={running}
              onClick={() => {
                setRunning(true);
                send(`${base}/review/run`, "POST", { periods: [month] })
                  .then(reload)
                  .catch(setErr)
                  .finally(() => setRunning(false));
              }}
            >
              {running ? "Granskar…" : "Kör granskning igen"}
            </Button>
          )}
        </div>
      </div>
      <ErrorBox error={err ?? cases.error} />
      {!cases.data ? (
        <Loading />
      ) : rows.length === 0 ? (
        <Empty>Inga ärenden att visa. Kontrollerna har inte hittat något som behöver åtgärdas.</Empty>
      ) : (
        <div className="space-y-2">
          {rows.map((c) => (
            <CaseRow key={c.key} c={c} onChanged={reload} />
          ))}
        </div>
      )}
    </div>
  );
}

function CaseRow({ c, onChanged }: { c: Case; onChanged: () => void }) {
  const { base, me } = useClient();
  const [open, setOpen] = useState(false);
  const [decision, setDecision] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const aml = c.visibility === "RESTRICTED_AML";
  return (
    <div className={cx("rounded-lg border bg-white", aml ? "border-ai/40" : "border-line")}>
      <button className="focus-ring flex w-full items-start gap-3 px-4 py-3 text-left" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <SeverityBadge severity={c.severity} />
        <div className="min-w-0 flex-1">
          <div className="font-medium">{c.ai?.title ?? c.title}</div>
          <div className="truncate text-[12px] text-muted">
            {c.findings.length} {c.findings.length === 1 ? "fynd" : "fynd"} · {monthLabel(c.period)}
            {c.memory_hint && " · 💡 liknande har bedömts tidigare"}
            {aml && " · PTL – visas bara för PTL-ansvarig"}
          </div>
        </div>
        <StatusBadge status={c.status} />
        <span className="text-muted">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-line px-4 py-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div>
              <h4 className="text-[12px] font-semibold text-muted">Trolig orsak</h4>
              <p>{c.root_cause}</p>
            </div>
            <div>
              <h4 className="text-[12px] font-semibold text-muted">Föreslagen åtgärd</h4>
              <p>{c.suggested_action}</p>
            </div>
          </div>
          {c.ai && (c.ai.suggestion || c.ai.root_cause?.length) && (
            <div className="rounded-md bg-ai-soft/60 p-3">
              <div className="mb-1 flex items-center gap-2 text-[12px] font-semibold text-ai">
                AI-förslag <AiBadge source={c.ai.source} />
              </div>
              {c.ai.suggestion && <p className="mb-1">{c.ai.suggestion}</p>}
              {c.ai.root_cause && <Claims claims={c.ai.root_cause} />}
            </div>
          )}
          {c.memory_hint && (
            <div className="rounded-md border border-ok/30 bg-ok-soft px-3 py-2 text-[13px] text-ok">💡 Kundminne: {c.memory_hint}</div>
          )}
          <table className="data">
            <thead>
              <tr>
                <th>Fynd</th>
                <th>Period</th>
                <th>Verifikationer</th>
                <th className="num">Belopp</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {c.findings.map((f) => (
                <FindingRow key={f.id} f={f} />
              ))}
            </tbody>
          </table>
          {me.permissions.write && c.status !== "CLOSED" && (
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => setDecision("RESOLVED")}>Åtgärdat</Button>
              <Button variant="secondary" onClick={() => setDecision("ACCEPTED_OK")}>Bedöm OK</Button>
              <Button variant="secondary" onClick={() => setDecision("IN_PROGRESS")}>Under utredning</Button>
              {!aml && (
                <Button variant="ghost" onClick={() => setAsking(true)}>
                  Fråga kunden{c.ask_client_suggested ? " (föreslås)" : ""}
                </Button>
              )}
            </div>
          )}
        </div>
      )}
      {decision && <DecisionModal c={c} status={decision} onClose={() => setDecision(null)} onDone={onChanged} base={base} />}
      {asking && <AskClientModal c={c} onClose={() => setAsking(false)} onDone={onChanged} base={base} />}
    </div>
  );
}

function FindingRow({ f }: { f: Finding }) {
  const [more, setMore] = useState(false);
  return (
    <tr>
      <td>
        <button className="text-left" onClick={() => setMore((m) => !m)}>
          <div className="font-medium">{f.title}</div>
          <div className="text-[12px] text-muted">{f.description_rendered}</div>
        </button>
        {more && (
          <div className="mt-1 space-y-0.5 text-[11px] text-muted">
            <div>Regel: <span className="font-mono">{f.rule_code}</span></div>
            {f.legal_basis && <div>Stöd: {f.legal_basis}</div>}
            {f.accounts.length > 0 && <div>Konton: {f.accounts.join(", ")}</div>}
            {f.resolution_note && <div>Motivering: {f.resolution_note} ({f.resolved_by})</div>}
          </div>
        )}
        {f.memory_suggestion && (
          <div className="mt-1 text-[11px] text-ok">💡 Tidigare: {f.memory_suggestion.text}</div>
        )}
      </td>
      <td>{f.period}</td>
      <td className="space-x-1">
        {f.vouchers.slice(0, 6).map((v) => (
          <VoucherLink key={v} v={v} hint={f.period} />
        ))}
        {f.vouchers.length > 6 && <span className="text-[11px] text-muted">+{f.vouchers.length - 6}</span>}
      </td>
      <td className="num">{f.amount ? kr(f.amount) : ""}</td>
      <td><StatusBadge status={f.status} /></td>
    </tr>
  );
}

function DecisionModal({ c, status, base, onClose, onDone }: { c: Case; status: string; base: string; onClose: () => void; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [remember, setRemember] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const needsNote = status === "ACCEPTED_OK";
  return (
    <Modal
      title={`${STATUS_SV[status]}: ${c.title}`}
      open
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Avbryt</Button>
          <Button
            disabled={needsNote && note.trim().length < 3}
            onClick={() =>
              send(`${base}/cases/${c.key}/decision`, "POST", { status, note: note || null, remember_until: remember || null })
                .then(() => {
                  onDone();
                  onClose();
                })
                .catch(setErr)
            }
          >
            Spara beslut
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <ErrorBox error={err} />
        <Field label={needsNote ? "Motivering (krävs – sparas i granskningsdokumentationen)" : "Kommentar (valfri)"}>
          <textarea className={cx(inputCls, "h-24")} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        {(status === "ACCEPTED_OK" || status === "RESOLVED") && (
          <Field label="Kom ihåg bedömningen till och med (valfritt)" hint="Liknande fynd för kunden får då ditt beslut som förslag. Utan datum gäller minnet tills vidare (kan tas bort under Inställningar).">
            <input type="date" className={inputCls} value={remember} onChange={(e) => setRemember(e.target.value)} />
          </Field>
        )}
        <p className="text-[12px] text-muted">Beslutet gäller alla {c.findings.length} fynd i ärendet.</p>
      </div>
    </Modal>
  );
}

function AskClientModal({ c, base, onClose, onDone }: { c: Case; base: string; onClose: () => void; onDone: () => void }) {
  const draft = useLoad<{ text: string; source: string; ai_note?: string | null }>(`${base}/cases/${c.key}/question-draft`);
  const [text, setText] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [link, setLink] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const value = text ?? draft.data?.text ?? "";
  return (
    <Modal
      title="Fråga kunden"
      open
      onClose={onClose}
      footer={
        link ? (
          <Button onClick={onClose}>Klar</Button>
        ) : (
          <>
            <Button variant="secondary" onClick={onClose}>Avbryt</Button>
            <Button
              disabled={!value.trim()}
              onClick={() =>
                send<{ link: string }>(`${base}/questions`, "POST", { case_key: c.key, text: value, recipient_email: email || null })
                  .then((r) => {
                    setLink(r.link);
                    onDone();
                  })
                  .catch(setErr)
              }
            >
              Skapa fråga
            </Button>
          </>
        )
      }
    >
      <div className="space-y-3">
        <ErrorBox error={err ?? draft.error} />
        {link ? (
          <>
            <p>Frågan är skapad. Skicka länken till kunden (den gäller i 14 dagar och kräver ingen inloggning):</p>
            <div className="flex gap-2">
              <input readOnly className={inputCls} value={link} onFocus={(e) => e.target.select()} />
              <Button variant="secondary" onClick={() => navigator.clipboard?.writeText(link)}>Kopiera</Button>
            </div>
          </>
        ) : !draft.data && !draft.error ? (
          <Loading />
        ) : (
          <>
            <Field label="Fråga till kunden">
              <textarea className={cx(inputCls, "h-32")} value={value} onChange={(e) => setText(e.target.value)} />
            </Field>
            {draft.data && (
              <div className="flex items-center gap-2 text-[12px] text-muted">
                Utkast: <AiBadge source={draft.data.source === "ai" ? "ai" : "rules"} note={draft.data.ai_note} /> Granska och redigera innan du skickar.
              </div>
            )}
            <Field label="Kundens e-post (valfritt, för påminnelser)">
              <input type="email" className={inputCls} value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <p className="text-[12px] text-muted">Kunden ser bara frågetexten – inga interna fynd, belopp eller regelnamn.</p>
          </>
        )}
      </div>
    </Modal>
  );
}
