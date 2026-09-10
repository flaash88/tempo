/**
 * Bind the app shell to the visual viewport.
 *
 * A `position: fixed` box is laid out against the *layout* viewport. On
 * most engines that is also what you see; in an installed iOS PWA it is
 * not. Zoom in there and the two come apart — the layout viewport stays
 * the full page while the visual viewport is the smaller window you are
 * actually looking at — and WebKit does not reconcile them. The shell
 * then reaches past the visible area: content cut off at the top, a band
 * below the tab bar at the bottom. In a Safari tab neither happens, which
 * is why this only ever showed up as installed.
 *
 * So the shell stops trusting `inset: 0` and takes its box from
 * `visualViewport` instead: offset, width and height, followed on every
 * resize and scroll of it. Those measurements are already in the layout
 * viewport's own CSS pixels, so a fixed box placed at that offset with
 * that size covers exactly what is visible. `scale` is therefore not
 * applied to the box — it is reported by the diagnosis screen, because it
 * is the number that says how far the two viewports have come apart.
 *
 * The same divergence appears when the iOS keyboard opens: the visual
 * viewport shrinks from the bottom while the layout viewport does not.
 * The binding handles that case by construction, which is the other
 * reason not to solve this by forbidding zoom.
 *
 * Without `visualViewport` nothing is set and the CSS falls back to
 * `inset: 0`, which is the behaviour every other engine wants.
 */

import { useEffect } from "react";

export const SHELL_ID = "root";

/** Marks the shell as measured, so the CSS switches to the bound rules. */
export const BOUND_ATTRIBUTE = "data-vv";

type Measurements = {
  top: number;
  left: number;
  width: number;
  height: number;
  scale: number;
};

export function measureVisualViewport(view: VisualViewport): Measurements {
  return {
    top: view.offsetTop,
    left: view.offsetLeft,
    width: view.width,
    height: view.height,
    scale: view.scale,
  };
}

export function applyMeasurements(shell: HTMLElement, values: Measurements): void {
  shell.style.setProperty("--vv-top", `${values.top}px`);
  shell.style.setProperty("--vv-left", `${values.left}px`);
  shell.style.setProperty("--vv-width", `${values.width}px`);
  shell.style.setProperty("--vv-height", `${values.height}px`);
  shell.setAttribute(BOUND_ATTRIBUTE, "");
}

export function releaseShell(shell: HTMLElement): void {
  shell.removeAttribute(BOUND_ATTRIBUTE);
  for (const name of ["--vv-top", "--vv-left", "--vv-width", "--vv-height"]) {
    shell.style.removeProperty(name);
  }
}

export function useVisualViewportShell(): void {
  useEffect(() => {
    const view = window.visualViewport;
    const shell = document.getElementById(SHELL_ID);
    if (!view || !shell) return;

    let frame = 0;
    const apply = () => {
      frame = 0;
      applyMeasurements(shell, measureVisualViewport(view));
    };
    // Pinching fires these continuously; one write per frame is enough
    // and keeps the reflow off the gesture's critical path.
    const schedule = () => {
      if (frame === 0) frame = requestAnimationFrame(apply);
    };

    apply();
    view.addEventListener("resize", schedule);
    view.addEventListener("scroll", schedule);
    window.addEventListener("resize", schedule);
    window.addEventListener("orientationchange", schedule);

    return () => {
      if (frame !== 0) cancelAnimationFrame(frame);
      view.removeEventListener("resize", schedule);
      view.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      window.removeEventListener("orientationchange", schedule);
      releaseShell(shell);
    };
  }, []);
}
