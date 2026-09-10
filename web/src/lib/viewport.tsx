/**
 * The viewport's state, where the screens can see it.
 *
 * One thing lives in here that several screens need to agree on: whether
 * something is covering the bottom of the window. The tab bar has to get
 * out of the way when it is — over the keyboard belongs the field being
 * typed into, not the navigation — and the Coach screen has to put its
 * composer there instead.
 */

import { createContext, useContext, type ReactNode } from "react";
import { CLOSED, useVisualViewportShell, type ViewportState } from "./visualViewport";

const Context = createContext<ViewportState>(CLOSED);

export function ViewportProvider({ children }: { children: ReactNode }) {
  // The binding itself lives here too, so there is exactly one writer.
  const state = useVisualViewportShell();
  return <Context.Provider value={state}>{children}</Context.Provider>;
}

export function useViewport(): ViewportState {
  return useContext(Context);
}
