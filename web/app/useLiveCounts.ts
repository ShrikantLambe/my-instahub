"use client";

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";

export type LiveCount = { count: number; version: number };
export type LiveStatus = "connecting" | "live" | "offline";

const MAX_WATCHED = 100; // the API's limit per stream

/**
 * Live like counts for a set of posts over Server-Sent Events.
 *
 * The stream starts with a snapshot of every watched post, then pushes each change.
 * EventSource reconnects by itself, and each reconnect starts with a fresh snapshot,
 * so nothing missed while offline is lost. Updates older than what we hold are ignored.
 */
export function useLiveCounts(postIds: number[]) {
  const [counts, setCounts] = useState<Record<number, LiveCount>>({});
  const [status, setStatus] = useState<LiveStatus>("connecting");
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
      setCounts((prev) => {
        const cur = prev[d.post_id];
        if (cur && cur.version >= d.like_version) return prev;
        return { ...prev, [d.post_id]: { count: d.like_count, version: d.like_version } };
      });
    });
    return () => es.close();
  }, [key]);

  return { counts, status };
}
