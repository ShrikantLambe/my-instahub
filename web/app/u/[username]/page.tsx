import { notFound } from "next/navigation";
import { getUser, getUserPosts } from "@/lib/api";
import { PostImage } from "../../PostImage";
import { ProfileHeader } from "./ProfileHeader";

export const dynamic = "force-dynamic";

export default async function ProfilePage({ params }: { params: Promise<{ username: string }> }) {
  const { username } = await params;
  const user = await getUser(username);
  if (!user) notFound();
  const posts = await getUserPosts(user.username);

  return (
    <section>
      <ProfileHeader initialUser={user} />
      <div className="grid">
        {posts.map((p) => (
          <PostImage key={p.id} src={p.image_url} alt={p.caption ?? `Photo by @${user.username}`} />
        ))}
      </div>
    </section>
  );
}
