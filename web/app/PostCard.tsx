"use client";

import Link from "next/link";
import type { Post } from "@/lib/api";
import { LikeButton } from "./LikeButton";
import { PostImage } from "./PostImage";
import { useLike } from "./useLike";

export function PostCard({ post }: { post: Post }) {
  const { liked, count, toggle, like } = useLike(post);
  return (
    <article className="card">
      <header className="card-head">
        <Link href={`/u/${post.author.username}`}>@{post.author.username}</Link>
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
      </footer>
    </article>
  );
}
