// React hook for the live-status WebSocket (spec 04 §WS).
// Replays a stream of corridor risk data; reconnects with
// exponential backoff on disconnect.

import { useEffect, useRef, useState } from "react";

const RECONNECT_BASE_MS = 2000;
const RECONNECT_MAX_MS = 30000;
const MAX_RECONNECTS = 8;

export function useLiveStatus(url = "/api/ws/live-status") {
  const [status, setStatus] = useState("connecting");
  const [messages, setMessages] = useState([]);
  const wsRef = useRef(null);
  const backoffRef = useRef(RECONNECT_BASE_MS);
  const reconnectsRef = useRef(0);
  const timerRef = useRef(null);

  useEffect(() => {
    let alive = true;

    const connect = () => {
      if (!alive) return;
      if (reconnectsRef.current >= MAX_RECONNECTS) {
        setStatus("closed");
        return;
      }

      const apiBase = import.meta.env.VITE_API_BASE;
      let fullUrl;
      if (apiBase) {
        const wsBase = apiBase.replace(/^http/, "ws");
        fullUrl = `${wsBase}${url}`;
      } else {
        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        fullUrl = `${proto}//${window.location.host}${url}`;
      }

      let ws;
      try {
        ws = new WebSocket(fullUrl);
      } catch {
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;
      setStatus("connecting");
      reconnectsRef.current += 1;

      ws.addEventListener("open", () => {
        if (!alive) return;
        setStatus("open");
        backoffRef.current = RECONNECT_BASE_MS;
      });
      ws.addEventListener("message", (ev) => {
        if (!alive) return;
        try {
          const data = JSON.parse(ev.data);
          if (data?.kind === "end") return;
          setMessages((m) => [...m.slice(-50), data]);
        } catch { /* ignore non-JSON */ }
      });
      ws.addEventListener("close", () => {
        if (!alive) return;
        setStatus("closed");
        scheduleReconnect();
      });
      ws.addEventListener("error", () => {
        if (!alive) return;
        setStatus("error");
        try { ws.close(); } catch { /* ignore */ }
      });
    };

    const scheduleReconnect = () => {
      if (!alive) return;
      const delay = Math.min(RECONNECT_MAX_MS, backoffRef.current);
      backoffRef.current = Math.min(RECONNECT_MAX_MS, backoffRef.current * 1.8);
      timerRef.current = setTimeout(connect, delay);
    };

    connect();
    return () => {
      alive = false;
      if (timerRef.current) clearTimeout(timerRef.current);
      if (wsRef.current) {
        try { wsRef.current.close(); } catch { /* ignore */ }
      }
    };
  }, [url]);

  return { status, messages };
}
