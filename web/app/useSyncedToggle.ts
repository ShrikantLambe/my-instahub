"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type State = { on: boolean; count: number };

/**
 * An on/off control with a count (like, follow), synced to the server optimistically.
 *
 * The UI flips the moment you tap. Requests go out one at a time and the latest intent
 * wins: tap on-off-on quickly and only the final state is sent, so out-of-order
 * responses can't leave the control lying. If a request fails, the UI rolls back to
 * what the server last confirmed. The endpoints are idempotent, so a retried or
 * duplicated request can never count twice.
 */
export function useSyncedToggle(
  initial: State,
  request: (want: boolean) => Promise<State>,
) {
  const [on, setOn] = useState(initial.on);
  const [count, setCount] = useState(initial.count);
  const server = useRef(initial);
  const desired = useRef(initial.on);
  const inFlight = useRef(false);
  const send = useRef(request);
  send.current = request;

  // Fresh data from the server (e.g. a re-fetch with the viewer's token).
  useEffect(() => {
    if (inFlight.current) return;
    server.current = { on: initial.on, count: initial.count };
    desired.current = initial.on;
    setOn(initial.on);
    setCount(initial.count);
  }, [initial.on, initial.count]);

  const sync = useCallback(async () => {
    if (inFlight.current) return;
    const want = desired.current;
    if (want === server.current.on) return;
    inFlight.current = true;
    try {
      const res = await send.current(want);
      server.current = res;
      if (desired.current === res.on) {
        setOn(res.on);
        setCount(res.count); // the server's count, which includes everyone else's changes
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
