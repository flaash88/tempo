/**
 * Service worker registration, with the update offered rather than applied.
 *
 * `registerType: "prompt"` in the Vite config is only half of it: the other
 * half is asking. A worker that activates itself mid-session can replace the
 * page under the athlete's thumb, and the numbers on screen are the one
 * thing that must not change without being asked for.
 */

import { useEffect, useState } from "react";
import { registerSW } from "virtual:pwa-register";

export function useUpdatePrompt() {
  const [updateReady, setUpdateReady] = useState(false);
  const [apply, setApply] = useState<(() => void) | null>(null);

  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const update = registerSW({
      immediate: true,
      onNeedRefresh() {
        setUpdateReady(true);
      },
    });
    setApply(() => () => void update(true));
  }, []);

  return {
    updateReady,
    applyUpdate: () => {
      setUpdateReady(false);
      apply?.();
    },
  };
}
