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
 * reason not to solve this by forbidding zoom — and it is also how the
 * keyboard is detected here, see `keyboardInset`.
 *
 * Without `visualViewport` nothing is set and the CSS falls back to
 * `inset: 0`, which is the behaviour every other engine wants.
 */

import { useEffect, useState } from "react";

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

/**
 * How much of the visible window something is covering from below.
 *
 * Zoom shrinks the visual viewport too, so the reported height is scaled
 * back up before it is compared: at zoom 2 a 426px visual viewport is the
 * whole 852px page, and nothing is covering anything. What is left after
 * that is the keyboard — or the dictation bar, which behaves the same way
 * and should be treated the same way.
 *
 * Deliberately not derived from focus events. A field can be focused with
 * an external keyboard attached, where nothing covers the screen and the
 * tab bar should stay; and dictation covers the screen without a focus
 * event of its own. The geometry knows; the events guess.
 */
export function keyboardInset(view: VisualViewport, layoutHeight: number): number {
  return Math.max(0, Math.round(layoutHeight - view.height * view.scale));
}

// Opening and closing use different thresholds, so a keyboard animating
// through the boundary cannot make the bar flicker. Both are far above
// any accessory bar and far below any keyboard.
export const KEYBOARD_OPENS_AT = 120;
export const KEYBOARD_CLOSES_AT = 80;

export function keyboardIsOpen(inset: number, wasOpen: boolean): boolean {
  return wasOpen ? inset > KEYBOARD_CLOSES_AT : inset > KEYBOARD_OPENS_AT;
}

export function measureVisualViewport(view: VisualViewport): Measurements {
  return {
    top: view.offsetTop,
    left: view.offsetLeft,
    width: view.width,
    height: view.height,
    scale: view.scale,
  };
}

export function applyMeasurements(
  shell: HTMLElement,
  values: Measurements,
  inset = 0,
): void {
  shell.style.setProperty("--vv-top", `${values.top}px`);
  shell.style.setProperty("--vv-left", `${values.left}px`);
  shell.style.setProperty("--vv-width", `${values.width}px`);
  shell.style.setProperty("--vv-height", `${values.height}px`);
  shell.style.setProperty("--kb-inset", `${inset}px`);
  shell.setAttribute(BOUND_ATTRIBUTE, "");
}

export function releaseShell(shell: HTMLElement): void {
  shell.removeAttribute(BOUND_ATTRIBUTE);
  for (const name of ["--vv-top", "--vv-left", "--vv-width", "--vv-height", "--kb-inset"]) {
    shell.style.removeProperty(name);
  }
}

export type ViewportState = {
  /** Pixels covered from below — the keyboard, or dictation. */
  keyboardInset: number;
  keyboardOpen: boolean;
};

export const CLOSED: ViewportState = { keyboardInset: 0, keyboardOpen: false };

export function useVisualViewportShell(): ViewportState {
  const [state, setState] = useState<ViewportState>(CLOSED);

  useEffect(() => {
    const view = window.visualViewport;
    const shell = document.getElementById(SHELL_ID);
    if (!view || !shell) return;

    let frame = 0;
    let open = false;
    const apply = () => {
      frame = 0;
      const inset = keyboardInset(view, window.innerHeight);
      open = keyboardIsOpen(inset, open);
      applyMeasurements(shell, measureVisualViewport(view), inset);
      setState((previous) =>
        previous.keyboardInset === inset && previous.keyboardOpen === open
          ? previous
          : { keyboardInset: inset, keyboardOpen: open },
      );
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

  return state;
}
