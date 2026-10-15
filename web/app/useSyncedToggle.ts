"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** version: optional ordering for counts (likes have it; follows don't). */
type State = { on: boolean; count: number; version?: number };
type Live = { count: number; version: number };

const newer = (a?: number, b?: number) => a === undefined || b === undefined || a >= b;

/**
 * An on/off control with a count (like, follow), synced to the server optimistically.
 *
 * The UI flips the moment you tap. Requests go out one at a time and the latest intent
 * wins: tap on-off-on quickly and only the final state is sent, so out-of-order
 * responses can't leave the control lying. If a request fails, the UI rolls back to
 * what the server last confirmed. The endpoints are idempotent, so a retried or
 * duplicated request can never count twice.
 *
 * `live` carries other people's changes to the count (e.g. from the SSE stream). It
 * only ever moves the count, never your own on/off state, and anything older than the
 * version already shown is ignored.
 */
export function useSyncedToggle(
  initial: State,
  request: (want: boolean) => Promise<State>,
  live?: Live,
) {
  const [on, setOn] = useState(initial.on);
  const [count, setCount] = useState(initial.count);
  const server = useRef<State>(initial);
  const desired = useRef(initial.on);
  const inFlight = useRef(false);
  const send = useRef(request);
  send.current = request;

  // Fresh data from the server (e.g. a re-fetch with the viewer's token).
  useEffect(() => {
    if (inFlight.current || !newer(initial.version, server.current.version)) return;
    server.current = { ...initial };
    desired.current = initial.on;
    setOn(initial.on);
    setCount(initial.count);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial.on, initial.count, initial.version]);

  // Someone else's change arrived live: update the count only.
  useEffect(() => {
    if (!live) return;
    const shown = server.current.version;
    if (shown !== undefined && shown >= live.version) return; // stale or already applied
    server.current = { ...server.current, count: live.count, version: live.version };
    if (!inFlight.current && desired.current === server.current.on) setCount(live.count);
  }, [live?.version, live?.count]); // eslint-disable-line react-hooks/exhaustive-deps

  const sync = useCallback(async () => {
    if (inFlight.current) return;
    const want = desired.current;
    if (want === server.current.on) return;
    inFlight.current = true;
    try {
      const res = await send.current(want);
      // A live update may already have shown a newer count; keep it, take our on/off.
      server.current = newer(res.version, server.current.version)
        ? res
        : { ...server.current, on: res.on };
      if (desired.current === server.current.on) {
        setOn(server.current.on);
        setCount(server.current.count);
      }
    } catch {
      desired.current = server.current.on;
      setOn(server.current.on);
      setCount(server.current.count);
    } finally {
      inFlight.current = false;
    }
    if (desired.current !== server.current.on) void sync();
  }, []);

  const set = useCallback(
    (want: boolean) => {
      if (want === desired.current) return;
      desired.current = want;
      setOn(want);
      setCount((c) => Math.max(0, c + (want ? 1 : -1)));
      void sync();
    },
    [sync],
  );

  return { on, count, set, toggle: () => set(!desired.current) };
}
