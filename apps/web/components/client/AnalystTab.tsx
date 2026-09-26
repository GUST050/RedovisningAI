"use client";

import { useState } from "react";
import { AiBadge, Button, Card, Claims, ErrorBox, cx, inputCls } from "@/components/ui";
import { type Claim, send } from "@/lib/api";
import { useClient } from "./shared";

type Answer = {
  question: string;
  claims: Claim[];
  source: string;
  ai_note?: string | null;
  tool_calls: { tool: string; input: Record<string, unknown> }[];
};

const EXAMPLES = [
  "Varför minskade rörelseresultatet jämfört med förra året?",
  "Vilka kostnader har ökat mest hittills i år?",
  "Finns det nya leverantörer den här perioden?",
  "Hur har bruttomarginalen utvecklats de senaste 12 månaderna?",
];

export function AnalystTab() {
  const { base, spec } = useClient();
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Answer[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const ask = (q: string) => {
    setBusy(true);
    setErr(null);
    send<{ data: { claims: Claim[] }; source: string; ai_note?: string | null; tool_calls: Answer["tool_calls"] }>(`${base}/ask`, "POST", { question: q, period: spec })
      .then((r) => {
        setHistory((h) => [{ question: q, claims: r.data.claims, source: r.source, ai_note: r.ai_note, tool_calls: r.tool_calls ?? [] }, ...h]);
        setQuestion("");
      })
      .catch(setErr)
      .finally(() => setBusy(false));
  };

  return (
    <div className="space-y-4">
      <Card title="Fråga AI-analytikern">
        <p className="mb-2 text-[13px] text-muted">
          Analytikern hämtar siffror med beräkningsmotorn (läsverktyg – den kan inte ändra något) och får bara återge tal som motorn räknat fram.
          Svaren är underlag för dig som konsult och ska granskas.
        </p>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (question.trim().length >= 3) ask(question.trim());
          }}
        >
          <input className={inputCls} placeholder="Ställ en fråga om bolagets siffror…" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={1000} />
          <Button type="submit" disabled={busy || question.trim().length < 3}>{busy ? "Analyserar…" : "Fråga"}</Button>
        </form>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {EXAMPLES.map((e) => (
            <button key={e} className="focus-ring rounded-full border border-line px-2.5 py-0.5 text-[12px] text-muted hover:text-ink" onClick={() => ask(e)} disabled={busy}>
              {e}
            </button>
          ))}
        </div>
        <div className="mt-2"><ErrorBox error={err} /></div>
      </Card>
      {history.map((a, i) => (
        <Card
          key={history.length - i}
          title={a.question}
          actions={<AiBadge source={a.source === "ai" ? "ai" : "rules"} note={a.ai_note} />}
        >
          {a.source === "ai" ? <Claims claims={a.claims} /> : <p>{a.claims.map((c) => c.rendered ?? c.text).join(" ")}</p>}
          {a.tool_calls.length > 0 && (
            <details className="mt-3 text-[12px] text-muted">
              <summary className="cursor-pointer">Så togs svaret fram ({a.tool_calls.length} uppslag i beräkningsmotorn)</summary>
              <ul className={cx("mt-1 space-y-0.5 font-mono text-[11px]")}>
                {a.tool_calls.map((t, j) => (
                  <li key={j}>
                    {t.tool}({JSON.stringify(t.input)})
                  </li>
                ))}
              </ul>
            </details>
          )}
        </Card>
      ))}
    </div>
  );
}
