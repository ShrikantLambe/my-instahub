"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { apiFetch, type Profile } from "@/lib/api";
import { useAuth } from "../AuthProvider";

export default function OnboardingPage() {
  const router = useRouter();
  const { ready, session, profile, refreshProfile } = useAuth();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    if (!session) router.replace("/login");
    else if (profile) router.replace("/");
  }, [ready, session, profile, router]);

  if (!ready || !session || profile) return null;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiFetch<Profile>("/me", session!.access_token, {
        method: "PUT",
        body: JSON.stringify({ username, display_name: displayName || null }),
      });
      await refreshProfile();
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setBusy(false);
    }
  }

  return (
    <form className="form" onSubmit={onSubmit}>
      <h1>Choose a username</h1>
      <label>
        Username
        <input
          required
          pattern="[A-Za-z0-9_.]{3,30}"
          title="3-30 letters, numbers, dots or underscores"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
      </label>
      <label>
        Display name (optional)
        <input maxLength={50} value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </label>
      {error && <p className="error">{error}</p>}
      <button className="button" disabled={busy}>
        {busy ? "Saving…" : "Continue"}
      </button>
    </form>
  );
}
