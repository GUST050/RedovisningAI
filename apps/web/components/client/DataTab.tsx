"use client";

import { useRef, useState } from "react";
import { useMe } from "@/components/AppShell";
import { Button, Card, Empty, ErrorBox, Field, Loading, StatusBadge, cx, inputCls } from "@/components/ui";
import { send, useLoad } from "@/lib/api";
import { dateTime, kr, sek } from "@/lib/format";
import type { Company } from "./shared";

type ImportRun = {
  id: string;
  seq: number;
  source: string;
  status: string;
  has_vouchers: boolean;
  fiscal_year: { start: string; end: string };
  stats: { added?: number; changed?: number; removed?: number; vouchers?: number };
  created_at: string;
  file: { name: string; sha256: string; size: number; encoding: string; format: string | null; issues: { code: string; line: number | null; message: string; severity: string }[] } | null;
};

type Connection = { id: string; source: string; status: string; last_success_at: string | null; last_error: string | null; authorized_at: string | null };

type UploadResult = {
  duplicate: boolean;
  format: string | null;
  columns: string | null;
  stats: Record<string, number>;
  issues: { message: string; severity: string }[];
  reviewed_periods: string[];
};

type TaxRec = {
  as_of: string;
  skv_balance: string;
  book_balance: string;
  difference: string;
  unmatched_skv: { date: string; text: string; amount: string }[];
  unmatched_book: { date: string; text: string; amount: string; voucher?: string }[];
};

const SOURCE_SV: Record<string, string> = {
  sie_file: "SIE-fil",
  csv_file: "CSV-fil",
  excel_file: "Excelfil",
  standard_file: "Standardformat",
  fortnox: "Fortnox",
  spiris: "Spiris",
};

const FORMAT_SV: Record<string, string> = { CSV: "CSV", XLSX: "Excel", "RAI-JSON": "standardformat (JSON)" };

