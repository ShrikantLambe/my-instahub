"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  apiFetch,
  getComments,
  type Comment,
  type CommentChange,
  type CommentPage,
  type Post,
} from "@/lib/api";
import { useAuth } from "./AuthProvider";
import type { LiveCount } from "./useLiveCounts";

type Shown = Comment & { pending?: boolean }; // pending: posted, not confirmed yet

const MAX_LENGTH = 2200;

/**
 * A post's comments, newest first, with a box to add one.
 *
 * Your own comment shows the moment you send it and is swapped for the server's copy
 * when the API confirms it (or removed again, with your text back in the box, if it
 * fails). Other people's comments and deletions arrive over the live stream. The count
 * is versioned like likes, so a late or repeated update can't move it backwards.
 */
export function Comments({
  post,
  initial,
  events,
  live,
}: {
  post: Post;
  initial: CommentPage | null;
  events: EventTarget; // "change" events carrying a CommentChange from the stream
  live?: LiveCount;
}) {
  const { session, profile } = useAuth();
  const [list, setList] = useState<Shown[]>(initial?.comments ?? []);
  const [cursor, setCursor] = useState(initial?.next_cursor ?? null);
  const [server, setServer] = useState<LiveCount>({
    count: initial?.comment_count ?? post.comment_count,
    version: initial?.comment_version ?? post.comment_version,
  });
  const [hidden, setHidden] = useState<Set<number>>(new Set()); // deletes in flight
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [loadFailed, setLoadFailed] = useState(initial === null);

  const applyCount = (count: number, version: number) =>
    setServer((cur) => (version > cur.version ? { count, version } : cur));

  useEffect(() => {
    if (live) applyCount(live.count, live.version);
  }, [live?.count, live?.version]); // eslint-disable-line react-hooks/exhaustive-deps

  // Server render failed (API asleep?): try once more from the browser.
  useEffect(() => {
    if (initial !== null) return;
    getComments(post.id)
      .then((page) => {
        if (!page) return;
        setList((cur) => addMissing(cur, page.comments));
        setCursor(page.next_cursor);
        applyCount(page.comment_count, page.comment_version);
        setLoadFailed(false);
      })
      .catch(() => {});
  }, [initial, post.id]);

  // Live changes from other viewers (and the echo of our own).
  useEffect(() => {
    const onChange = (e: Event) => {
      const change = (e as CustomEvent<CommentChange>).detail;
      if (change.post_id !== post.id) return;
      if (change.comment) {
        const real = change.comment;
        setList((cur) => {
          if (cur.some((c) => c.id === real.id)) return cur;
          // Our own comment can arrive here before the API answers us: swap it in.
          const mine = cur.findIndex(
            (c) => c.pending && c.author.id === real.author.id && c.body === real.body,
          );
          if (mine >= 0) return cur.map((c, i) => (i === mine ? real : c));
          return [real, ...cur];
        });
      }
      if (change.deleted_id) {
        const gone = change.deleted_id;
        setList((cur) => cur.filter((c) => c.id !== gone));
      }
    };
    events.addEventListener("change", onChange);
    return () => events.removeEventListener("change", onChange);
  }, [events, post.id]);

  const pendingAdds = list.filter((c) => c.pending).length;
  const shownCount = Math.max(0, server.count + pendingAdds - hidden.size);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const body = text.trim();
    if (!body || !session || !profile) return;
    setError(null);
    setText("");
    const temp: Shown = {
      id: -Date.now(),
      post_id: post.id,
      author: profile,
      body,
      created_at: new Date().toISOString(),
      pending: true,
    };
    setList((cur) => [temp, ...cur]);
    try {
      const res = await apiFetch<CommentChange>(`/posts/${post.id}/comments`, session.access_token, {
        method: "POST",
        body: JSON.stringify({ body }),
      });
      const real = res.comment!;
      setList((cur) => {
        const rest = cur.filter((c) => c.id !== temp.id);
        return rest.some((c) => c.id === real.id) ? rest : [real, ...rest];
      });
      applyCount(res.comment_count, res.comment_version);
    } catch (err) {
      setList((cur) => cur.filter((c) => c.id !== temp.id));
      setText((cur) => cur || body); // give the text back unless they started a new one
      setError(err instanceof ApiError ? err.message : "Couldn't post your comment. Try again.");
    }
  }

  async function remove(c: Comment) {
    if (!session) return;
    setError(null);
    setHidden((cur) => new Set(cur).add(c.id));
    try {
      const res = await apiFetch<CommentChange>(`/comments/${c.id}`, session.access_token, {
        method: "DELETE",
      });
      applyCount(res.comment_count, res.comment_version);
      setList((cur) => cur.filter((x) => x.id !== c.id));
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setList((cur) => cur.filter((x) => x.id !== c.id)); // already gone
      } else {
        setError(err instanceof ApiError ? err.message : "Couldn't delete the comment. Try again.");
      }
    } finally {
      setHidden((cur) => {
        const next = new Set(cur);
        next.delete(c.id);
        return next;
      });
    }
  }

  async function loadOlder() {
    if (!cursor || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const page = await getComments(post.id, cursor);
      if (page) {
        setList((cur) => addMissing(cur, page.comments));
        setCursor(page.next_cursor);
        applyCount(page.comment_count, page.comment_version);
      }
    } catch {
      setError("Couldn't load older comments. Try again.");
    } finally {
      setLoadingOlder(false);
    }
  }

  const canDelete = (c: Shown) =>
    !c.pending && !!profile && (profile.id === c.author.id || profile.id === post.author.id);

  return (
    <section className="comments" aria-label="Comments">
      <h2>
        {shownCount} {shownCount === 1 ? "comment" : "comments"}
      </h2>

      {session && profile ? (
        <form className="comment-form" onSubmit={submit}>
          <input
            aria-label="Add a comment"
            placeholder="Add a comment…"
            value={text}
            maxLength={MAX_LENGTH}
            onChange={(e) => setText(e.target.value)}
          />
          <button type="submit" className="link" disabled={!text.trim()}>
            Post
          </button>
        </form>
      ) : (
        <p className="muted">
          {session ? (
            <Link href="/onboarding">Choose a username</Link>
          ) : (
            <Link href="/login">Log in</Link>
          )}{" "}
          to comment.
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <ul className="comment-list">
        {list
          .filter((c) => !hidden.has(c.id))
          .map((c) => (
            <li key={c.id} className={c.pending ? "pending" : undefined}>
              <p>
                <Link href={`/u/${c.author.username}`} className="comment-author">
                  {c.author.username}
                </Link>{" "}
                <span className="comment-body">{c.body}</span>
              </p>
              <p className="comment-meta">
                {c.pending ? (
                  "Posting…"
                ) : (
                  <time dateTime={c.created_at} suppressHydrationWarning>
                    {timeAgo(c.created_at)}
                  </time>
                )}
                {canDelete(c) && (
                  <button type="button" className="link" onClick={() => void remove(c)}>
                    Delete
                  </button>
                )}
              </p>
            </li>
          ))}
      </ul>

      {cursor && (
        <button type="button" className="link" onClick={() => void loadOlder()} disabled={loadingOlder}>
          {loadingOlder ? "Loading…" : "View older comments"}
        </button>
      )}
      {loadFailed && list.length === 0 && server.count > 0 && (
        <p className="muted">Couldn&apos;t load comments.</p>
      )}
    </section>
  );
}

// Append an older page, skipping comments we already show (e.g. ones that arrived live).
function addMissing(cur: Shown[], older: Comment[]): Shown[] {
  const seen = new Set(cur.map((c) => c.id));
  return [...cur, ...older.filter((c) => !seen.has(c.id))];
}

// Instagram-style short ages: "now", "5m", "3h", "2d", then a date.
function timeAgo(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d`;
  return new Date(iso).toLocaleDateString();
}
