"use client";

import { useEffect, useRef, useState } from "react";

// Instagram's limits for a photo in the feed: portrait 4:5 up to landscape 1.91:1.
// Photos inside that range show exactly; beyond it they're shown whole with bars.
const MIN_RATIO = 4 / 5;
const MAX_RATIO = 1.91;
const clampRatio = (r: number) => Math.min(MAX_RATIO, Math.max(MIN_RATIO, r));

type Props = {
  src: string;
  alt: string;
  /** "natural": whole photo at its own shape (feed). "square": cropped thumbnail (grids). */
  shape?: "natural" | "square";
  width?: number | null;
  height?: number | null;
};

// The photo, or a placeholder tile if it can't load (e.g. the seed posts' sample keys).
export function PostImage({ src, alt, shape = "square", width, height }: Props) {
  const [failed, setFailed] = useState(false);
  // Known size (stored at upload) reserves the right space before the image arrives,
  // so the feed doesn't jump; older posts get measured once the image loads.
  const [ratio, setRatio] = useState(width && height ? width / height : 1);
  const img = useRef<HTMLImageElement>(null);

  const measure = (el: HTMLImageElement) => {
    if (!(width && height) && el.naturalWidth && el.naturalHeight) {
      setRatio(el.naturalWidth / el.naturalHeight);
    }
  };

  // A server-rendered <img> can finish (or fail) before React hydrates and attaches
  // onLoad/onError, so also check once on mount.
  useEffect(() => {
    const el = img.current;
    if (!el || !el.complete) return;
    if (el.naturalWidth === 0) setFailed(true);
    else measure(el);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src]);

  if (failed) {
    return (
      <div className="photo" role="img" aria-label={alt}>
        {alt}
      </div>
    );
  }

  const image = (
    // Plain <img>: images come from the bucket's public URL, which varies per environment.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      ref={img}
      className={shape === "square" ? "photo" : "photo-natural"}
      src={src}
      alt={alt}
      loading="lazy"
      onLoad={(e) => measure(e.currentTarget)}
      onError={() => setFailed(true)}
    />
  );
  if (shape === "square") return image;
  return (
    <div className="photo-frame" style={{ aspectRatio: clampRatio(ratio) }}>
      {image}
    </div>
  );
}
