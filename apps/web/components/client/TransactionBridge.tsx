"use client";

import { useState } from "react";
import { ErrorBox, Loading } from "@/components/ui";
import { type BridgeGroup, type BridgePart, type BridgePartCode, type TransactionBridge, useLoad } from "@/lib/api";
import { pct, sek } from "@/lib/format";
import { VoucherLink } from "./shared";

type Mode = "yoy" | "previous";

const PART_LABELS: Record<BridgePartCode, string> = {
  both: "Motpart i båda perioderna",
  current_only: "Bara i aktuell jämförelseperiod",
  previous_only: "Bara i den tidigare perioden",
  unknown: "Okänd motpart",
};

const SIGNAL_BADGES: Record<string, string> = {
  periodization: "Möjlig periodisering",
  reversal: "Återföring eller rättelse",
  large_booking: "Stor enskild bokning",
};

const SOURCE_LABELS: Record<string, string> = {
  alias: "alias",
  row_text: "radtext",
  voucher_text: "verifikationstext",
  unknown: "oidentifierat",
};

/** Knapp som utan förhandsladdning expanderar transaktionsbryggan för ett mål. */
export function TransactionBridgeToggle({
  base,
  target,
  period,
  mode,
}: {
  base: string;
  target: string;
  period: string;
  mode: Mode;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="mt-2">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="focus-ring text-[12px] text-brand hover:underline"
      >
        {expanded ? "Dölj transaktionsbrygga" : "Visa transaktionsbrygga"}
      </button>
      {expanded && <TransactionBridgePanel base={base} target={target} period={period} mode={mode} />}
    </div>
  );
}

function TransactionBridgePanel({ base, target, period, mode }: { base: string; target: string; period: string; mode: Mode }) {
  const path = `${base}/transaction-bridge?target=${encodeURIComponent(target)}&period=${encodeURIComponent(period)}&mode=${mode}`;
  const { data, error, loading } = useLoad<TransactionBridge>(path);
  return (
    <div className="mt-2 rounded border border-line bg-canvas p-3">
      <ErrorBox error={error} />
      {loading && !data && <Loading />}
      {data && <BridgeTable bridge={data} />}
    </div>
  );
}

function BridgeTable({ bridge }: { bridge: TransactionBridge }) {
  const both = bridge.parts.find((p) => p.code === "both");
  const topGroups = bridge.groups.slice(0, 5);
  return (
    <div className="space-y-3 text-[12px]">
      <h4 className="font-semibold">Transaktionsbrygga</h4>
      <div className="overflow-x-auto">
        <table className="data min-w-[560px]">
          <thead>
            <tr>
              <th>Del</th>
              <th className="num">Belopp, aktuell</th>
              <th className="num">Antal, aktuell</th>
              <th className="num">Belopp, jämförelse</th>
              <th className="num">Antal, jämförelse</th>
              <th className="num">Effekt</th>
            </tr>
          </thead>
          <tbody>
            {bridge.parts.map((part) => (
              <PartRows key={part.code} part={part} />
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th>Förändring</th>
              <td colSpan={4} />
              <td className="num">{sek(bridge.change, { signed: true })}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      {both && (both.count_effect !== null || both.amount_effect !== null) && (
        <p className="text-muted">
          Varav fler eller färre verifikationer: {sek(both.count_effect, { signed: true })} · varav ändrat belopp per
          verifikation: {sek(both.amount_effect, { signed: true })}.
        </p>
      )}
      <p>
        Identifieringsgrad:{" "}
        {(["alias", "row_text", "voucher_text", "unknown"] as const)
          .map((key) => `${SOURCE_LABELS[key]} ${pct(Number(bridge.identified_share_abs[key]) * 100, false)}`)
          .join(" · ")}
      </p>
      {Object.keys(bridge.signals).length > 0 && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(bridge.signals).map(([signal, amount]) => (
            <span key={signal} className="rounded-full bg-medium-soft px-2 py-0.5 text-medium">
              {SIGNAL_BADGES[signal] ?? signal}: {sek(amount, { signed: true })}
            </span>
          ))}
        </div>
      )}
      {bridge.masked ? (
        <p className="text-muted">Motparter på lönekonton visas bara med behörigheten Lönedata.</p>
      ) : (
        topGroups.length > 0 && (
          <div className="space-y-2">
            <h5 className="font-semibold">Största motparter</h5>
            <ul className="space-y-1">
              {topGroups.map((group) => (
                <GroupRow key={group.key || "unknown"} group={group} />
              ))}
            </ul>
          </div>
        )
      )}
    </div>
  );
}

function PartRows({ part }: { part: BridgePart }) {
  return (
    <>
      <tr>
        <td>{PART_LABELS[part.code]}</td>
        <td className="num">{sek(part.current)}</td>
        <td className="num">{part.current_count}</td>
        <td className="num">{sek(part.previous)}</td>
        <td className="num">{part.previous_count}</td>
        <td className="num">{sek(part.effect, { signed: true })}</td>
      </tr>
      {part.code === "both" && (
        <>
          <tr>
            <td className="pl-4 text-muted">varav fler eller färre verifikationer</td>
            <td className="num" colSpan={4} />
            <td className="num text-muted">{sek(part.count_effect, { signed: true })}</td>
          </tr>
          <tr>
            <td className="pl-4 text-muted">varav ändrat belopp per verifikation</td>
            <td className="num" colSpan={4} />
            <td className="num text-muted">{sek(part.amount_effect, { signed: true })}</td>
          </tr>
        </>
      )}
    </>
  );
}

function GroupRow({ group }: { group: BridgeGroup }) {
  return (
    <li className="rounded bg-white px-2 py-1">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium">{group.name ?? "Okänd motpart"}</span>
        <span className="tabular-nums">
          {sek(group.current, { signed: false })} mot {sek(group.previous, { signed: false })}
        </span>
      </div>
      {group.signals.length > 0 && (
        <div className="mt-0.5 flex flex-wrap gap-1">
          {group.signals.map((signal) => (
            <span key={signal} className="rounded-full bg-medium-soft px-2 py-0.5 text-[11px] text-medium">
              {SIGNAL_BADGES[signal] ?? signal}
            </span>
          ))}
        </div>
      )}
      <div className="mt-0.5 flex flex-wrap gap-2 text-[11px] text-muted">
        {group.current_vouchers.map(([key, voucherDate]) => (
          <VoucherLink key={`current-${key}-${voucherDate}`} v={key} hint={voucherDate} />
        ))}
        {group.previous_vouchers.map(([key, voucherDate]) => (
          <VoucherLink key={`previous-${key}-${voucherDate}`} v={key} hint={voucherDate} />
        ))}
      </div>
    </li>
  );
}
