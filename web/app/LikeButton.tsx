"use client";

export function LikeButton({ liked, onToggle }: { liked: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      className={`like-button${liked ? " liked" : ""}`}
      aria-pressed={liked}
      aria-label={liked ? "Unlike" : "Like"}
      onClick={onToggle}
    >
      <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
        <path
          d="M12 21s-7.5-4.6-9.6-9.3C.9 8.3 3 4.5 6.7 4.5c2.1 0 3.6 1.2 4.3 2.6h2c.7-1.4 2.2-2.6 4.3-2.6 3.7 0 5.8 3.8 4.3 7.2C19.5 16.4 12 21 12 21z"
          fill={liked ? "currentColor" : "none"}
          stroke="currentColor"
          strokeWidth="2"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}
