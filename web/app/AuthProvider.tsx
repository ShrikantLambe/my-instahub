"use client";

import type { Session } from "@supabase/supabase-js";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { ApiError, apiFetch, type Profile } from "@/lib/api";
import { supabase, supabaseConfigured } from "@/lib/supabase";

type AuthState = {
  ready: boolean; // false until the stored session (and profile) have loaded
  session: Session | null;
  profile: Profile | null; // null when signed out or signed in without a username yet
  refreshProfile: () => Promise<Profile | null>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [sessionLoaded, setSessionLoaded] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [profileLoaded, setProfileLoaded] = useState(false);

  useEffect(() => {
    if (!supabaseConfigured) {
      setSessionLoaded(true);
      return;
    }
    const sb = supabase();
    sb.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setSessionLoaded(true);
    });
    const { data } = sb.auth.onAuthStateChange((_event, s) => setSession(s));
    return () => data.subscription.unsubscribe();
  }, []);

  const token = session?.access_token;

  const loadProfile = useCallback(async (): Promise<Profile | null> => {
    if (!token) return null;
    try {
      return await apiFetch<Profile>("/me", token);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null; // no username yet
      throw err;
    }
  }, [token]);

  useEffect(() => {
    if (!sessionLoaded) return;
    let cancelled = false;
    setProfileLoaded(false);
    loadProfile()
      .catch(() => null)
      .then((p) => {
        if (cancelled) return;
        setProfile(p);
        setProfileLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionLoaded, loadProfile]);

  const refreshProfile = useCallback(async () => {
    const p = await loadProfile();
    setProfile(p);
    return p;
  }, [loadProfile]);

  const signOut = useCallback(async () => {
    if (supabaseConfigured) await supabase().auth.signOut();
    setSession(null);
    setProfile(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{ ready: sessionLoaded && profileLoaded, session, profile, refreshProfile, signOut }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
