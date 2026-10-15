"use client";

export function FollowButton({ following, onToggle }: { following: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      className={following ? "button secondary" : "button"}
      aria-pressed={following}
      onClick={onToggle}
    >
      {following ? "Following" : "Follow"}
    </button>
  );
}
