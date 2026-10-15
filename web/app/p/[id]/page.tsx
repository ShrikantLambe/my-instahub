import { notFound } from "next/navigation";
import { getPost } from "@/lib/api";
import { Feed } from "../../Feed";

export const dynamic = "force-dynamic";

// A single post: where User C watches the like count (live updates arrive in Phase 4).
export default async function PostPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  const post = await getPost(id);
  if (!post) notFound();
  return <Feed initialPosts={[post]} path={`/posts/${post.id}`} single />;
}
