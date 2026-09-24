"use client";

import { useEffect, useState } from "react";
import { Button, Card, Empty, ErrorBox, Loading, cx, inputCls } from "@/components/ui";
import { useLoad } from "@/lib/api";
import { kr } from "@/lib/format";
import { VoucherLink, useClient } from "./shared";

type TxRow = {
  voucher: string;
  date: string;
  voucher_text: string;
  account: number;
  account_name: string;
  amount: string;
  text: string | null;
  status: string;
  source_line: number | null;
};

function monthRange(month: string): [string, string] {
  const [y, m] = month.split("-").map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return [`${month}-01`, `${month}-${String(last).padStart(2, "0")}`];
}

export function TransactionsTab() {
  const { base, month } = useClient();
  const [range, setRange] = useState(() => monthRange(month));
  const [account, setAccount] = useState("");
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState({ account: "", q: "" });
  const [page, setPage] = useState(1);
  useEffect(() => {
    setRange(monthRange(month));
    setPage(1);
  }, [month]);
  const params = new URLSearchParams({ from: range[0], to: range[1], page: String(page), page_size: "100" });
  if (applied.account) params.set("account", applied.account);
  if (applied.q) params.set("q", applied.q);
  const data = useLoad<{ rows: TxRow[]; total: number; page: number; page_size: number }>(`${base}/transactions?${params.toString()}`);
  const pages = data.data ? Math.max(1, Math.ceil(data.data.total / data.data.page_size)) : 1;

  return (
    <Card
      title="Transaktioner"
      actions={
        <a className="rounded-md border border-line bg-white px-3 py-1.5 text-[13px] hover:bg-canvas" href={`${base}/export/transactions.xlsx?period=${month}`}>
          Exportera månaden (Excel)
        </a>
      }
    >
      <form
        className="mb-3 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied({ account, q });
          setPage(1);
        }}
      >
        <label className="text-[12px] text-muted">
          Från
          <input type="date" className={cx(inputCls, "w-36")} value={range[0]} onChange={(e) => setRange([e.target.value, range[1]])} />
        </label>
        <label className="text-[12px] text-muted">
          Till
          <input type="date" className={cx(inputCls, "w-36")} value={range[1]} onChange={(e) => setRange([range[0], e.target.value])} />
        </label>
        <label className="text-[12px] text-muted">
          Konto
          <input inputMode="numeric" className={cx(inputCls, "w-24")} value={account} onChange={(e) => setAccount(e.target.value.replace(/\D/g, ""))} />
        </label>
        <label className="text-[12px] text-muted">
          Sök i text
          <input className={cx(inputCls, "w-56")} value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <Button type="submit" variant="secondary">Filtrera</Button>
      </form>
      <ErrorBox error={data.error} />
      {!data.data ? (
        <Loading />
      ) : data.data.rows.length === 0 ? (
        <Empty>Inga transaktioner matchar filtret.</Empty>
      ) : (
        <>
          <table className="data">
            <thead>
              <tr>
                <th>Ver</th>
                <th>Datum</th>
                <th>Text</th>
                <th>Konto</th>
                <th className="num">Debet</th>
                <th className="num">Kredit</th>
              </tr>
            </thead>
            <tbody>
              {data.data.rows.map((r, i) => {
                const n = Number(r.amount);
                return (
                  <tr key={`${r.voucher}-${i}`} className={r.status === "added" ? "bg-medium-soft/40" : ""}>
                    <td><VoucherLink v={r.voucher} hint={r.date} /></td>
                    <td className="whitespace-nowrap">{r.date}</td>
                    <td>{r.text || r.voucher_text}</td>
                    <td className="whitespace-nowrap">{r.account} <span className="text-muted">{r.account_name}</span></td>
                    <td className="num">{n > 0 ? kr(n) : ""}</td>
                    <td className="num">{n < 0 ? kr(-n) : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="mt-3 flex items-center justify-between text-[12px] text-muted">
            <span>{data.data.total.toLocaleString("sv-SE")} rader</span>
            <div className="flex items-center gap-2">
              <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Föregående</Button>
              <span>Sida {page} av {pages}</span>
              <Button variant="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>Nästa</Button>
            </div>
          </div>
          <p className="mt-2 text-[11px] text-muted">Rader på lönekonton visas bara för användare med behörigheten Lönedata.</p>
        </>
      )}
    </Card>
  );
}
