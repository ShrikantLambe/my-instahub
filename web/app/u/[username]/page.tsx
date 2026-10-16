import Link from "next/link";
import { notFound } from "next/navigation";
import { getUser, getUserPosts } from "@/lib/api";
import { PostImage } from "../../PostImage";
import { ProfileHeader } from "./ProfileHeader";

export const dynamic = "force-dynamic";

export default async function ProfilePage({ params }: { params: Promise<{ username: string }> }) {
  const { username } = await params;
  // In parallel: both only need the username.
  const [user, posts] = await Promise.all([getUser(username), getUserPosts(username)]);
  if (!user) notFound();

  return (
    <section>
      <ProfileHeader initialUser={user} />
      {posts.length === 0 ? (
        <p className="empty">No posts yet.</p>
      ) : (
        <div className="grid">
          {posts.map((p) => (
            <Link key={p.id} href={`/p/${p.id}`} aria-label={p.caption ?? `Post by @${user.username}`}>
              <PostImage src={p.image_url} alt={p.caption ?? `Photo by @${user.username}`} />
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
