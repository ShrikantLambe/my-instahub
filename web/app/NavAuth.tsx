"use client";

import Link from "next/link";
import { useAuth } from "./AuthProvider";
import { Avatar } from "./Avatar";

export function NavAuth() {
  const { ready, session, profile } = useAuth();

  const explore = <Link href="/explore">Explore</Link>;
  if (!ready) return null;
  if (!session || !profile) {
    return (
      <div className="nav-links">
        {explore}
        {session ? <Link href="/onboarding">Choose a username</Link> : <Link href="/login">Log in</Link>}
      </div>
    );
  }

  return (
    <div className="nav-links">
      {explore}
      <Link href="/new" className="button">New post</Link>
      <Link href={`/u/${profile.username}`} className="nav-profile" aria-label={`Your profile (@${profile.username})`}>
        <Avatar user={profile} size={30} />
      </Link>
    </div>
  );
}
