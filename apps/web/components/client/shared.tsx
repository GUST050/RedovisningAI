"use client";

import { createContext, useContext } from "react";
import { type Me, useLoad } from "@/lib/api";
import { kr, pct, sek } from "@/lib/format";
import { ErrorBox, Loading, Modal, cx } from "@/components/ui";

export type Company = {
  id: string;
  name: string;
  org_number: string | null;
  legal_form: string | null;
  industry: string | null;
  vat_period: string | null;
  food_retail: boolean;
  materiality: string;
  has_overdraft: boolean;
  accounting_method_override: string | null;
  source_system: string | null;
  settings: { manual_series?: string[]; suspense_accounts?: number[]; person_names?: string[] } | null;
};

export type View = "month" | "YTD" | "R12";

export type ClientCtx = {
  id: string;
  company: Company;
  me: Me;
  /** Vald månad, t.ex. "2026-09". */
  month: string;
  view: View;
  /** Periodspecifikation för analysvyer, t.ex. "YTD:2026-09". */
  spec: string;
  months: string[];
  /** `hint` är en period ("2026-03") eller ett datum ("2026-03-14") som avgör räkenskapsåret. */
  openVoucher: (key: string, hint?: string) => void;
  refresh: () => void;
  base: string;
};

export const ClientContext = createContext<ClientCtx | null>(null);
export function useClient(): ClientCtx {
  const c = useContext(ClientContext);
  if (!c) throw new Error("ClientContext saknas");
  return c;
}

export function Amount({ value, className }: { value: string | number | null | undefined; className?: string }) {
  return <td className={cx("num", className)}>{sek(value)}</td>;
}

export function Diff({ value, pctValue, inverse }: { value: string | null | undefined; pctValue?: string | null; inverse?: boolean }) {
  if (value === null || value === undefined) return <td className="num text-muted">–</td>;
  const n = Number(value);
  const good = inverse ? n < 0 : n > 0;
  return (
    <td className={cx("num", n === 0 ? "text-muted" : good ? "text-ok" : "text-high")}>
      {sek(value, { signed: true })}
      {pctValue !== undefined && pctValue !== null && <span className="ml-1 text-[11px]">({pct(pctValue)})</span>}
    </td>
  );
}

type VoucherView = {
  key: string;
  date: string;
  reg_date: string | null;
  text: string;
  rows: { account: number; account_name: string; amount: string; text: string | null; status: string; source_line: number | null }[];
  balance: string;
  source_line: number | null;
  payroll_rows_masked?: boolean;
};

export function VoucherModal({ base, voucherKey, hint, onClose }: { base: string; voucherKey: string | null; hint?: string; onClose: () => void }) {
  const q = !hint ? "" : /^\d{4}-\d{2}-\d{2}$/.test(hint) ? `?on=${hint}` : `?period=${encodeURIComponent(hint)}`;
  const { data, error } = useLoad<VoucherView>(voucherKey ? `${base}/vouchers/${encodeURIComponent(voucherKey)}${q}` : null);
  return (
    <Modal title={`Verifikation ${voucherKey ?? ""}`} open={!!voucherKey} onClose={onClose}>
      <ErrorBox error={error} />
      {!data && !error && <Loading />}
      {data && data.key === voucherKey && (
        <div className="space-y-3">
          <div className="text-[13px]">
            <div className="font-medium">{data.text || "(ingen text)"}</div>
            <div className="text-muted">
              Datum {data.date}
              {data.reg_date && ` · registrerad ${data.reg_date}`}
              {data.source_line && ` · rad ${data.source_line} i SIE-filen`}
            </div>
          </div>
          <table className="data">
            <thead>
              <tr><th>Konto</th><th>Text</th><th className="num">Debet</th><th className="num">Kredit</th></tr>
            </thead>
            <tbody>
              {data.rows.map((r, i) => {
                const n = Number(r.amount);
                return (
                  <tr key={i} className={r.status === "removed" ? "text-muted line-through" : ""}>
                    <td>{r.account} {r.account_name}{r.status === "added" && <span className="ml-1 text-[11px] text-medium">(tillagd)</span>}</td>
                    <td className="text-muted">{r.text}</td>
                    <td className="num">{n > 0 ? kr(n) : ""}</td>
                    <td className="num">{n < 0 ? kr(-n) : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {data.payroll_rows_masked && <p className="text-[12px] text-muted">Rader på lönekonton visas inte – kräver behörigheten Lönedata.</p>}
          {Number(data.balance) !== 0 && <p className="text-high">Verifikationen balanserar inte ({kr(data.balance)}).</p>}
        </div>
      )}
    </Modal>
  );
}

export function VoucherLink({ v, hint }: { v: string; hint?: string }) {
  const { openVoucher } = useClient();
  return (
    <button className="focus-ring font-mono text-[12px] text-brand hover:underline" onClick={() => openVoucher(v, hint)}>
      {v}
    </button>
  );
}
