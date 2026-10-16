"use client";

import { useEffect, useRef, useState } from "react";

/** A round profile photo, or the first letter of the username if there's none (or it fails). */
export function Avatar({
  user,
  size = 32,
}: {
  user: { username: string; avatar_url?: string | null };
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  const img = useRef<HTMLImageElement>(null);
  const style = { width: size, height: size, fontSize: size * 0.42 };

  // As with PostImage: a server-rendered <img> can fail before React attaches onError.
  useEffect(() => {
    setFailed(false);
    const el = img.current;
    if (el?.complete && el.naturalWidth === 0) setFailed(true);
  }, [user.avatar_url]);

  if (!user.avatar_url || failed) {
    return (
      <span className="avatar avatar-letter" style={style} aria-hidden="true">
        {user.username.charAt(0).toUpperCase()}
      </span>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      ref={img}
      className="avatar"
      style={style}
      src={user.avatar_url}
      alt=""
      onError={() => setFailed(true)}
    />
  );
}
