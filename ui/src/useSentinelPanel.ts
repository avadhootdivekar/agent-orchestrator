import { useCallback, useState } from "react";
import { api, ApiError } from "./api";
import { errorMessage } from "./errors";
import type { SentinelPanelV2 } from "./types";
import { SENTINEL_PANEL_SCHEMA } from "./types";
import { POLL_MS, usePolling } from "./usePolling";

/**
 * Panel-v2 data of a run (generation timeline, proposals, directives). `panel` stays `null` for an
 * old backend (404) or a foreign body, so the v2 sections simply hide.
 */
export function useSentinelPanel(runId: string) {
  const [panel, setPanel] = useState<SentinelPanelV2 | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const next = await api.sentinelPanel(runId);
      if (next?.schema !== SENTINEL_PANEL_SCHEMA) {
        setPanel(null);
        return;
      }
      setPanel((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setPanel(null);
        setError(null);
      } else setError(errorMessage(err));
    }
  }, [runId]);
  usePolling(load, POLL_MS);
  return { panel, error };
}
