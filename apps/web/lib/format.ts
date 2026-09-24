// Svensk formatering för värden som inte redan formaterats av API:t.

const MINUS = "−";

export function sek(value: string | number | null | undefined, opts: { signed?: boolean } = {}): string {
  if (value === null || value === undefined || value === "") return "–";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return String(value);
  const abs = Math.abs(n);
  let body: string;
  if (abs >= 1_000_000) body = (abs / 1_000_000).toLocaleString("sv-SE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " Mkr";
  else if (abs >= 10_000) body = Math.round(abs / 1000).toLocaleString("sv-SE") + " tkr";
  else body = Math.round(abs).toLocaleString("sv-SE") + " kr";
  if (n < 0) return MINUS + body;
  return (opts.signed && n > 0 ? "+" : "") + body;
}

export function kr(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const n = typeof value === "number" ? value : Number(value);
  return n.toLocaleString("sv-SE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).replace("-", MINUS);
}

export function pct(value: string | number | null | undefined, signed = true): string {
  if (value === null || value === undefined || value === "") return "–";
  const n = typeof value === "number" ? value : Number(value);
  const s = Math.abs(n).toLocaleString("sv-SE", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " %";
  return n < 0 ? MINUS + s : (signed && n > 0 ? "+" : "") + s;
}

const MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];

export function monthLabel(spec: string | null | undefined): string {
  if (!spec) return "–";
  const m = /^(\d{4})-(\d{2})/.exec(spec);
  return m ? `${MONTHS[Number(m[2]) - 1]} ${m[1]}` : spec;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("sv-SE", { dateStyle: "short", timeStyle: "short" });
}

export const STATUS_SV: Record<string, string> = {
  NO_DATA: "Ingen data",
  DATA_RECEIVED: "Data mottagen",
  PRELIMINARY: "Preliminär",
  NEEDS_REVIEW: "Behöver granskning",
  REVIEWED: "Granskad",
  APPROVED: "Godkänd",
  REPORTED: "Rapporterad",
  CHANGED_AFTER_APPROVAL: "Ändrad efter godkännande",
  OPEN: "Öppet",
  IN_PROGRESS: "Under utredning",
  WAITING_CLIENT: "Väntar på kund",
  CLOSED: "Stängt",
  NEW: "Ny",
  ASK_CLIENT: "Fråga till kund",
  RESOLVED: "Åtgärdat",
  ACCEPTED_OK: "Bedömt OK",
  SUPPRESSED: "Undertryckt",
  AUTO_CLOSED: "Rättat i bokföringen",
  SENT: "Skickad",
  ANSWERED: "Besvarad",
  EXPIRED: "Utgången",
};

export const SEVERITY_SV: Record<string, string> = { HIGH: "Hög", MEDIUM: "Medel", LOW: "Låg" };

/** "2026-09" → "sep 2026", "YTD:2026-09" → "hittills i år t.o.m. sep 2026", "R12:2026-09" → "12 mån t.o.m. sep 2026". */
export function periodLabel(spec: string | null | undefined): string {
  if (!spec) return "–";
  const [kind, rest] = spec.includes(":") ? spec.split(":", 2) : ["", spec];
  if (kind === "YTD") return `hittills i år t.o.m. ${monthLabel(rest)}`;
  if (kind === "R12") return `12 mån t.o.m. ${monthLabel(rest)}`;
  return /^\d{4}-\d{2}$/.test(spec) ? monthLabel(spec) : spec;
}
