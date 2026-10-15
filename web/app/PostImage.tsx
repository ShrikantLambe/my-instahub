"use client";

import { useState } from "react";

// The photo, or a placeholder tile if it can't load (e.g. the seed posts' sample keys).
export function PostImage({ src, alt }: { src: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="photo" role="img" aria-label={alt}>
        {alt}
      </div>
    );
  }
  // Plain <img>: images come from the bucket's public URL, which varies per environment.
  // eslint-disable-next-line @next/next/no-img-element
  return <img className="photo" src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} />;
}
