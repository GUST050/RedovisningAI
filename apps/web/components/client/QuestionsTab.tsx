"use client";

import { Button, Card, Empty, ErrorBox, Loading, StatusBadge } from "@/components/ui";
import { send, useLoad } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useMe } from "@/components/AppShell";

type Question = {
  id: string;
  case_key: string;
  text: string;
  status: string;
  sent_by: string;
  sent_at: string;
  expires_at: string;
  answered_at: string | null;
  answer_text: string | null;
  attachments: { name: string; size: number; key: string }[];
};

export function QuestionsTab({ base }: { base: string }) {
  const me = useMe();
  const qs = useLoad<Question[]>(`${base}/questions`);
  if (qs.error) return <ErrorBox error={qs.error} />;
  if (!qs.data) return <Loading />;
  return (
    <Card title="Frågor till kunden">
      {qs.data.length === 0 ? (
        <Empty>Inga frågor skickade. Skapa en fråga från ett ärende med knappen &quot;Fråga kunden&quot;.</Empty>
      ) : (
        <div className="space-y-3">
          {qs.data.map((q) => (
            <div key={q.id} className="rounded-md border border-line p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="whitespace-pre-wrap">{q.text}</div>
                <StatusBadge status={q.status} />
              </div>
              <div className="mt-1 text-[12px] text-muted">
                Skickad {dateTime(q.sent_at)} av {q.sent_by} · länken gäller till {dateTime(q.expires_at)}
              </div>
              {q.answer_text && (
                <div className="mt-2 rounded-md bg-canvas p-2">
                  <div className="text-[12px] font-semibold text-muted">Kundens svar {dateTime(q.answered_at)}</div>
                  <div className="whitespace-pre-wrap">{q.answer_text}</div>
                  {q.attachments.length > 0 && (
                    <ul className="mt-1 text-[12px]">
                      {q.attachments.map((a) => (
                        <li key={a.key}>
                          📎{" "}
                          <a className="text-brand hover:underline" href={`${base}/questions/${q.id}/attachments/${a.key}`}>
                            {a.name}
                          </a>{" "}
                          <span className="text-muted">({Math.ceil(a.size / 1024)} kB)</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              {me?.permissions.write && q.status !== "CLOSED" && (
                <div className="mt-2">
                  <Button variant="secondary" onClick={() => send(`${base}/questions/${q.id}/close`, "POST").then(qs.reload)}>
                    Markera som klar
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
