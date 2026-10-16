import { notFound } from "next/navigation";
import { getComments, getPost } from "@/lib/api";
import { Feed } from "../../Feed";

export const dynamic = "force-dynamic";

// A single post with its comments; like counts and comments update live.
export default async function PostPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  const [post, comments] = await Promise.all([
    getPost(id),
    getComments(Number(id)).catch(() => null), // the page still works; comments retry in the browser
  ]);
  if (!post) notFound();
  return <Feed initialPosts={[post]} path={`/posts/${post.id}`} single initialComments={comments} />;
}
