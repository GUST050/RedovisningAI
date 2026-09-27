"use client";

import { useState } from "react";
import { AiBadge, Button, Card, Claims, ErrorBox, Loading } from "@/components/ui";
import { ApiError, type PeriodCommentary, send, useLoad } from "@/lib/api";
import { dateTime, monthLabel } from "@/lib/format";
import { useClient } from "./shared";

/** Periodanalysen (A3) som den visas både på Översikt och under Rapporter. */
export function PeriodAnalysis({ commentary, emptyText }: { commentary: PeriodCommentary | null; emptyText: string }) {
  if (!commentary) return <p className="text-muted">{emptyText}</p>;
  return (
    <div className="space-y-2">
      {commentary.stale && (
        <p className="rounded bg-medium-soft px-3 py-2 text-[12px] text-medium">
          Analysen bygger på ändrad bokföring, jämförelse eller promptversion och är inaktuell. Skapa en ny innan den används.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
        <AiBadge source={commentary.source === "ai" ? "ai" : "rules"} />
        {commentary.created_at && (
          <span>
            {dateTime(commentary.created_at)} · {commentary.by}
            {commentary.compare_period ? ` · jämför med ${monthLabel(commentary.compare_period)}` : ""}
          </span>
        )}
      </div>
      <Claims claims={commentary.data.claims} />
    </div>
  );
}

/**
 * AI-analysen av vald månad på Översikt. Att visa den kostar inga tokens: analysen skapas i
 * bakgrunden efter uppladdning och granskning, och bara knappen anropar AI direkt.
 */
export function AiAnalysisCard() {
  const { base, month, me } = useClient();
  const detail = useLoad<{ commentary: PeriodCommentary | null }>(`${base}/periods/${month}`);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const commentary = detail.data?.commentary ?? null;
  const notReviewed = detail.error instanceof ApiError && detail.error.status === 404;

  const create = () => {
    // Behåll den sparade analysens jämförelse; utan sparad analys gäller samma månad föregående år.
    const compare = commentary?.compare_period ? `?compare=${encodeURIComponent(commentary.compare_period)}` : "";
    setBusy(true);
    setErr(null);
    send(`${base}/periods/${month}/commentary${compare}`, "POST")
      .then(detail.reload)
      .catch(setErr)
      .finally(() => setBusy(false));
  };

  let body;
  if (notReviewed) {
    body = <p className="text-muted">Perioden har inte granskats än. Analysen skapas automatiskt efter granskningen.</p>;
  } else if (detail.error) {
    body = <ErrorBox error={detail.error} />;
  } else if (!detail.data) {
    body = <Loading />;
  } else {
    body = (
      <PeriodAnalysis
        commentary={commentary}
        emptyText="Ingen analys för månaden ännu. Den skapas automatiskt efter uppladdning och granskning, eller direkt med knappen."
      />
    );
  }

  return (
    <Card
      title={`AI-analys – ${monthLabel(month)}`}
      actions={
        me.permissions.write &&
        !notReviewed && (
          <Button variant="secondary" disabled={busy || !detail.data} onClick={create}>
            {busy ? "Analyserar…" : commentary ? "Skapa ny analys" : "Skapa analys"}
          </Button>
        )
      }
    >
      <div className="space-y-2">
        <ErrorBox error={err} />
        {body}
      </div>
    </Card>
  );
}
