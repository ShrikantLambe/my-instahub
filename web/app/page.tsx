import { getPosts } from "@/lib/api";
import { PostCard } from "./PostCard";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const posts = await getPosts();
  if (posts.length === 0) {
    return <p className="empty">No posts yet. Run <code>make seed</code> to add the demo users.</p>;
  }
  return (
    <section className="feed">
      {posts.map((p) => (
        <PostCard key={p.id} post={p} />
      ))}
    </section>
  );
}
