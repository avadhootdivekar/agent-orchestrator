import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import {
  launchStatusOf,
  readStoredLaunchId,
  writeStoredLaunchId,
} from "./launch";
import type { LaunchRecord } from "./types";

/**
 * Owns the launch-result panel's record for one launcher (`scope` = "workflow" | "template").
 *
 * The last unresolved launch id is kept in sessionStorage, so a failure is still on screen
 * after the operator navigates away and back within the browser tab (until dismissed). A
 * `started` launch is deliberately NOT remembered: it has nothing left to explain.
 */
export function useLaunchPanel(scope: string) {
  const [launch, setLaunch] = useState<LaunchRecord | null>(null);

  const remember = useCallback(
    (record: LaunchRecord | null) => {
      const unresolved =
        record !== null && launchStatusOf(record) !== "started";
      writeStoredLaunchId(scope, unresolved ? record.launch_id : null);
    },
    [scope],
  );

  useEffect(() => {
    const id = readStoredLaunchId(scope);
    if (!id) return;
    api
      .launch(id)
      .then((record) => {
        if (launchStatusOf(record) === "started")
          return writeStoredLaunchId(scope, null);
        // Never clobber a launch the user already started while this was loading.
        setLaunch((current) => current ?? record);
      })
      .catch(() => writeStoredLaunchId(scope, null));
  }, [scope]);

  const show = useCallback(
    (record: LaunchRecord) => {
      setLaunch(record);
      remember(record);
    },
    [remember],
  );

  const clear = useCallback(() => {
    setLaunch(null);
    remember(null);
  }, [remember]);

  return { launch, show, clear, track: remember };
}
