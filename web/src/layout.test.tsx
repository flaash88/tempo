/**
 * The layout contract, pinned.
 *
 * jsdom does no layout, so these cannot measure anything — the measuring
 * is what scripts/audit-layout.mjs does against a real browser. What they
 * can do is hold the arrangement that made the tab bar sit still, because
 * the bug that started this was one word: the screen was a min-height box
 * instead of a height box, so it grew with its content, never scrolled
 * itself, and handed the scrolling to the document. A fixed bar over a
 * scrolling body is what iOS repositions.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { Screen, TabBar } from "./components/Chrome";

const css = readFileSync(resolve(__dirname, "styles/index.css"), "utf-8");

describe("the app shell", () => {
  it("is one dynamic viewport tall, with the static one as the fallback", () => {
    const body = css.slice(css.indexOf("body {"), css.indexOf("#root {"));

    // Order matters: a browser that does not know dvh keeps the vh above
    // it, and one that does overrides it.
    expect(body.indexOf("height: 100vh")).toBeGreaterThan(-1);
    expect(body.indexOf("height: 100dvh")).toBeGreaterThan(body.indexOf("height: 100vh"));
  });

  it("does not let the document scroll", () => {
    const body = css.slice(css.indexOf("body {"), css.indexOf("#root {"));

    expect(body).toContain("overflow: hidden");
  });

  it("gives the scrolling to the screen, and only to the screen", () => {
    const scroller = css.slice(css.indexOf("[data-tempo-scroll] {"));

    expect(scroller).toContain("overflow-y: auto");
    expect(scroller).toContain("flex: 1 1 auto");
    // Without min-height:0 a flex child refuses to shrink and grows the
    // shell back out of the viewport.
    expect(scroller).toContain("min-height: 0");
  });
});

describe("a screen", () => {
  const markup = renderToStaticMarkup(
    <Screen title="Plan">
      <p>Inhalt</p>
    </Screen>,
  );

  it("is the scroll container", () => {
    expect(markup).toContain("data-tempo-scroll");
  });

  it("is not a min-height box", () => {
    // The regression itself: min-h-full grows with the content and never
    // scrolls, which moves the scrolling to the document.
    expect(markup).not.toContain("min-h-full");
    expect(markup).not.toContain("min-h-screen");
  });

  it("leaves room below its content for the bar and the home indicator", () => {
    expect(markup).toContain("var(--tabbar-h)");
    expect(markup).toContain("var(--inset-bottom)");
  });
});

describe("the tab bar", () => {
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <TabBar />
    </MemoryRouter>,
  );

  it("is anchored to the viewport, not to the content", () => {
    expect(markup).toContain("fixed");
    expect(markup).toContain("bottom-0");
  });

  it("keeps its labels above the home indicator", () => {
    // The strip is added below the bar as padding rather than eaten out
    // of the row the labels sit in.
    expect(markup).toContain("padding-bottom:var(--inset-bottom)");
    expect(markup).toContain("height:calc(var(--tabbar-h) + var(--inset-bottom))");
  });

  it("offers all five destinations, each a touch target", () => {
    for (const label of ["Heute", "Trends", "Plan", "Coach", "Mehr"]) {
      expect(markup).toContain(label);
    }
    expect(markup.match(/min-height:var\(--touch\)/g)).toHaveLength(5);
  });
});
