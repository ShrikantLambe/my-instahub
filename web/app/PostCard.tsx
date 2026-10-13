import Link from "next/link";
import type { Post } from "@/lib/api";

export function PostCard({ post }: { post: Post }) {
  return (
    <article className="card">
      <header className="card-head">
        <Link href={`/u/${post.author.username}`}>@{post.author.username}</Link>
        <time dateTime={post.created_at}>{new Date(post.created_at).toLocaleString()}</time>
      </header>
      {/* Phase 0: no uploads yet, so every post shows a placeholder tile. Phase 1 swaps in the R2 image. */}
      <div className="photo" aria-label={`Placeholder for ${post.image_key}`}>
        {post.image_key}
      </div>
      <footer className="card-foot">
        <strong>{post.like_count} likes</strong>
        {post.caption && <p>{post.caption}</p>}
      </footer>
    </article>
  );
}
