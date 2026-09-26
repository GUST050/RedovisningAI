"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo, useState } from "react";
import { AppShell, useMe } from "@/components/AppShell";
import { ClientContext, type ClientCtx, type Company, type View, VoucherModal } from "@/components/client/shared";
import { OverviewTab } from "@/components/client/OverviewTab";
import { ComparisonTab } from "@/components/client/ComparisonTab";
import { CasesTab } from "@/components/client/CasesTab";
import { StatementsTab } from "@/components/client/StatementsTab";
import { CostsTab } from "@/components/client/CostsTab";
import { TransactionsTab } from "@/components/client/TransactionsTab";
import { QuestionsTab } from "@/components/client/QuestionsTab";
import { AnalystTab } from "@/components/client/AnalystTab";
import { ReportsTab } from "@/components/client/ReportsTab";
import { DataTab } from "@/components/client/DataTab";
import { AmlTab } from "@/components/client/AmlTab";
import { SettingsTab } from "@/components/client/SettingsTab";
import { ErrorBox, Loading, StatusBadge, Tabs, cx, inputCls } from "@/components/ui";
import { type Overview, useLoad } from "@/lib/api";
import { monthLabel } from "@/lib/format";

export default function ClientPage() {
  return (
    <AppShell>
      <Suspense fallback={<Loading />}>
        <ClientWorkspace />
      </Suspense>
    </AppShell>
  );
}

type Tab = { id: string; label: string; badge?: number };

