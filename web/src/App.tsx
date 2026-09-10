/**
 * The shell: session gate, routes, tab bar, and the update prompt.
 *
 * A new service worker never takes over silently — the athlete is asked.
 * Swapping the app out underneath someone reading a number is exactly the
 * kind of surprise a training app should not spring.
 */

import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { TabBar } from "./components/Chrome";
import { ThresholdsProvider } from "./lib/thresholds";
import { apiGet, apiSend } from "./lib/api";
import Heute from "./screens/Heute";
import Trends from "./screens/Trends";
import Plan from "./screens/Plan";
import Coach from "./screens/Coach";
import Mehr from "./screens/Mehr";
import Diagnose from "./screens/Diagnose";
import Anmeldung from "./screens/Anmeldung";
import {
  ActivityDetailScreen,
  ActivityListScreen,
} from "./screens/Aktivitaeten";
import { useUpdatePrompt } from "./lib/updatePrompt";
import { ViewportProvider, useViewport } from "./lib/viewport";

type SessionState = { authenticated: boolean; configured: boolean };

export default function App() {
  const [session, setSession] = useState<SessionState | null>(null);
  const [checked, setChecked] = useState(false);
  const { updateReady, applyUpdate } = useUpdatePrompt();

  const check = useCallback(async () => {
    try {
      const { data } = await apiGet<SessionState>("/api/auth/session", {
        cache: false,
      });
      setSession(data);
    } catch {
      // Offline: the cached screens are still worth showing, and any call
      // that really needs a session will say so on its own.
      setSession({ authenticated: true, configured: true });
    } finally {
      setChecked(true);
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  if (!checked) {
    return (
      <div className="flex h-full items-center justify-center">
        <div
          className="shimmer h-10 w-40"
          style={{ borderRadius: "var(--r-md)" }}
        />
      </div>
    );
  }

  if (session && !session.authenticated) {
    return <Anmeldung onDone={() => void check()} />;
  }

  return (
    <BrowserRouter>
      {/* The shell follows what is visible, not what is laid out — see
          lib/visualViewport.ts. Without it, zooming in the installed app
          cuts the top off and leaves a band under the tab bar. */}
      <ViewportProvider>
        <ThresholdsProvider>
          {updateReady ? (
            <button
              type="button"
              onClick={applyUpdate}
              // Absolute, not fixed: the shell is the containing block, so
              // this rides with it instead of staying behind on the layout
              // viewport when the two come apart.
              className="absolute inset-x-4 z-20 px-3 py-2 text-sub"
              style={{
                top: "calc(var(--inset-top) + var(--sp-2))",
                borderRadius: "var(--r-md)",
                background: "var(--t-accent-soft)",
                color: "var(--t-accent-ink)",
                boxShadow: "inset 0 0 0 1px var(--t-accent-line)",
              }}
            >
              Neue Version verfügbar — tippen zum Laden
            </button>
          ) : null}
          <Routes>
            <Route path="/" element={<Heute />} />
            <Route path="/trends" element={<Trends />} />
            <Route path="/plan" element={<Plan />} />
            <Route path="/coach" element={<Coach />} />
            <Route path="/more" element={<Mehr />} />
            <Route path="/diagnose" element={<Diagnose />} />
            <Route path="/activities" element={<ActivityListScreen />} />
            <Route path="/activity/:id" element={<ActivityDetailScreen />} />
            <Route path="*" element={<Heute />} />
          </Routes>
          <Navigation />
        </ThresholdsProvider>
      </ViewportProvider>
    </BrowserRouter>
  );
}

/**
 * The tab bar, unless something is covering the bottom of the window.
 *
 * With the keyboard open the shell ends at the keyboard's top edge, and
 * the bar — its last child — would sit right above the keys. That is
 * where the field being typed into belongs. So the bar stands down for
 * as long as the keyboard is up, and comes back when it goes.
 */
function Navigation() {
  const { keyboardOpen } = useViewport();
  return keyboardOpen ? null : <TabBar />;
}

/** Used by the settings screen to end the session. */
export async function logout(): Promise<void> {
  await apiSend("/api/auth/logout");
}
