"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, createContext, useContext, useEffect, useState } from "react";
import { get, type Me } from "@/lib/api";
import { cx } from "./ui";

const MeContext = createContext<Me | null>(null);
export const useMe = () => useContext(MeContext);

export function AppShell({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const path = usePathname();
  useEffect(() => {
    get<Me>("/api/me").then(setMe).catch(() => undefined);
  }, []);
  const nav = [
    { href: "/", label: "Portfölj" },
    { href: "/settings", label: "Regler och inställningar" },
  ];
  return (
    <MeContext.Provider value={me}>
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-[1400px] items-center gap-6 px-5 py-3">
          <Link href="/" className="text-[15px] font-bold tracking-tight text-brand">RedovisningAI</Link>
          <nav className="flex gap-1">
            {nav.map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className={cx("rounded-md px-3 py-1.5 text-[13px]", path === n.href ? "bg-brand-soft font-medium text-brand" : "text-muted hover:text-ink")}
              >
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-[12px] text-muted">
            {me && (
              <>
                <span>{me.org.name}</span>
                <span className="rounded bg-canvas px-2 py-0.5">{me.user.name} · {me.role === "ADMIN" ? "Byråadmin" : me.role === "CONSULTANT" ? "Konsult" : "Läsare"}</span>
                {me.permissions.payroll && <span title="Behörighet: Lönedata">💼</span>}
                {me.permissions.aml && <span title="Behörighet: PTL-ansvarig">🛡</span>}
                <Link href="/login" className="text-brand hover:underline">Byt användare</Link>
              </>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1400px] px-5 py-5">{children}</main>
    </MeContext.Provider>
  );
}