function ClientWorkspace() {
  const me = useMe();
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const search = useSearchParams();
  const base = `/api/companies/${id}`;
  const tab = search.get("tab") ?? "overview";
  const [view, setView] = useState<View | null>(null);
  const [month, setMonth] = useState<string | null>(search.get("period"));
  const [voucher, setVoucher] = useState<{ key: string; hint?: string } | null>(null);
  const [nonce, setNonce] = useState(0);

  const company = useLoad<Company>(`${base}?n=${nonce}`);
  // Översikten ger senaste månaden med data och rekommenderad vy (periodmognad).
  const overview = useLoad<Overview & { latest_month: string | null }>(`${base}/overview${month ? `?period=${month}` : ""}${month ? "&" : "?"}n=${nonce}`);
  const cases = useLoad<{ severity: string; status: string }[]>(`${base}/cases?n=${nonce}`);

  const setTab = useCallback(
    (t: string) => {
      const p = new URLSearchParams(search.toString());
      p.set("tab", t);
      router.replace(`/clients/${id}?${p.toString()}`, { scroll: false });
    },
    [id, router, search],
  );

  const ov = overview.data;
  const effMonth = month ?? ov?.period.spec ?? null;
  const effView: View = view ?? (ov?.recommended_view === "r12" ? "R12" : "month");
  const latest = ov?.latest_month ?? null;
  // Månader efter senaste månaden med verifikationer (t.ex. bara budget/PSALDO) visas inte.
  const months = useMemo(
    () => (ov?.months_with_data ?? []).map((m) => m.slice(0, 7)).filter((m) => !latest || m <= latest).reverse(),
    [ov, latest],
  );

  if (company.error) return <ErrorBox error={company.error} />;
  if (!company.data || !me) return <Loading />;

  const noData = overview.error !== null && overview.data === null;
  const ctx: ClientCtx | null =
    effMonth && ov
      ? {
          id,
          company: company.data,
          me,
          month: effMonth,
          view: effView,
          spec: effView === "month" ? effMonth : `${effView}:${effMonth}`,
          months,
          base,
          openVoucher: (key, hint) => setVoucher({ key, hint }),
          refresh: () => setNonce((n) => n + 1),
        }
      : null;

  const openCases = (cases.data ?? []).filter((c) => c.status !== "CLOSED");
  const tabs: Tab[] = [
    { id: "overview", label: "Översikt" },
    { id: "compare", label: "Jämförelse & rapport" },
    { id: "cases", label: "Ärenden", badge: openCases.filter((c) => c.severity === "HIGH").length },
    { id: "statements", label: "Resultat & balans" },
    { id: "costs", label: "Kostnader" },
    { id: "transactions", label: "Transaktioner" },
    { id: "questions", label: "Kundfrågor" },
    { id: "analyst", label: "AI-analytiker" },
    { id: "reports", label: "Rapporter" },
    { id: "data", label: "Data" },
    ...(me.permissions.aml ? [{ id: "aml", label: "PTL" }] : []),
    { id: "settings", label: "Inställningar" },
  ];
  const needsPeriod = !["data", "settings", "questions", "aml"].includes(tab);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Link href="/" className="text-[12px] text-muted hover:underline">← Portfölj</Link>
          <h1 className="text-xl font-semibold">{company.data.name}</h1>
          <div className="flex items-center gap-2 text-[12px] text-muted">
            <span>{company.data.org_number}</span>
            {company.data.source_system && <span>· {company.data.source_system}</span>}
            {ov && (
              <span className="flex items-center gap-1" title="Periodmognad för vald månad, bedömd från bokföringen">
                · mognad <StatusBadge status={ov.maturity.status} />
              </span>
            )}
          </div>
        </div>
        {ctx && (
          <div className="flex items-center gap-2">
            <select
              aria-label="Period"
              className={cx(inputCls, "w-36")}
              value={effMonth ?? ""}
              onChange={(e) => {
                setMonth(e.target.value);
                const p = new URLSearchParams(search.toString());
                p.set("period", e.target.value);
                router.replace(`/clients/${id}?${p.toString()}`, { scroll: false });
              }}
            >
              {months.map((m) => (
                <option key={m} value={m}>{monthLabel(m)}</option>
              ))}
            </select>
            <div className="flex shrink-0 overflow-hidden rounded-md border border-line bg-white text-[12px]" role="group" aria-label="Vy">
              {(["month", "YTD", "R12"] as View[]).map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={cx("focus-ring whitespace-nowrap px-2.5 py-1.5", effView === v ? "bg-brand text-white" : "text-muted hover:bg-canvas")}
                  title={v === "month" ? "Enskild månad" : v === "YTD" ? "Hittills i år" : "Rullande 12 månader"}
                >
                  {v === "month" ? "Månad" : v === "YTD" ? "Hittills i år" : "R12"}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {ov && ov.recommended_view !== "month" && effView === "month" && (
        <div className="rounded-md border border-medium/30 bg-medium-soft px-3 py-2 text-[13px] text-medium">
          Bolaget periodiserar lite under året – enskilda månader kan ge en missvisande bild. Hittills i år eller R12 rekommenderas.
        </div>
      )}

      <Tabs tabs={tabs} active={tab} onChange={setTab} />

      {needsPeriod && !ctx ? (
        noData ? (
          <div className="rounded-md border border-dashed border-line bg-white p-6 text-center">
            <p className="mb-2 font-medium">Ingen bokföring ännu</p>
            <p className="text-muted">Ladda upp en SIE4-fil eller koppla bokföringssystemet under fliken Data.</p>
          </div>
        ) : (
          <Loading />
        )
      ) : (
        <ClientContext.Provider value={ctx}>
          {tab === "overview" && ctx && <OverviewTab />}
          {tab === "compare" && ctx && <ComparisonTab />}
          {tab === "cases" && ctx && <CasesTab onChanged={() => cases.reload()} />}
          {tab === "statements" && ctx && <StatementsTab />}
          {tab === "costs" && ctx && <CostsTab />}
          {tab === "transactions" && ctx && <TransactionsTab />}
          {tab === "analyst" && ctx && <AnalystTab />}
          {tab === "reports" && ctx && <ReportsTab />}
          {tab === "questions" && <QuestionsTab base={base} />}
          {tab === "data" && <DataTab base={base} company={company.data} onImported={() => setNonce((n) => n + 1)} />}
          {tab === "aml" && me.permissions.aml && <AmlTab base={base} />}
          {tab === "settings" && <SettingsTab base={base} company={company.data} onSaved={() => setNonce((n) => n + 1)} />}
        </ClientContext.Provider>
      )}
      <VoucherModal base={base} voucherKey={voucher?.key ?? null} hint={voucher?.hint} onClose={() => setVoucher(null)} />
    </div>
  );
}
