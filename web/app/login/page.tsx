"use client";

import { FormEvent, Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { API_BASE } from "@/lib/api";

function OperatorLogin() {
  const params = useSearchParams();
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setLoading(true); setError("");
    try {
      const response = await fetch(`${API_BASE}/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ access_code: code }) });
      if (!response.ok) throw new Error("Unable to sign in. Check your operator access code.");
      const next = params.get("next") || "/response-planner";
      window.location.assign(next.startsWith("/") && !next.startsWith("//") ? next : "/response-planner");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Sign-in failed"); }
    finally { setLoading(false); }
  }
  return <main className="page stack-lg"><h1>Operator sign-in</h1><p>Sign in to create and save response plans.</p><form className="planner-form stack-md" onSubmit={submit}><label className="field">Operator access code<input required type="password" autoComplete="current-password" value={code} onChange={(e) => setCode(e.target.value)} /></label><button disabled={loading} className="button button-primary">{loading ? "Signing in…" : "Sign in"}</button>{error && <p className="error-banner">{error}</p>}</form></main>;
}

export default function LoginPage() {
  return <Suspense fallback={<p>Loading sign-in…</p>}><OperatorLogin /></Suspense>;
}