export function DataTab({ base, company, onImported }: { base: string; company: Company; onImported: () => void }) {
  const me = useMe();
  const imports = useLoad<ImportRun[]>(`${base}/imports`);
  const conns = useLoad<Connection[]>(`${base}/connections`);
  const write = !!me?.permissions.write;

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      {write && (
        <SieUpload
          base={base}
          onDone={() => {
            imports.reload();
            onImported();
          }}
        />
      )}
      <ConnectionsCard base={base} conns={conns.data} error={conns.error} write={write} onChanged={() => { conns.reload(); imports.reload(); onImported(); }} />
      <Card
        title="Importer"
        className="xl:col-span-2"
        actions={
          me?.permissions.payroll && imports.data && imports.data.length > 0 ? (
            <a href={`${base}/export/ledger.json`} className="rounded-md border border-line bg-white px-3 py-1 text-[12px] hover:bg-canvas" title="Hela bokföringen i RedovisningAI:s standardformat (JSON), samma data som analysen bygger på">
              Exportera standardformat
            </a>
          ) : undefined
        }
      >
        <ErrorBox error={imports.error} />
        {!imports.data ? (
          <Loading />
        ) : imports.data.length === 0 ? (
          <Empty>Ingen bokföring importerad för {company.name} ännu.</Empty>
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>#</th>
                <th>Tid</th>
                <th>Källa</th>
                <th>Räkenskapsår</th>
                <th>Fil</th>
                <th className="num">Ver. (nya/ändrade/borttagna)</th>
                <th>Anmärkningar</th>
              </tr>
            </thead>
            <tbody>
              {imports.data.map((r) => (
                <tr key={r.id}>
                  <td>{r.seq}</td>
                  <td className="whitespace-nowrap">{dateTime(r.created_at)}</td>
                  <td>{SOURCE_SV[r.source] ?? r.source}</td>
                  <td className="whitespace-nowrap">{r.fiscal_year.start} – {r.fiscal_year.end}{!r.has_vouchers && <div className="text-[11px] text-muted">bara saldon</div>}</td>
                  <td className="text-[12px]">
                    {r.file ? (
                      <>
                        {r.file.name}
                        <div className="text-muted">{r.file.format ? `${FORMAT_SV[r.file.format] ?? r.file.format} · ` : ""}{Math.ceil(r.file.size / 1024)} kB · {r.file.encoding} · <span className="font-mono" title={r.file.sha256}>{r.file.sha256.slice(0, 10)}…</span></div>
                      </>
                    ) : "–"}
                  </td>
                  <td className="num">{r.stats.added ?? 0} / {r.stats.changed ?? 0} / {r.stats.removed ?? 0}</td>
                  <td className="text-[12px] text-muted">
                    {r.file?.issues.length ? (
                      <details>
                        <summary className="cursor-pointer">{r.file.issues.length} st</summary>
                        <ul>
                          {r.file.issues.slice(0, 20).map((i, k) => (
                            <li key={k} className={i.severity.toLowerCase() === "error" ? "text-high" : i.severity.toLowerCase() === "warning" ? "text-medium" : ""}>{i.line ? `Rad ${i.line}: ` : ""}{i.message}</li>
                          ))}
                        </ul>
                      </details>
                    ) : "–"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {write && <TaxAccountCard base={base} />}
      {write && <BulkUpload />}
    </div>
  );
}

function SieUpload({ base, onDone }: { base: string; onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const upload = (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    setBusy(true);
    setErr(null);
    setResult(null);
    send<UploadResult>(`${base}/imports`, "POST", fd)
      .then((r) => {
        setResult(r);
        onDone();
      })
      .catch(setErr)
      .finally(() => setBusy(false));
  };
  return (
    <Card title="Ladda upp bokföring">
      <div
        className="rounded-md border-2 border-dashed border-line p-6 text-center"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const f = e.dataTransfer.files[0];
          if (f) upload(f);
        }}
      >
        <p className="mb-2 text-muted">Dra hit en SIE-fil, en verifikationslista eller huvudbok som CSV/Excel (t.ex. export från Fortnox) eller en fil i standardformat – eller</p>
        <Button variant="secondary" disabled={busy} onClick={() => input.current?.click()}>{busy ? "Importerar och granskar…" : "Välj fil"}</Button>
        <input ref={input} type="file" accept=".se,.si,.sie,.txt,.csv,.xlsx,.json" className="hidden" onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) upload(file);
        }} />
        <p className="mt-2 text-[11px] text-muted">Allt översätts till samma standardformat innan analysen. Filen krypteras och sparas som underlag. Samma fil två gånger importeras inte igen.</p>
      </div>
      <div className="mt-3 space-y-2">
        <ErrorBox error={err} />
        {result && (
          <div className="rounded-md border border-ok/30 bg-ok-soft p-3 text-[13px]">
            {result.duplicate ? (
              <p>Filen är redan importerad – inget ändrades.</p>
            ) : (
              <>
                <p className="font-medium text-ok">Importen är klar{result.format ? ` (${FORMAT_SV[result.format] ?? result.format})` : ""}.</p>
                {result.columns && <p className="text-[12px] text-muted">Kolumner: {result.columns}</p>}
                <p>
                  {result.stats.vouchers ?? 0} verifikationer ({result.stats.added ?? 0} nya, {result.stats.changed ?? 0} ändrade, {result.stats.removed ?? 0} borttagna).
                  {result.reviewed_periods.length > 0 && ` Granskade perioder: ${result.reviewed_periods.join(", ")}.`}
                </p>
              </>
            )}
            {result.issues.some((i) => i.severity !== "info") && (
              <ul className="mt-1 list-disc pl-4 text-[12px] text-medium">
                {result.issues.filter((i) => i.severity !== "info").slice(0, 8).map((i, k) => <li key={k}>{i.message}</li>)}
              </ul>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

function ConnectionsCard({ base, conns, error, write, onChanged }: { base: string; conns: Connection[] | null; error: unknown; write: boolean; onChanged: () => void }) {
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const fortnox = conns?.find((c) => c.source === "fortnox");
  return (
    <Card title="Koppling till bokföringssystem">
      <ErrorBox error={error ?? err} />
      {!conns ? (
        <Loading />
      ) : (
        <div className="space-y-3">
          {conns.filter((c) => c.source !== "sie_file").map((c) => (
            <div key={c.id} className="flex items-start justify-between rounded-md border border-line p-3">
              <div>
                <div className="font-medium">{SOURCE_SV[c.source] ?? c.source}</div>
                <div className="text-[12px] text-muted">
                  Senast hämtad: {dateTime(c.last_success_at)} · godkänd {dateTime(c.authorized_at)}
                </div>
                {c.last_error && <div className="text-[12px] text-high">{c.last_error}</div>}
              </div>
              <StatusBadge status={c.status} />
            </div>
          ))}
          {write && (
            <div className="flex flex-wrap gap-2">
              <Button
                variant={fortnox ? "secondary" : "primary"}
                onClick={() =>
                  send<{ url: string }>(`${base}/connections/fortnox/authorize`, "POST")
                    .then((r) => (window.location.href = r.url))
                    .catch(setErr)
                }
              >
                {fortnox ? "Förnya Fortnox-behörighet" : "Koppla Fortnox"}
              </Button>
              {fortnox && (
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() => {
                    setBusy(true);
                    setMsg(null);
                    send<{ status: string; imported_years?: string[]; errors?: string[]; error?: string }>(`${base}/sync`, "POST")
                      .then((r) => {
                        setMsg(
                          r.status === "OK"
                            ? `Hämtade ${r.imported_years?.length ?? 0} räkenskapsår.`
                            : r.status === "no_connection"
                              ? "Ingen koppling finns."
                              : r.error ?? r.errors?.join(" ") ?? `Status: ${r.status}`,
                        );
                        onChanged();
                      })
                      .catch(setErr)
                      .finally(() => setBusy(false));
                  }}
                >
                  {busy ? "Hämtar…" : "Hämta nu"}
                </Button>
              )}
            </div>
          )}
          {msg && <p className="text-[13px]">{msg}</p>}
          <p className="text-[12px] text-muted">
            Fortnox hämtas automatiskt varje natt (bokföringen som SIE4 via Fortnox API, med byråns servicekonto). Spiris och Björn Lundén: exportera SIE4 och ladda upp filen.
          </p>
        </div>
      )}
    </Card>
  );
}

function TaxAccountCard({ base }: { base: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [opening, setOpening] = useState("0");
  const [rec, setRec] = useState<TaxRec | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const upload = (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    setErr(null);
    send<TaxRec>(`${base}/tax-account?opening_balance=${encodeURIComponent(opening.replace(",", ".").replace(/\s/g, "") || "0")}`, "POST", fd).then(setRec).catch(setErr);
  };
  return (
    <Card title="Skattekontot mot bokföringen (konto 1630)">
      <p className="mb-2 text-[13px] text-muted">Ladda upp ett utdrag från Skatteverkets e-tjänst Skattekonto (CSV). Transaktionerna stäms av mot konto 1630.</p>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Ingående saldo på skattekontot">
          <input className={cx(inputCls, "w-40")} value={opening} onChange={(e) => setOpening(e.target.value)} />
        </Field>
        <Button variant="secondary" onClick={() => input.current?.click()}>Välj CSV-fil</Button>
        <input ref={input} type="file" accept=".csv,.txt" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
      </div>
      <div className="mt-3 space-y-2">
        <ErrorBox error={err} />
        {rec && (
          <div className="space-y-2 text-[13px]">
            <table className="data">
              <tbody>
                <tr><td>Saldo enligt Skatteverket {rec.as_of}</td><td className="num">{kr(rec.skv_balance)}</td></tr>
                <tr><td>Saldo konto 1630</td><td className="num">{kr(rec.book_balance)}</td></tr>
                <tr className="font-semibold"><td>Differens</td><td className={cx("num", Number(rec.difference) !== 0 && "text-high")}>{kr(rec.difference)}</td></tr>
              </tbody>
            </table>
            {rec.unmatched_skv.length > 0 && (
              <div>
                <div className="font-medium">Finns hos Skatteverket men inte i bokföringen</div>
                <ul className="text-[12px]">{rec.unmatched_skv.map((t, i) => <li key={i}>{t.date} {t.text} {sek(t.amount)}</li>)}</ul>
              </div>
            )}
            {rec.unmatched_book.length > 0 && (
              <div>
                <div className="font-medium">Finns i bokföringen men inte hos Skatteverket</div>
                <ul className="text-[12px]">{rec.unmatched_book.map((t, i) => <li key={i}>{t.date} {t.voucher ?? ""} {t.text} {sek(t.amount)}</li>)}</ul>
              </div>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

function BulkUpload() {
  const input = useRef<HTMLInputElement>(null);
  const [res, setRes] = useState<{ file: string; status: string; error?: string; company_name?: string }[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const STATUS: Record<string, string> = { imported: "Importerad", duplicate: "Redan importerad", no_match: "Ingen kund med orgnr", error: "Fel" };
  return (
    <Card title="Många kunder på en gång (zip)">
      <p className="mb-2 text-[13px] text-muted">Zip-fil med SIE-filer (eller andra bokföringsfiler som anger organisationsnummer). Varje fil kopplas till rätt kund via organisationsnumret i filen.</p>
      <Button variant="secondary" disabled={busy} onClick={() => input.current?.click()}>{busy ? "Importerar…" : "Välj zip-fil"}</Button>
      <input
        ref={input}
        type="file"
        accept=".zip"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          const fd = new FormData();
          fd.append("file", f);
          setBusy(true);
          send<{ results: typeof res }>("/api/imports/bulk", "POST", fd)
            .then((r) => setRes(r.results))
            .catch(setErr)
            .finally(() => setBusy(false));
        }}
      />
      <div className="mt-3"><ErrorBox error={err} /></div>
      {res && (
        <ul className="mt-2 space-y-0.5 text-[12px]">
          {res.map((r, i) => (
            <li key={i} className={r.status === "imported" ? "text-ok" : r.status === "duplicate" ? "text-muted" : "text-high"}>
              {r.file}: {STATUS[r.status] ?? r.status}{r.company_name ? ` (${r.company_name})` : ""}{r.error ? ` – ${r.error}` : ""}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
