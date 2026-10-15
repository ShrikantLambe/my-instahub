"use client";

import { useRouter } from "next/navigation";
import { apiFetch, type LikeResult, type Post } from "@/lib/api";
import { useAuth } from "./AuthProvider";
import { useSyncedToggle } from "./useSyncedToggle";

// Optimistic like state for one post; see useSyncedToggle for the sync rules.
export function useLike(post: Post) {
  const router = useRouter();
  const { session, profile } = useAuth();
  const { on, count, set, toggle } = useSyncedToggle(
    { on: post.viewer_has_liked, count: post.like_count },
    async (want) => {
      const res = await apiFetch<LikeResult>(`/posts/${post.id}/like`, session!.access_token, {
        method: want ? "POST" : "DELETE",
      });
      return { on: res.liked, count: res.like_count };
    },
  );

  // Signed out or no username yet: send them where they can fix that instead.
  const ready = () => {
    if (!session) router.push("/login");
    else if (!profile) router.push("/onboarding");
    else return true;
    return false;
  };

  return {
    liked: on,
    count,
    toggle: () => ready() && toggle(),
    like: () => ready() && set(true), // double-tap on the photo only ever likes, like Instagram
  };
}
