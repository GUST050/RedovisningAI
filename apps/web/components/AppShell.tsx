"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, createContext, useContext, useEffect, useState } from "react";
import { ApiError, get, type Me } from "@/lib/api";
import { cx } from "./ui";

const MeContext = createContext<Me | null>(null);
export const useMe = () => useContext(MeContext);

export function AppShell({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [noFirm, setNoFirm] = useState(false);
  const path = usePathname();
  useEffect(() => {
    get<Me>("/api/me")
      .then(setMe)
      .catch((e) => setNoFirm(e instanceof ApiError && e.status === 403));
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
      <main className="mx-auto max-w-[1400px] px-5 py-5">{noFirm ? <NoFirm /> : children}</main>
    </MeContext.Provider>
  );
}

/** Inloggad användare som inte hör till någon byrå – vanligast när demodata inte lagts in. */
function NoFirm() {
  const current = typeof document === "undefined" ? "" : decodeURIComponent(/rai_dev_user=([^;]+)/.exec(document.cookie)?.[1] ?? "");
  const loginAs = (email: string) => {
    document.cookie = `rai_dev_user=${encodeURIComponent(email)}; path=/; samesite=strict`;
    window.location.href = "/";
  };
  return (
    <div className="mx-auto mt-10 max-w-xl rounded-lg border border-line bg-white p-6">
      <h1 className="mb-2 text-lg font-semibold">Ingen byrå kopplad till {current || "användaren"}</h1>
      <p className="mb-4 text-muted">
        Användaren finns inte i någon byrå. Logga in som en av demobyråns användare – eller, om demobyrån inte är skapad än,
        kör kommandot nedan i terminalen och ladda om sidan.
      </p>
      <pre className="mb-4 overflow-x-auto rounded-md bg-canvas px-3 py-2 text-[12px]">docker compose run --rm api redovisningai seed-demo</pre>
      <div className="flex flex-wrap gap-2">
        <button className="focus-ring rounded-md bg-brand px-3 py-1.5 text-[13px] font-medium text-white" onClick={() => loginAs("anna@demobyran.se")}>
          Logga in som anna@demobyran.se
        </button>
        <Link href="/login" className="rounded-md border border-line px-3 py-1.5 text-[13px]">Byt användare</Link>
      </div>
    </div>
  );
}
