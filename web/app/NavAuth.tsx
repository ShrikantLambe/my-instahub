"use client";

import Link from "next/link";
import { useAuth } from "./AuthProvider";
import { Avatar } from "./Avatar";
import { useNotifications } from "./NotificationsProvider";

export function NavAuth() {
  const { ready, session, profile } = useAuth();
  const { unread } = useNotifications();

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
      <Link
        href="/notifications"
        className="nav-icon"
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
      >
        <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
          <path
            d="M12 21s-7.5-4.6-9.6-9.3C.9 8.3 3 4.5 6.7 4.5c2.1 0 3.6 1.2 4.3 2.6h2c.7-1.4 2.2-2.6 4.3-2.6 3.7 0 5.8 3.8 4.3 7.2C19.5 16.4 12 21 12 21z"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinejoin="round"
          />
        </svg>
        {unread > 0 && <span className="badge">{unread > 99 ? "99+" : unread}</span>}
      </Link>
      <Link href={`/u/${profile.username}`} className="nav-profile" aria-label={`Your profile (@${profile.username})`}>
        <Avatar user={profile} size={30} />
      </Link>
    </div>
  );
}
