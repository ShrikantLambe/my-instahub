import { notFound } from "next/navigation";
import { getPosts, getUser } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ProfilePage({ params }: { params: Promise<{ username: string }> }) {
  const { username } = await params;
  const user = await getUser(username);
  if (!user) notFound();
  // Phase 1 adds a per-user posts endpoint; for now filter the global list.
  const posts = (await getPosts()).filter((p) => p.author.username === user.username);

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
          <div key={p.id} className="photo" title={p.caption ?? ""}>
            {p.caption}
          </div>
        ))}
      </div>
    </section>
  );
}
