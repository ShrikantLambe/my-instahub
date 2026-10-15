import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | null = null;

export const supabaseConfigured = Boolean(
  process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
);

// Browser-side Supabase client: handles sign-up, login and keeping the session fresh.
// The API never sees passwords, only the access token Supabase issues.
export function supabase(): SupabaseClient {
  if (!client) {
    if (!supabaseConfigured) {
      throw new Error("Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY");
    }
    // Only the origin: a URL pasted with "/rest/v1" makes Supabase reject auth calls
    // with "Invalid path specified in request URL".
    const url = new URL(process.env.NEXT_PUBLIC_SUPABASE_URL!.trim()).origin;
    client = createClient(url, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!.trim());
  }
  return client;
}
