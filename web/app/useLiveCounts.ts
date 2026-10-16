"use client";

import { useEffect, useRef, useState } from "react";
import { API_URL, type CommentChange } from "@/lib/api";

export type LiveCount = { count: number; version: number };
export type LiveStatus = "connecting" | "live" | "offline";

const MAX_WATCHED = 100; // the API's limit per stream

type Counts = Record<number, LiveCount>;

// Keep the newer of two versioned counts; stale or repeated updates change nothing.
function merge(prev: Counts, postId: number, count: number, version: number): Counts {
  const cur = prev[postId];
  if (cur && cur.version >= version) return prev;
  return { ...prev, [postId]: { count, version } };
}

/**
 * Live like and comment counts for a set of posts over Server-Sent Events.
 *
 * The stream starts with a snapshot of every watched post, then pushes each change.
 * EventSource reconnects by itself, and each reconnect starts with a fresh snapshot,
 * so nothing missed while offline is lost. Updates older than what we hold are ignored.
 * onComment also receives each comment added or deleted (for an open comment list).
 */
export function useLiveCounts(postIds: number[], onComment?: (change: CommentChange) => void) {
  const [counts, setCounts] = useState<Counts>({});
  const [commentCounts, setCommentCounts] = useState<Counts>({});
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const commentHandler = useRef(onComment);
  commentHandler.current = onComment;
  const key = [...new Set(postIds)].slice(0, MAX_WATCHED).sort((a, b) => a - b).join(",");

  useEffect(() => {
    if (!key || typeof EventSource === "undefined") return;
    setStatus("connecting");
    const es = new EventSource(`${API_URL}/stream?posts=${key}`);
    es.onopen = () => setStatus("live");
    es.onerror = () => setStatus(es.readyState === EventSource.CLOSED ? "offline" : "connecting");
    es.addEventListener("likes", (e) => {
      const d = JSON.parse((e as MessageEvent).data) as {
        post_id: number;
        like_count: number;
        like_version: number;
      };
      setCounts((prev) => merge(prev, d.post_id, d.like_count, d.like_version));
    });
    es.addEventListener("comments", (e) => {
      const d = JSON.parse((e as MessageEvent).data) as CommentChange;
      setCommentCounts((prev) => merge(prev, d.post_id, d.comment_count, d.comment_version));
      if (d.comment || d.deleted_id) commentHandler.current?.(d);
    });
    return () => es.close();
  }, [key]);

  return { counts, commentCounts, status };
}
