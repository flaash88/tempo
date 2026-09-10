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
  applyMeasurements,
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
