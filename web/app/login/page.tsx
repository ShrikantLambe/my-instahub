"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { supabase, supabaseConfigured } from "@/lib/supabase";
import { useAuth } from "../AuthProvider";

export default function LoginPage() {
  const router = useRouter();
  const { refreshProfile } = useAuth();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (!supabaseConfigured) {
    return (
      <p className="empty">
        Login isn&apos;t configured. Set <code>NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
        <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code>.
      </p>
    );
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    const auth = supabase().auth;
    const { data, error } =
      mode === "login"
        ? await auth.signInWithPassword({ email, password })
        : await auth.signUp({ email, password });
    if (error) {
      setError(error.message);
      setBusy(false);
      return;
    }
    if (!data.session) {
      // Supabase's "Confirm email" setting is on: no session until the link is clicked.
      setNotice("Check your email for a confirmation link, then log in.");
      setMode("login");
      setBusy(false);
      return;
    }
    try {
      const profile = await refreshProfile();
      router.push(profile ? "/" : "/onboarding");
    } catch {
      // Signed in, but the API is unreachable (e.g. still waking up): finish setup later.
      router.push("/onboarding");
    }
  }

  return (
    <form className="form" onSubmit={onSubmit}>
      <h1>{mode === "login" ? "Log in" : "Create an account"}</h1>
      <label>
        Email
        <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <label>
        Password
        <input
          type="password"
          required
          minLength={6}
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {error && <p className="error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}
      <button className="button" disabled={busy}>
        {busy ? "Please wait…" : mode === "login" ? "Log in" : "Sign up"}
      </button>
      <button type="button" className="link" onClick={() => setMode(mode === "login" ? "signup" : "login")}>
        {mode === "login" ? "New here? Create an account" : "Have an account? Log in"}
      </button>
    </form>
  );
}
