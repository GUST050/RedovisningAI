"use client";

import { type ReactNode, useEffect } from "react";
import { SEVERITY_SV, STATUS_SV } from "@/lib/format";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-lg border border-line bg-panel", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-[15px] font-semibold">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  type = "button",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
}) {
  const styles = {
    primary: "bg-brand text-white hover:bg-[#173d60]",
    secondary: "border border-line bg-white text-ink hover:bg-canvas",
    ghost: "text-brand hover:bg-brand-soft",
    danger: "bg-high text-white hover:bg-[#8f1c13]",
  }[variant];
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={cx("focus-ring rounded-md px-3 py-1.5 text-[13px] font-medium transition disabled:cursor-not-allowed disabled:opacity-50", styles)}
    >
      {children}
    </button>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const s = {
    HIGH: "bg-high-soft text-high",
    MEDIUM: "bg-medium-soft text-medium",
    LOW: "bg-low-soft text-low",
  }[severity] ?? "bg-low-soft text-low";
  return <span className={cx("inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold", s)}>{SEVERITY_SV[severity] ?? severity}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  const tone =
    ["APPROVED", "REPORTED", "REVIEWED", "RESOLVED", "ACCEPTED_OK", "CLOSED", "ANSWERED", "AUTO_CLOSED", "OK"].includes(status)
      ? "bg-ok-soft text-ok"
      : ["CHANGED_AFTER_APPROVAL", "ERROR", "REVOKED"].includes(status)
        ? "bg-high-soft text-high"
        : ["NEEDS_REVIEW", "OPEN", "NEW", "PRELIMINARY", "ASK_CLIENT", "WAITING_CLIENT", "SENT"].includes(status)
          ? "bg-medium-soft text-medium"
          : "bg-low-soft text-low";
  return <span className={cx("inline-block rounded px-1.5 py-0.5 text-[11px] font-medium", tone)}>{STATUS_SV[status] ?? status}</span>;
}

/** Märkning av AI-genererat innehåll (AI Act art. 50). Texten visas alltid som ren text. */
export function AiBadge({ source }: { source?: string }) {
  if (source === "rules") {
    return <span className="rounded bg-low-soft px-1.5 py-0.5 text-[11px] text-low" title="Genererad av fasta regler (AI ej tillgänglig)">Regelbaserad text</span>;
  }
  return <span className="rounded bg-ai-soft px-1.5 py-0.5 text-[11px] font-medium text-ai" title="Texten är AI-genererad och ska granskas av konsulten. Siffror kommer från beräkningsmotorn.">AI-genererad</span>;
}

const CLAIM_LABEL: Record<string, string> = {
  OBSERVATION: "Iakttagelse",
  EXPLANATION: "Förklaring",
  HYPOTHESIS: "Hypotes",
  QUESTION: "Fråga",
};

export function Claims({ claims }: { claims: { type: string; rendered?: string; text: string }[] }) {
  if (!claims?.length) return <p className="text-muted">Inga påståenden.</p>;
  return (
    <ul className="space-y-1.5">
      {claims.map((c, i) => (
        <li key={i} className="flex gap-2">
          <span
            className={cx(
              "mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase",
              c.type === "HYPOTHESIS" ? "bg-medium-soft text-medium" : c.type === "QUESTION" ? "bg-brand-soft text-brand" : "bg-low-soft text-low",
            )}
          >
            {CLAIM_LABEL[c.type] ?? c.type}
          </span>
          {/* Ren text – ingen markdown/HTML-rendering av AI-output. */}
          <span>{c.rendered ?? c.text}</span>
        </li>
      ))}
    </ul>
  );
}

export function Tabs({ tabs, active, onChange }: { tabs: { id: string; label: string; badge?: number }[]; active: string; onChange: (id: string) => void }) {
  return (
    <nav className="flex flex-wrap gap-1 border-b border-line">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={cx(
            "focus-ring -mb-px border-b-2 px-3 py-2 text-[13px] font-medium",
            active === t.id ? "border-brand text-brand" : "border-transparent text-muted hover:text-ink",
          )}
        >
          {t.label}
          {t.badge ? <span className="ml-1.5 rounded-full bg-high px-1.5 text-[10px] text-white">{t.badge}</span> : null}
        </button>
      ))}
    </nav>
  );
}

export function Modal({ title, open, onClose, children, footer }: { title: string; open: boolean; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/30 p-6" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} className="mt-10 w-full max-w-2xl rounded-lg bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-line px-5 py-3">
          <h3 className="text-[15px] font-semibold">{title}</h3>
          <button onClick={onClose} className="focus-ring rounded px-2 text-muted hover:text-ink" aria-label="Stäng">✕</button>
        </header>
        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-md border border-dashed border-line p-6 text-center text-muted">{children}</div>;
}

export function Loading() {
  return <div className="p-6 text-muted">Laddar…</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="rounded-md border border-high/30 bg-high-soft px-3 py-2 text-high">{error instanceof Error ? error.message : String(error)}</div>;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block space-y-1">
      <span className="text-[12px] font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

export const inputCls = "focus-ring w-full rounded-md border border-line bg-white px-2.5 py-1.5 text-[13px]";
