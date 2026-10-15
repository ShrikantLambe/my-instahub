import Link from "next/link";
import type { Post } from "@/lib/api";
import { PostImage } from "./PostImage";

export function PostCard({ post }: { post: Post }) {
  return (
    <article className="card">
      <header className="card-head">
        <Link href={`/u/${post.author.username}`}>@{post.author.username}</Link>
        <time dateTime={post.created_at}>{new Date(post.created_at).toLocaleString()}</time>
      </header>
      <PostImage src={post.image_url} alt={post.caption ?? `Photo by @${post.author.username}`} />
      <footer className="card-foot">
        <strong>{post.like_count} likes</strong>
        {post.caption && <p>{post.caption}</p>}
      </footer>
    </article>
  );
}
