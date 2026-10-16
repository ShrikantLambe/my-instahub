"use client";

import Link from "next/link";
import type { Post } from "@/lib/api";
import { Avatar } from "./Avatar";
import { LikeButton } from "./LikeButton";
import { PostImage } from "./PostImage";
import { useLike } from "./useLike";
import type { LiveCount } from "./useLiveCounts";

export function PostCard({
  post,
  live,
  comments,
  single = false,
}: {
  post: Post;
  live?: LiveCount;
  comments?: LiveCount; // live comment count from the stream
  single?: boolean; // on the post's own page, where the comments are listed below
}) {
  const { liked, count, toggle, like } = useLike(post, live);
  // `?? 0` covers an API deployed before comments existed (Vercel can go live first).
  const commentCount =
    comments && comments.version > (post.comment_version ?? 0) ? comments.count : (post.comment_count ?? 0);
  return (
    <article className="card">
      <header className="card-head">
        <Link href={`/u/${post.author.username}`} className="author">
          <Avatar user={post.author} size={28} />
          {post.author.username}
        </Link>
        <Link href={`/p/${post.id}`} className="muted">
          <time dateTime={post.created_at}>{new Date(post.created_at).toLocaleString()}</time>
        </Link>
      </header>
      <div onDoubleClick={like}>
        <PostImage
          src={post.image_url}
          alt={post.caption ?? `Photo by @${post.author.username}`}
          shape="natural"
          width={post.image_width}
          height={post.image_height}
        />
      </div>
      <footer className="card-foot">
        <LikeButton liked={liked} onToggle={toggle} />
        <strong>
          {count} {count === 1 ? "like" : "likes"}
        </strong>
        {post.caption && <p>{post.caption}</p>}
        {!single && (
          <p>
            <Link href={`/p/${post.id}`} className="muted">
              {commentCount === 0
                ? "Add a comment…"
                : commentCount === 1
                  ? "View 1 comment"
                  : `View all ${commentCount} comments`}
            </Link>
          </p>
        )}
      </footer>
    </article>
  );
}
