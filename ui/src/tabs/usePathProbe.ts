import { useEffect, useState } from "react";
import { api } from "../api";
import type { PathProbeStatus } from "../types";

/** Server per-request cap (`MAX_PROBE_PATHS`). */
const PROBE_BATCH = 200;

/** Settled probe results, shared by every link on the page. Failures are NOT cached as links. */
const statusCache = new Map<string, PathProbeStatus>();
const inflight = new Set<string>();
const listeners = new Set<() => void>();
let workspaceRootPromise: Promise<string | null> | null = null;

/** Test hook: forget everything learned so far. */
export function resetPathProbeCache(): void {
  statusCache.clear();
  inflight.clear();
  workspaceRootPromise = null;
}

function notify(): void {
  for (const listener of listeners) listener();
}

/** Absolute workspace root (fetched once); `null` until known or if the lookup fails. */
export function useWorkspaceRoot(): string | null {
  const [root, setRoot] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    workspaceRootPromise ??= api
      .workspace()
      .then((info) => info.workspace_root)
      .catch(() => null);
    void workspaceRootPromise.then((value) => {
      if (live) setRoot(value);
    });
    return () => {
      live = false;
    };
  }, []);
  return root;
}

async function probe(paths: string[]): Promise<void> {
  paths.forEach((path) => inflight.add(path));
  try {
    const { results } = await api.resolvePaths(paths);
    for (const { path, status } of results) statusCache.set(path, status);
  } catch {
    // Network/auth/server error: leave the paths unlinked (plain text) rather than guessing.
    for (const path of paths) statusCache.set(path, "denied");
  } finally {
    paths.forEach((path) => inflight.delete(path));
    notify();
  }
}

/**
 * Probe *paths* (once each, batched) and return their statuses. A path is `undefined` while
 * pending; callers must render pending / `missing` / `denied` as plain text.
 */
export function useResolvedPaths(paths: readonly string[]): ReadonlyMap<string, PathProbeStatus> {
  const [, setTick] = useState(0);
  const key = paths.join("\n");
  useEffect(() => {
    const listener = () => setTick((n) => n + 1);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  useEffect(() => {
    const todo = [...new Set(key ? key.split("\n") : [])].filter(
      (path) => !statusCache.has(path) && !inflight.has(path),
    );
    for (let i = 0; i < todo.length; i += PROBE_BATCH) void probe(todo.slice(i, i + PROBE_BATCH));
  }, [key]);
  const known = new Map<string, PathProbeStatus>();
  for (const path of paths) {
    const status = statusCache.get(path);
    if (status) known.set(path, status);
  }
  return known;
}
