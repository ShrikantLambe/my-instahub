"use client";

import { useEffect, useRef, useState } from "react";

// The photo, or a placeholder tile if it can't load (e.g. the seed posts' sample keys).
export function PostImage({ src, alt }: { src: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  const img = useRef<HTMLImageElement>(null);

  // A server-rendered <img> can fail before React hydrates and attaches onError,
  // so also check once on mount whether it already finished loading with no pixels.
  useEffect(() => {
    const el = img.current;
    if (el && el.complete && el.naturalWidth === 0) setFailed(true);
  }, [src]);

  if (failed) {
    return (
      <div className="photo" role="img" aria-label={alt}>
        {alt}
      </div>
    );
  }
  // Plain <img>: images come from the bucket's public URL, which varies per environment.
  // eslint-disable-next-line @next/next/no-img-element
  return <img ref={img} className="photo" src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} />;
}
