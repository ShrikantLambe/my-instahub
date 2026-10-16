"use client";

import { useEffect, useRef } from "react";

/**
 * A photo shown large over the page. A native <dialog> opened as a modal gives us
 * Escape to close, focus kept inside, and the page behind made inert. Clicking
 * anywhere outside the photo also closes it.
 */
export function Lightbox({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    el.showModal();
    // Stop the feed scrolling underneath while the photo is open.
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
      if (el.open) el.close();
    };
  }, []);

  return (
    <dialog
      ref={dialog}
      className="lightbox"
      aria-label={alt}
      onClose={onClose}
      onClick={(e) => {
        if (e.target !== e.currentTarget.querySelector("img")) dialog.current?.close();
      }}
    >
      <button type="button" className="lightbox-close" aria-label="Close" autoFocus>
        ✕
      </button>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={alt} onError={() => dialog.current?.close()} />
    </dialog>
  );
}
