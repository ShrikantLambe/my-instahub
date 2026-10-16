"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import type { Post } from "@/lib/api";
import { Avatar } from "./Avatar";
import { LikeButton } from "./LikeButton";
import { Lightbox } from "./Lightbox";
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
  const [zoomed, setZoomed] = useState(false);
  const pendingOpen = useRef<number | null>(null);
  const alt = post.caption ?? `Photo by @${post.author.username}`;

  // One click opens the photo large; a double click likes it instead. A double click
  // also fires two clicks first, so wait a moment before opening and cancel on the second.
  const cancelOpen = () => {
    if (pendingOpen.current !== null) window.clearTimeout(pendingOpen.current);
    pendingOpen.current = null;
  };
  const onPhotoClick = (e: React.MouseEvent) => {
    if (!(e.target instanceof HTMLImageElement)) return; // placeholder tile: nothing to enlarge
    if (e.detail > 1) return cancelOpen();
    cancelOpen();
    pendingOpen.current = window.setTimeout(() => {
      pendingOpen.current = null;
      setZoomed(true);
    }, 250);
  };
  const onPhotoKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setZoomed(true);
    }
  };
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
      <div
        className="zoomable"
        role="button"
        tabIndex={0}
        aria-label={`Enlarge photo: ${alt}`}
        onClick={onPhotoClick}
        onKeyDown={onPhotoKey}
        onDoubleClick={() => {
          cancelOpen();
          like();
        }}
      >
        <PostImage
          src={post.image_url}
          alt={alt}
          shape="natural"
          width={post.image_width}
          height={post.image_height}
        />
      </div>
      {zoomed && <Lightbox src={post.image_url} alt={alt} onClose={() => setZoomed(false)} />}
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
