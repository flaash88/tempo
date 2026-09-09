/**
 * The diagnosis screen has one job: work when everything else does not.
 *
 * It is the thing that will settle the standalone question from the
 * device, so it must render without any of the APIs it reports being
 * present, and it must carry every field that was asked for — a screen
 * that quietly drops visualViewport is a screen that sends someone back
 * to the phone with a debugger.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Diagnose from "./Diagnose";

function render(): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <Diagnose />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  // jsdom has no matchMedia at all, which is also a fair impression of an
  // old browser: the screen has to survive it.
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({ matches: query.includes("standalone"), media: query })),
  );
});

describe("the diagnosis screen", () => {
  it("reports the display mode both ways round", () => {
    const markup = render();

    // navigator.standalone is iOS's answer and the historical one;
    // display-mode is the standards answer. They can disagree.
    expect(markup).toContain("navigatorStandalone");
    expect(markup).toContain("displayModeStandalone");
    expect(markup).toContain("appleStatusBarStyle");
    expect(markup).toContain("viewportMeta");
  });

  it("reports the heights that can disagree with each other", () => {
    const markup = render();

    for (const field of [
      "innerHeight",
      "visualViewportHeight",
      "visualViewportOffsetTop",
      "documentScrollHeight",
      "documentScrollTop",
      "einheit100vh",
      "einheit100dvh",
      "einheit100svh",
      "einheit100lvh",
    ]) {
      expect(markup).toContain(field);
    }
  });

  it("reports the four insets and the bar's own box", () => {
    const markup = render();

    expect(markup).toContain("sicherheitsabstaende");
    expect(markup).toContain("abstandZumUnterrand");
    expect(markup).toContain("devicePixelRatio");
    expect(markup).toContain("userAgent");
  });

  it("says in one sentence whether the bar sits right", () => {
    const markup = render();

    // Without a tab bar in the tree there is nothing to measure, and the
    // screen has to say so rather than claim everything is fine.
    expect(markup).toContain("Keine Tab-Leiste gefunden");
  });

  it("offers the values as JSON to copy", () => {
    const markup = render();

    expect(markup).toContain("Als JSON kopieren");
    expect(markup).toContain("diagnose-json");
  });

  it("renders without visualViewport", () => {
    vi.stubGlobal("visualViewport", undefined);

    expect(() => render()).not.toThrow();
  });
});
