"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { API_URL, apiFetch, type Notification } from "@/lib/api";
import { useAuth } from "./AuthProvider";

type NotificationsState = {
  unread: number;
  /** Fires "notification" events (detail: Notification) as they arrive live. */
  events: EventTarget;
  markAllRead: () => Promise<void>;
};

const Ctx = createContext<NotificationsState | null>(null);

/**
 * The signed-in user's unread count, kept live over Server-Sent Events.
 *
 * EventSource can't send the auth token, so each connection starts by trading the
 * token for a short-lived, single-use ticket. When the stream drops (network, API
 * restart, a deploy), we fetch a new ticket and reconnect with backoff; the stream
 * always opens with the current count, so nothing is missed.
 */
export function NotificationsProvider({ children }: { children: React.ReactNode }) {
  const { session, profile } = useAuth();
  const [unread, setUnread] = useState(0);
  const events = useMemo(() => new EventTarget(), []);
  const tokenRef = useRef(session?.access_token);
  tokenRef.current = session?.access_token;
  const signedIn = Boolean(session && profile);
  const userId = profile?.id;

  useEffect(() => {
    if (!signedIn || typeof EventSource === "undefined") {
      setUnread(0);
      return;
    }
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let delay = 1000;
    let stopped = false;

    const connect = async () => {
      if (stopped || !tokenRef.current) return;
      try {
        const { ticket } = await apiFetch<{ ticket: string }>("/notifications/ticket", tokenRef.current, {
          method: "POST",
        });
        if (stopped) return;
        es = new EventSource(`${API_URL}/notifications/stream?ticket=${encodeURIComponent(ticket)}`);
        es.addEventListener("unread", (e) => {
          delay = 1000; // connected: reset the backoff
          setUnread(JSON.parse((e as MessageEvent).data).unread_count);
        });
        es.addEventListener("notification", (e) => {
          const d = JSON.parse((e as MessageEvent).data) as { unread_count: number; notification?: Notification };
          setUnread(d.unread_count);
          if (d.notification) events.dispatchEvent(new CustomEvent("notification", { detail: d.notification }));
        });
        // The ticket is spent, so EventSource's own retry would be refused: start over.
        es.onerror = () => {
          es?.close();
          scheduleReconnect();
        };
      } catch {
        scheduleReconnect();
      }
    };
    const scheduleReconnect = () => {
      if (stopped) return;
      retry = setTimeout(connect, delay);
      delay = Math.min(delay * 2, 30000);
    };

    void connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      es?.close();
    };
  }, [signedIn, userId, events]);

  const markAllRead = useCallback(async () => {
    if (!tokenRef.current) return;
    setUnread(0);
    await apiFetch("/notifications/read", tokenRef.current, { method: "POST" }).catch(() => {});
  }, []);

  return <Ctx.Provider value={{ unread, events, markAllRead }}>{children}</Ctx.Provider>;
}

export function useNotifications(): NotificationsState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useNotifications must be used inside <NotificationsProvider>");
  return ctx;
}
