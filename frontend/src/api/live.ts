// Live asset updates from the backend WebSocket at /api/ws.

import { useEffect, useState } from "react";
import type { QueryClient } from "@tanstack/react-query";
import type { Asset, AssetStatus, Gate, Stage } from "./client";
import { keys } from "./queries";

interface AssetEvent {
  type: "status" | "progress";
  asset_id: string;
  status: AssetStatus;
  stage: Stage;
  gate: Gate | null;
  step: string;
  progress: number;
  error: string | null;
}

type LiveEvent = { type: "hello" } | AssetEvent | { type: "deleted"; asset_id: string };

export type LiveState = "connecting" | "live" | "offline";

function patch(client: QueryClient, event: AssetEvent) {
  const changes = {
    status: event.status,
    stage: event.stage,
    gate: event.gate,
    step: event.step,
    progress: event.progress,
    error: event.error,
  };
  client.setQueryData<Asset>(keys.asset(event.asset_id), (old) => (old ? { ...old, ...changes } : old));
  client.setQueriesData<Asset[]>({ queryKey: [...keys.assets, "list"] }, (old) =>
    old?.map((a) => (a.id === event.asset_id ? { ...a, ...changes } : a)),
  );
}

function handle(client: QueryClient, event: LiveEvent) {
  switch (event.type) {
    case "progress":
      patch(client, event);
      break;
    case "status":
      // Show the new status at once, then fetch the full asset (with its file URLs).
      patch(client, event);
      void client.invalidateQueries({ queryKey: keys.asset(event.asset_id) });
      void client.invalidateQueries({ queryKey: [...keys.assets, "list"] });
      break;
    case "deleted":
      client.removeQueries({ queryKey: keys.asset(event.asset_id) });
      void client.invalidateQueries({ queryKey: [...keys.assets, "list"] });
      break;
  }
}

/** Keeps a WebSocket open (reconnecting with backoff) and folds its events into the query cache. */
export function useLiveUpdates(client: QueryClient): LiveState {
  const [state, setState] = useState<LiveState>("connecting");

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let delay = 500;
    let stopped = false;

    const connect = () => {
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${window.location.host}/api/ws`);
      socket.onopen = () => {
        delay = 500;
        setState("live");
        // Anything may have changed while we were away.
        void client.invalidateQueries({ queryKey: keys.assets });
        void client.invalidateQueries({ queryKey: ["asset"] });
      };
      socket.onmessage = (message) => {
        try {
          handle(client, JSON.parse(message.data) as LiveEvent);
        } catch {
          // ignore malformed messages
        }
      };
      socket.onclose = () => {
        if (stopped) return;
        setState("offline");
        retry = setTimeout(connect, delay);
        delay = Math.min(delay * 2, 10_000);
      };
    };
    connect();

    return () => {
      stopped = true;
      clearTimeout(retry);
      if (socket?.readyState === WebSocket.CONNECTING) {
        // Closing now would log a warning; close as soon as it opens instead.
        const pending = socket;
        pending.onopen = () => pending.close();
        pending.onmessage = null;
      } else {
        socket?.close();
      }
    };
  }, [client]);

  return state;
}
