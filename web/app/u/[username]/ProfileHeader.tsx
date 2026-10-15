"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { apiFetch, type FollowResult, type User } from "@/lib/api";
import { useAuth } from "../../AuthProvider";
import { FollowButton } from "../../FollowButton";
import { useSyncedToggle } from "../../useSyncedToggle";

/**
 * Profile stats plus the follow button. The page renders anonymously on the server;
 * once we know who is looking, one re-fetch with their token fills in viewer_follows.
 */
export function ProfileHeader({ initialUser }: { initialUser: User }) {
  const router = useRouter();
  const { session, profile, signOut } = useAuth();
  const [user, setUser] = useState(initialUser);
  const token = session?.access_token;

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    apiFetch<User>(`/users/${encodeURIComponent(initialUser.username)}`, token)
      .then((u) => !cancelled && setUser(u))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [token, initialUser.username]);

  const follow = useSyncedToggle(
    { on: user.viewer_follows, count: user.follower_count },
    async (want) => {
      const res = await apiFetch<FollowResult>(
        `/users/${encodeURIComponent(user.username)}/follow`,
        token!,
        { method: want ? "POST" : "DELETE" },
      );
      return { on: res.following, count: res.follower_count };
    },
  );

  // Only signed-in users with a username can follow, and never themselves.
  const isMe = Boolean(profile && profile.username === user.username);
  const canFollow = Boolean(token && profile && !isMe);

  return (
    <header className="profile">
      <div className="profile-title">
        <h1>@{user.username}</h1>
        {canFollow && <FollowButton following={follow.on} onToggle={follow.toggle} />}
        {isMe && (
          <button
            type="button"
            className="button secondary"
            onClick={async () => {
              await signOut();
              router.push("/");
              router.refresh();
            }}
          >
            Log out
          </button>
        )}
      </div>
      {user.display_name && <p>{user.display_name}</p>}
      <ul className="stats">
        <li><strong>{user.post_count}</strong> posts</li>
        <li><strong>{follow.count}</strong> {follow.count === 1 ? "follower" : "followers"}</li>
        <li><strong>{user.following_count}</strong> following</li>
      </ul>
    </header>
  );
}
