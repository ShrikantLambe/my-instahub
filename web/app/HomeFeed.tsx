"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, type FeedPage, type Post } from "@/lib/api";
import { useAuth } from "./AuthProvider";
import { Feed } from "./Feed";
import { PostCard } from "./PostCard";

const PAGE_SIZE = 10;

/** Home: your following feed when signed in, otherwise the latest posts from everyone. */
export function HomeFeed({ explore }: { explore: Post[] }) {
  const { ready, session, profile } = useAuth();
  if (!ready) return <p className="empty">Loading…</p>;
  if (!session || !profile) {
    return (
      <>
        <p className="banner">
          Latest posts from everyone.{" "}
          {session ? (
            <Link href="/onboarding">Choose a username</Link>
          ) : (
            <Link href="/login">Log in</Link>
          )}{" "}
          to see posts from people you follow.
        </p>
        <Feed initialPosts={explore} path="/posts" />
      </>
    );
  }
  return <FollowingFeed token={session.access_token} />;
}

function FollowingFeed({ token }: { token: string }) {
  const [posts, setPosts] = useState<Post[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "more" | "end" | "error">("loading");
  const busy = useRef(false);
  // Supabase refreshes the token about hourly; read the latest without restarting the feed.
  const tokenRef = useRef(token);
  tokenRef.current = token;

  const load = useCallback(async (before: string | null) => {
    if (busy.current) return;
    busy.current = true;
    setStatus("loading");
    try {
      const qs = `limit=${PAGE_SIZE}` + (before ? `&before=${encodeURIComponent(before)}` : "");
      const page = await apiFetch<FeedPage>(`/feed?${qs}`, tokenRef.current);
      setPosts((prev) => {
        if (!before) return page.posts;
        const seen = new Set(prev.map((p) => p.id));
        return [...prev, ...page.posts.filter((p) => !seen.has(p.id))];
      });
      setCursor(page.next_cursor);
      setStatus(page.next_cursor ? "more" : "end");
    } catch {
      setStatus("error");
    } finally {
      busy.current = false;
    }
  }, []);

  useEffect(() => {
    void load(null);
  }, [load]);

  // Infinite scroll: fetch the next page as the bottom of the list comes into view.
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el || status !== "more") return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) void load(cursor);
      },
      { rootMargin: "800px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [status, cursor, load]);

  if (status === "end" && posts.length === 0) {
    return (
      <p className="empty">
        Your feed is empty. Find people to follow on <Link href="/explore">Explore</Link>.
      </p>
    );
  }

  return (
    <section className="feed">
      {posts.map((p) => (
        <PostCard key={p.id} post={p} />
      ))}
      <div ref={sentinel} className="feed-end">
        {status === "loading" && <span className="muted">Loading…</span>}
        {status === "more" && (
          <button type="button" className="link" onClick={() => void load(cursor)}>
            Load more
          </button>
        )}
        {status === "error" && (
          <button type="button" className="link" onClick={() => void load(cursor)}>
            Couldn&apos;t load posts. Try again
          </button>
        )}
        {status === "end" && <span className="muted">You&apos;re all caught up.</span>}
      </div>
    </section>
  );
}
