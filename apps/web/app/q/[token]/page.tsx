"use client";

import { useParams } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { Button, ErrorBox, Loading, cx, inputCls } from "@/components/ui";
import { ApiError, get, send } from "@/lib/api";
import { dateTime } from "@/lib/format";

type PublicQuestion = {
  id: string;
  text: string;
  status: string;
  company_name: string | null;
  firm_name: string | null;
  sent_at: string;
  expires_at: string;
  answer_text: string | null;
  attachments: { name: string; size: number }[];
};

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.heic,.txt,.xlsx";
const MAX_BYTES = 10 * 1024 * 1024;

/** Publik svarssida för kunden. Kräver ingen inloggning; länken är personlig och tidsbegränsad. */
export default function PublicQuestionPage() {
  const { token } = useParams<{ token: string }>();
  const [q, setQ] = useState<PublicQuestion | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [answer, setAnswer] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    get<PublicQuestion>(`/api/public/questions/${encodeURIComponent(token)}`).then(setQ).catch(setError);
  }, [token]);

  if (error instanceof ApiError && error.status === 404) {
    return (
      <Shell>
        <h1 className="mb-2 text-lg font-semibold">Länken fungerar inte</h1>
        <p className="text-muted">Länken är ogiltig eller har gått ut. Kontakta din redovisningskonsult om du behöver en ny.</p>
      </Shell>
    );
  }
  if (!q) return <Shell>{error ? <ErrorBox error={error} /> : <Loading />}</Shell>;

  const tooBig = files.find((f) => f.size > MAX_BYTES);
  const closed = q.status !== "SENT" && q.status !== "ANSWERED";

  return (
    <Shell firm={q.firm_name}>
      <p className="text-[12px] text-muted">
        Fråga till {q.company_name ?? "er"} · skickad {dateTime(q.sent_at)} · svara senast {dateTime(q.expires_at)}
      </p>
      <div className="my-3 whitespace-pre-wrap rounded-md bg-canvas p-4 text-[15px]">{q.text}</div>

      {q.answer_text && (
        <div className="mb-4 rounded-md border border-ok/30 bg-ok-soft p-3">
          <div className="text-[12px] font-semibold text-ok">Ditt svar{sent ? " har skickats – tack!" : ""}</div>
          <div className="whitespace-pre-wrap">{q.answer_text}</div>
          {q.attachments.length > 0 && <div className="mt-1 text-[12px] text-muted">Bilagor: {q.attachments.map((a) => a.name).join(", ")}</div>}
        </div>
      )}

      {closed ? (
        <p className="text-muted">Frågan är avslutad.</p>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            const fd = new FormData();
            fd.append("answer", answer);
            files.forEach((f) => fd.append("files", f));
            setBusy(true);
            setError(null);
            send<PublicQuestion>(`/api/public/questions/${encodeURIComponent(token)}`, "POST", fd)
              .then((r) => {
                setQ(r);
                setSent(true);
                setAnswer("");
                setFiles([]);
              })
              .catch(setError)
              .finally(() => setBusy(false));
          }}
        >
          <label className="block">
            <span className="text-[13px] font-medium">{q.answer_text ? "Komplettera ditt svar" : "Ditt svar"}</span>
            <textarea className={cx(inputCls, "mt-1 h-36 text-[14px]")} value={answer} onChange={(e) => setAnswer(e.target.value)} maxLength={10000} required />
          </label>
          <div>
            <Button variant="secondary" onClick={() => input.current?.click()}>Bifoga underlag</Button>
            <input
              ref={input}
              type="file"
              multiple
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => setFiles(Array.from(e.target.files ?? []).slice(0, 5))}
            />
            <span className="ml-2 text-[12px] text-muted">PDF, bild, text eller Excel · högst 5 filer à 10 MB</span>
            {files.length > 0 && (
              <ul className="mt-1 text-[13px]">
                {files.map((f) => (
                  <li key={f.name} className={f.size > MAX_BYTES ? "text-high" : ""}>📎 {f.name} ({Math.ceil(f.size / 1024)} kB)</li>
                ))}
              </ul>
            )}
          </div>
          <ErrorBox error={error} />
          <Button type="submit" disabled={busy || !answer.trim() || !!tooBig}>{busy ? "Skickar…" : "Skicka svar"}</Button>
        </form>
      )}
      <p className="mt-6 text-[11px] text-muted">
        Svaret och bilagorna skickas krypterat till din redovisningsbyrå och sparas som underlag till bokföringen.
      </p>
    </Shell>
  );
}

function Shell({ children, firm }: { children: ReactNode; firm?: string | null }) {
  return (
    <main className="mx-auto max-w-xl px-4 py-10">
      <div className="mb-4 text-[13px] font-semibold text-brand">{firm ?? "Din redovisningsbyrå"}</div>
      <div className="rounded-lg border border-line bg-white p-6">{children}</div>
    </main>
  );
}
