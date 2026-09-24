"use client";

import { useState } from "react";
import { Button, Card, Field, inputCls } from "@/components/ui";

// Utvecklingsinloggning. I produktion används OIDC (t.ex. Microsoft Entra) – se docs/DRIFT.md.
export default function LoginPage() {
  const [email, setEmail] = useState("anna@demobyran.se");
  const login = (e?: string) => {
    const v = (e ?? email).trim();
    document.cookie = `rai_dev_user=${encodeURIComponent(v)}; path=/; samesite=strict`;
    window.location.href = "/";
  };
  return (
    <main className="mx-auto mt-24 max-w-md">
      <Card title="Logga in (utvecklingsläge)">
        <form
          className="space-y-4"
          onSubmit={(ev) => {
            ev.preventDefault();
            login();
          }}
        >
          <Field label="E-post" hint="I produktion loggar du in med byråns identitetsleverantör (OIDC/Entra).">
            <input className={inputCls} value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Button type="submit">Logga in</Button>
        </form>
        <div className="mt-6 space-y-1 text-[12px] text-muted">
          <p>Demobyråns användare:</p>
          <button className="block text-brand hover:underline" onClick={() => login("anna@demobyran.se")}>anna@demobyran.se – byråadmin (lönedata, PTL)</button>
          <button className="block text-brand hover:underline" onClick={() => login("lisa@demobyran.se")}>lisa@demobyran.se – läsare</button>
        </div>
      </Card>
    </main>
  );
}
