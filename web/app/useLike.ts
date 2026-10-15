"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, type LikeResult, type Post } from "@/lib/api";
import { useAuth } from "./AuthProvider";

/**
 * Optimistic like state for one post.
 *
 * The heart flips the moment you tap. Requests for a post go out one at a time and the
 * latest intent wins: tap like-unlike-like quickly and only the final state is synced,
 * so out-of-order responses can't leave the button lying. If a request fails, the UI
 * rolls back to what the server last confirmed. The server is idempotent anyway, so a
 * retried or duplicated request can never count twice.
 */
export function useLike(post: Post) {
  const router = useRouter();
  const { session, profile } = useAuth();
  const [liked, setLiked] = useState(post.viewer_has_liked);
  const [count, setCount] = useState(post.like_count);
  const server = useRef({ liked: post.viewer_has_liked, count: post.like_count });
  const desired = useRef(post.viewer_has_liked);
  const inFlight = useRef(false);

  // Fresh data from the server (e.g. the feed re-fetched with the viewer's token).
  useEffect(() => {
    if (inFlight.current) return;
    server.current = { liked: post.viewer_has_liked, count: post.like_count };
    desired.current = post.viewer_has_liked;
    setLiked(post.viewer_has_liked);
    setCount(post.like_count);
  }, [post.viewer_has_liked, post.like_count]);

  const token = session?.access_token;

  const sync = useCallback(async () => {
    if (inFlight.current || !token) return;
    const want = desired.current;
    if (want === server.current.liked) return;
    inFlight.current = true;
    try {
      const res = await apiFetch<LikeResult>(`/posts/${post.id}/like`, token, {
        method: want ? "POST" : "DELETE",
      });
      server.current = { liked: res.liked, count: res.like_count };
      if (desired.current === res.liked) {
        setLiked(res.liked);
        setCount(res.like_count); // the server's count, which includes other people's likes
      }
    } catch {
      desired.current = server.current.liked;
      setLiked(server.current.liked);
      setCount(server.current.count);
    } finally {
      inFlight.current = false;
    }
    if (desired.current !== server.current.liked) void sync();
  }, [post.id, token]);

  const setWant = useCallback(
    (want: boolean) => {
      if (!session) return router.push("/login");
      if (!profile) return router.push("/onboarding");
      if (want === desired.current) return;
      desired.current = want;
      setLiked(want);
      setCount((c) => Math.max(0, c + (want ? 1 : -1)));
      void sync();
    },
    [session, profile, router, sync],
  );

  return {
    liked,
    count,
    toggle: () => setWant(!desired.current),
    like: () => setWant(true), // double-tap on the photo only ever likes, like Instagram
  };
}
