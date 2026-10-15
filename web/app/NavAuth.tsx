"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "./AuthProvider";

export function NavAuth() {
  const { ready, session, profile, signOut } = useAuth();
  const router = useRouter();

  if (!ready) return null;
  if (!session) return <Link href="/login">Log in</Link>;
  if (!profile) return <Link href="/onboarding">Choose a username</Link>;

  return (
    <div className="nav-links">
      <Link href="/new" className="button">New post</Link>
      <Link href={`/u/${profile.username}`}>@{profile.username}</Link>
      <button
        className="link"
        onClick={async () => {
          await signOut();
          router.push("/");
          router.refresh();
        }}
      >
        Log out
      </button>
    </div>
  );
}
