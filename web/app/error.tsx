"use client";

// Shown when a page can't load its data, most often because the API isn't reachable.
export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <section className="empty">
      <h1>Can&apos;t reach the API</h1>
      <p>
        The web app is up, but it couldn&apos;t load data from the API. Check that the API is running
        and that <code>NEXT_PUBLIC_API_URL</code> points at it.
      </p>
      <button onClick={reset}>Try again</button>
    </section>
  );
}
