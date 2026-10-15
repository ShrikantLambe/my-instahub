import { notFound } from "next/navigation";
import { getUser, getUserPosts } from "@/lib/api";
import { PostImage } from "../../PostImage";

export const dynamic = "force-dynamic";

export default async function ProfilePage({ params }: { params: Promise<{ username: string }> }) {
  const { username } = await params;
  const user = await getUser(username);
  if (!user) notFound();
  const posts = await getUserPosts(user.username);

  return (
    <section>
      <header className="profile">
        <h1>@{user.username}</h1>
        {user.display_name && <p>{user.display_name}</p>}
        <ul className="stats">
          <li><strong>{user.post_count}</strong> posts</li>
          <li><strong>{user.follower_count}</strong> followers</li>
          <li><strong>{user.following_count}</strong> following</li>
        </ul>
      </header>
      <div className="grid">
        {posts.map((p) => (
          <PostImage key={p.id} src={p.image_url} alt={p.caption ?? `Photo by @${user.username}`} />
        ))}
      </div>
    </section>
  );
}
