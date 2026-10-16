"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, type Notification, type NotificationPage } from "@/lib/api";
import { timeAgo } from "@/lib/time";
import { useAuth } from "../AuthProvider";
import { Avatar } from "../Avatar";
import { useNotifications } from "../NotificationsProvider";

/** Activity: who liked, commented on or followed you. Opening it marks everything read. */
export default function NotificationsPage() {
  const router = useRouter();
  const { ready, session, profile } = useAuth();
  const { events, markAllRead } = useNotifications();
  const [items, setItems] = useState<Notification[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "idle" | "error">("loading");
  const tokenRef = useRef(session?.access_token);
  tokenRef.current = session?.access_token;
  const signedIn = Boolean(session && profile);

  useEffect(() => {
    if (!ready) return;
    if (!session) router.replace("/login");
    else if (!profile) router.replace("/onboarding");
  }, [ready, session, profile, router]);

  const load = useCallback(async (before: string | null) => {
    if (!tokenRef.current) return;
    setStatus("loading");
    try {
      const qs = before ? `?before=${encodeURIComponent(before)}` : "";
      const page = await apiFetch<NotificationPage>(`/notifications${qs}`, tokenRef.current);
      setItems((cur) => {
        const seen = new Set(cur.map((n) => n.id));
        return [...cur, ...page.notifications.filter((n) => !seen.has(n.id))];
      });
      setCursor(page.next_cursor);
      setStatus("idle");
    } catch {
      setStatus("error");
    }
  }, []);

  // First page, then mark it all read (the list keeps showing which ones were new).
  useEffect(() => {
    if (!signedIn) return;
    void load(null).then(markAllRead);
  }, [signedIn, load, markAllRead]);

  // New activity while the page is open: show it on top, already read.
  useEffect(() => {
    const onNew = (e: Event) => {
      const n = (e as CustomEvent<Notification>).detail;
      setItems((cur) => (cur.some((x) => x.id === n.id) ? cur : [{ ...n, read: false }, ...cur]));
      void markAllRead();
    };
    events.addEventListener("notification", onNew);
    return () => events.removeEventListener("notification", onNew);
  }, [events, markAllRead]);

  if (!ready || !signedIn) return null;

  return (
    <section className="activity">
      <h1>Notifications</h1>
      {status !== "loading" && items.length === 0 && (
        <p className="empty">Nothing yet. When people like or comment on your posts, or follow you, it shows up here.</p>
      )}
      <ul className="activity-list">
        {items.map((n) => (
          <li key={n.id} className={n.read ? undefined : "unread"}>
            <Link href={`/u/${n.actor.username}`} className="activity-avatar">
              <Avatar user={n.actor} size={40} />
            </Link>
            <p>
              <Link href={`/u/${n.actor.username}`} className="activity-actor">
                {n.actor.username}
              </Link>{" "}
              {describe(n)}{" "}
              <time dateTime={n.created_at} className="muted" suppressHydrationWarning>
                {timeAgo(n.created_at)}
              </time>
            </p>
            {n.post && (
              <Link href={`/p/${n.post.id}`} className="activity-thumb" aria-label="Open the post">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={n.post.image_url} alt="" loading="lazy" />
              </Link>
            )}
          </li>
        ))}
      </ul>
      <div className="feed-end">
        {status === "loading" && <span className="muted">Loading…</span>}
        {status === "error" && (
          <button type="button" className="link" onClick={() => void load(cursor)}>
            Couldn&apos;t load notifications. Try again
          </button>
        )}
        {status === "idle" && cursor && (
          <button type="button" className="link" onClick={() => void load(cursor)}>
            Load more
          </button>
        )}
      </div>
    </section>
  );
}

function describe(n: Notification): string {
  switch (n.kind) {
    case "like":
      return "liked your post.";
    case "comment":
      return `commented: “${truncate(n.comment?.body ?? "", 80)}”`;
    case "follow":
      return "started following you.";
  }
}

const truncate = (s: string, max: number) => (s.length > max ? `${s.slice(0, max - 1)}…` : s);
