"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { apiFetch, type FollowResult, type User } from "@/lib/api";
import { useAuth } from "../../AuthProvider";
import { Avatar } from "../../Avatar";
import { FollowButton } from "../../FollowButton";
import { useSyncedToggle } from "../../useSyncedToggle";

/**
 * Photo, name, bio, city and link, the stats, and follow / edit buttons. The page
 * renders anonymously on the server; once we know who is looking, one re-fetch with
 * their token fills in viewer_follows.
 */
export function ProfileHeader({ initialUser }: { initialUser: User }) {
  const router = useRouter();
  const { session, profile, signOut } = useAuth();
  const [user, setUser] = useState(initialUser);
  const token = session?.access_token;

  useEffect(() => setUser(initialUser), [initialUser]);

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
  const isMe = Boolean(profile && profile.id === user.id);
  const canFollow = Boolean(token && profile && !isMe);

  return (
    <header className="profile">
      <div className="profile-top">
        <Avatar user={user} size={84} />
        <div className="profile-main">
          <div className="profile-title">
            <h1>{user.username}</h1>
            {canFollow && <FollowButton following={follow.on} onToggle={follow.toggle} />}
          </div>
          <ul className="stats">
            <li><strong>{user.post_count}</strong> {user.post_count === 1 ? "post" : "posts"}</li>
            <li><strong>{follow.count}</strong> {follow.count === 1 ? "follower" : "followers"}</li>
            <li><strong>{user.following_count}</strong> following</li>
          </ul>
        </div>
      </div>

      <div className="profile-about">
        {user.display_name && <p className="profile-name">{user.display_name}</p>}
        {user.bio && <p className="profile-bio">{user.bio}</p>}
        {user.city && (
          <p className="profile-city">
            <span aria-hidden="true">📍</span> {user.city}
          </p>
        )}
        {user.website && (
          <p>
            <a href={user.website} target="_blank" rel="noopener noreferrer nofollow ugc">
              {displayUrl(user.website)}
            </a>
          </p>
        )}
      </div>

      {isMe && (
        <div className="profile-actions">
          <Link href="/accounts/edit" className="button secondary">
            Edit profile
          </Link>
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
        </div>
      )}
    </header>
  );
}

// "https://www.example.com/me/" -> "example.com/me"
function displayUrl(url: string): string {
  return url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
}
