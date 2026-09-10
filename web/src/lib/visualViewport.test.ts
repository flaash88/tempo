/**
 * The binding, tested where it can be tested: does the shell take its box
 * from the numbers the visual viewport reports, and does it follow when
 * they change?
 *
 * jsdom has no visual viewport and no zoom, so the object is supplied and
 * the events are fired by hand. That is enough for the thing that broke —
 * the shell was following the laid-out page instead of the visible
 * window — and it covers the keyboard for the same reason it covers zoom:
 * to this code both are just a smaller visible window.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  BOUND_ATTRIBUTE,
  KEYBOARD_CLOSES_AT,
  KEYBOARD_OPENS_AT,
  applyMeasurements,
  keyboardInset,
  keyboardIsOpen,
  measureVisualViewport,
  releaseShell,
} from "./visualViewport";

function shell(): HTMLElement {
  document.body.innerHTML = '<div id="root"></div>';
  return document.getElementById("root") as HTMLElement;
}

function viewport(values: Partial<VisualViewport>): VisualViewport {
  return {
    offsetTop: 0,
    offsetLeft: 0,
    width: 393,
    height: 852,
    scale: 1,
    ...values,
  } as VisualViewport;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("measuring", () => {
  it("takes offset, size and scale as reported", () => {
    const measured = measureVisualViewport(
      viewport({ offsetTop: 189, offsetLeft: 12, width: 197, height: 426, scale: 2 }),
    );

    expect(measured).toEqual({ top: 189, left: 12, width: 197, height: 426, scale: 2 });
  });
});

describe("applying", () => {
  it("writes the box the shell should occupy", () => {
    const root = shell();

    applyMeasurements(root, { top: 189, left: 0, width: 197, height: 426, scale: 2 });

    expect(root.style.getPropertyValue("--vv-top")).toBe("189px");
    expect(root.style.getPropertyValue("--vv-height")).toBe("426px");
    expect(root.style.getPropertyValue("--vv-width")).toBe("197px");
    // The attribute is what switches the CSS over; without it the shell
    // stays on inset: 0, which is right everywhere but iOS standalone.
    expect(root.hasAttribute(BOUND_ATTRIBUTE)).toBe(true);
  });

  it("does not scale the box — the reported size already is scaled", () => {
    const root = shell();
    const view = viewport({ width: 197, height: 426, scale: 2 });

    applyMeasurements(root, measureVisualViewport(view));

    // 426, not 852 or 213: visualViewport reports CSS pixels of the
    // layout viewport, so the box is used as given.
    expect(root.style.getPropertyValue("--vv-height")).toBe("426px");
  });

  it("follows the keyboard, which is only a shorter visible window", () => {
    const root = shell();
    applyMeasurements(root, measureVisualViewport(viewport({})));

    applyMeasurements(root, measureVisualViewport(viewport({ height: 852 - 320 })));

    expect(root.style.getPropertyValue("--vv-height")).toBe("532px");
    expect(root.style.getPropertyValue("--vv-top")).toBe("0px");
  });

  it("follows a panned, zoomed viewport at its offset", () => {
    const root = shell();

    applyMeasurements(
      root,
      measureVisualViewport(viewport({ offsetTop: 189, height: 426, scale: 2 })),
    );

    expect(root.style.getPropertyValue("--vv-top")).toBe("189px");
    expect(root.style.getPropertyValue("--vv-height")).toBe("426px");
  });

  it("hands the shell back when it lets go", () => {
    const root = shell();
    applyMeasurements(root, measureVisualViewport(viewport({})));

    releaseShell(root);

    expect(root.hasAttribute(BOUND_ATTRIBUTE)).toBe(false);
    expect(root.style.getPropertyValue("--vv-height")).toBe("");
  });
});


describe("noticing the keyboard", () => {
  const LAYOUT = 852;

  it("sees nothing covering an untouched window", () => {
    expect(keyboardInset(viewport({}), LAYOUT)).toBe(0);
  });

  it("measures what covers the window from below", () => {
    expect(keyboardInset(viewport({ height: LAYOUT - 320 }), LAYOUT)).toBe(320);
  });

  it("is not fooled by zoom", () => {
    // At zoom 2 the visual viewport is half the page and nothing is
    // covering anything. Comparing the raw heights would call that a
    // 426px keyboard.
    const zoomed = viewport({ height: 426, scale: 2 });

    expect(keyboardInset(zoomed, LAYOUT)).toBe(0);
  });

  it("still finds the keyboard under zoom", () => {
    // Zoomed to 2 with a 320px keyboard: (852 - 320) / 2 = 266.
    const both = viewport({ height: 266, scale: 2 });

    expect(keyboardInset(both, LAYOUT)).toBe(320);
  });

  it("ignores an accessory bar, notices a keyboard", () => {
    // An external keyboard leaves the screen alone or shows only a small
    // suggestion strip; the tab bar should stay in both cases.
    expect(keyboardIsOpen(0, false)).toBe(false);
    expect(keyboardIsOpen(44, false)).toBe(false);
    expect(keyboardIsOpen(320, false)).toBe(true);
  });

  it("holds its answer through the animation", () => {
    // Opening and closing use different thresholds, so a keyboard sliding
    // through the boundary cannot make the bar flicker in and out.
    expect(KEYBOARD_CLOSES_AT).toBeLessThan(KEYBOARD_OPENS_AT);
    const between = (KEYBOARD_OPENS_AT + KEYBOARD_CLOSES_AT) / 2;

    expect(keyboardIsOpen(between, true)).toBe(true);
    expect(keyboardIsOpen(between, false)).toBe(false);
  });

  it("writes the inset out for the layout to use", () => {
    const root = shell();

    applyMeasurements(root, measureVisualViewport(viewport({ height: 532 })), 320);

    expect(root.style.getPropertyValue("--kb-inset")).toBe("320px");
  });
});
