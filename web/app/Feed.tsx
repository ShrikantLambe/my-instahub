"use client";

import { useEffect, useState } from "react";
import { apiFetch, type Post } from "@/lib/api";
import { useAuth } from "./AuthProvider";
import { PostCard } from "./PostCard";

/**
 * Posts render straight from the server (fast, no login needed). Once we know who is
 * looking, one more request with their token fills in viewer_has_liked for every post
 * on the page; the API answers that with a single batched query.
 */
export function Feed({
  initialPosts,
  path,
  single = false,
}: {
  initialPosts: Post[];
  path: string;
  single?: boolean; // path returns one post rather than a list
}) {
  const { session } = useAuth();
  const [posts, setPosts] = useState(initialPosts);
  const token = session?.access_token;

  useEffect(() => setPosts(initialPosts), [initialPosts]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    apiFetch<Post[] | Post>(path, token)
      .then((fresh) => !cancelled && setPosts(single ? [fresh as Post] : (fresh as Post[])))
      .catch(() => {}); // keep the server-rendered posts; hearts just start unfilled
    return () => {
      cancelled = true;
    };
  }, [token, path, single]);

  return (
    <section className="feed">
      {posts.map((p) => (
        <PostCard key={p.id} post={p} />
      ))}
    </section>
  );
}
