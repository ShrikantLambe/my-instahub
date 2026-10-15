import { getPosts } from "@/lib/api";
import { Feed } from "../Feed";

export const dynamic = "force-dynamic";

// Latest posts from everyone: where you find people to follow.
export default async function ExplorePage() {
  const posts = await getPosts();
  if (posts.length === 0) return <p className="empty">No posts yet.</p>;
  return <Feed initialPosts={posts} path="/posts" />;
}
