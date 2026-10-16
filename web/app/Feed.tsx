"use client";

import { useEffect, useMemo, useState } from "react";
import { apiFetch, type CommentPage, type Post } from "@/lib/api";
import { useAuth } from "./AuthProvider";
import { Comments } from "./Comments";
import { PostCard } from "./PostCard";
import { useLiveCounts } from "./useLiveCounts";

/**
 * Posts render straight from the server (fast, no login needed). Once we know who is
 * looking, one more request with their token fills in viewer_has_liked for every post
 * on the page; the API answers that with a single batched query.
 */
export function Feed({
  initialPosts,
  path,
  single = false,
  initialComments = null,
}: {
  initialPosts: Post[];
  path: string;
  single?: boolean; // path returns one post rather than a list; also shows its comments
  initialComments?: CommentPage | null;
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

  // Comment changes from the stream, handed to the comment list on a single post page.
  const commentEvents = useMemo(() => new EventTarget(), []);
  const { counts, commentCounts, status } = useLiveCounts(
    posts.map((p) => p.id),
    single ? (change) => commentEvents.dispatchEvent(new CustomEvent("change", { detail: change })) : undefined,
  );

  return (
    <section className="feed">
      {single && <LiveBadge status={status} />}
      {posts.map((p) => (
        <PostCard key={p.id} post={p} live={counts[p.id]} comments={commentCounts[p.id]} single={single} />
      ))}
      {single && posts[0] && (
        <Comments
          post={posts[0]}
          initial={initialComments}
          events={commentEvents}
          live={commentCounts[posts[0].id]}
        />
      )}
    </section>
  );
}

function LiveBadge({ status }: { status: string }) {
  const label = { live: "Live", connecting: "Connecting…", offline: "Offline" }[status] ?? status;
  return (
    <p className={`live-badge ${status}`} role="status">
      <span aria-hidden="true">●</span> {label}
    </p>
  );
}
