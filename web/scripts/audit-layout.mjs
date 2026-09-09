/**
 * The layout audit: what only goes wrong on the device.
 *
 * Chromium is not iOS Safari, so this cannot reproduce the viewport
 * quirks themselves. What it can do is check the structural invariants
 * those quirks punish — and the one that produced the wandering tab bar
 * is right at the top: the document must not scroll. A fixed bar over a
 * scrolling body is the arrangement iOS repositions; over a body that
 * cannot scroll it stays put.
 *
 * The safe-area insets are injected, because Chromium reports zero for
 * every env(safe-area-inset-*) and an audit on a device with no notch and
 * no home indicator would pass while the real one fails.
 *
 * Run against a served build:
 *
 *   npx playwright install chromium      # once, if you have no browser
 *   node scripts/audit-layout.mjs        # expects the app on :8331
 *
 * Playwright is deliberately not a dependency of this project: it is a
 * hundred megabytes for a tool that is run by hand when the layout
 * changes, not on every build.
 */

import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

const BASE = process.env.TEMPO_URL ?? "http://127.0.0.1:8331";
// iPhone 15 Pro, portrait, with the insets iOS actually reports in a
// standalone PWA. Chromium's env(safe-area-inset-*) is always 0, so they
// are injected — otherwise the audit would pass on a device that has no
// notch and no home indicator, which is not the device this runs on.
const INSETS = { top: 59, bottom: 34, left: 0, right: 0 };
const TABBAR = 49;
const TOUCH = 44;
const VIEWPORT = { width: 393, height: 852 };

const SCREENS = [
  ["/", "Heute"],
  ["/trends", "Trends"],
  ["/plan", "Plan"],
  ["/coach", "Coach"],
  ["/more", "Mehr"],
  ["/activities", "Aktivitäten"],
];

const OUT = process.env.TEMPO_SHOTS ?? "/tmp/tempo-layout";
await mkdir(OUT, { recursive: true });

// PLAYWRIGHT_CHROMIUM lets an environment point at a browser it already
// has, instead of downloading a second one.
const browser = await chromium.launch(
  process.env.PLAYWRIGHT_CHROMIUM
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM }
    : {},
);
const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
const INSET_CSS = `:root{--inset-top:${INSETS.top}px!important;--inset-bottom:${INSETS.bottom}px!important;--inset-left:${INSETS.left}px!important;--inset-right:${INSETS.right}px!important}`;
async function applyInsets(page) {
  await page.addStyleTag({ content: INSET_CSS });
}

const page = await context.newPage();
const problems = [];
page.on("pageerror", (e) => problems.push(`Konsole: ${e}`));

await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
await applyInsets(page);
if (await page.locator("#password").count()) {
  // The login has no tab bar, so it is checked on its own terms: it must
  // still not hand the scrolling to the document, and its one field and
  // one button must clear the home indicator.
  const login = await page.evaluate((insets) => {
    const out = [];
    const doc = document.scrollingElement;
    if (doc.scrollHeight > doc.clientHeight + 1) out.push("Body scrollt");
    if (doc.scrollWidth > window.innerWidth + 1) out.push("horizontaler Overflow");
    const guard = window.innerHeight - insets.bottom;
    for (const el of document.querySelectorAll("input, button")) {
      const r = el.getBoundingClientRect();
      if (r.height < 44 - 0.5) out.push(`${el.tagName} nur ${Math.round(r.height)}px`);
      if (r.bottom > guard) out.push(`${el.tagName} unter dem Home-Indicator`);
    }
    return out;
  }, INSETS);
  console.log(`\n── Anmeldung`);
  console.log(login.length ? `   ✗ ${login.join("; ")}` : "   ✓ Layout in Ordnung");
  if (login.length) problems.push(`Anmeldung: ${login.join("; ")}`);

  await page.fill("#password", "testpasswort");
  await page.click("button[type=submit]");
  await page.waitForTimeout(900);
}

const report = [];
for (const [route, name] of SCREENS) {
  await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" });
  await applyInsets(page);
  await page.waitForTimeout(500);

  const result = await page.evaluate(({ tabbar, touch, insets }) => {
    const out = { problems: [] };
    const vh = window.innerHeight;
    const vw = window.innerWidth;

    // 1. The document must not scroll. If it does, the tab bar is riding
    //    on a moving body, which is the reported bug.
    out.bodyScrolls = document.scrollingElement.scrollHeight > document.scrollingElement.clientHeight + 1;
    if (out.bodyScrolls) out.problems.push(`Body scrollt (${document.scrollingElement.scrollHeight} > ${document.scrollingElement.clientHeight})`);

    // 2. Exactly one scroll container, and it is the screen.
    const scrollers = [...document.querySelectorAll("*")].filter((el) => {
      const style = getComputedStyle(el);
      return /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 1;
    });
    out.scrollers = scrollers.map((el) => el.tagName + (el.dataset.tempoScroll !== undefined ? "[screen]" : ""));
    if (scrollers.some((el) => el.dataset.tempoScroll === undefined)) out.problems.push("ein anderes Element scrollt");

    // 3. The bar sits on the bottom edge, whatever the content length.
    const nav = document.querySelector("nav[aria-label='Hauptnavigation']");
    if (!nav) { out.problems.push("keine Tab-Leiste"); return out; }
    const bar = nav.getBoundingClientRect();
    out.bar = { top: Math.round(bar.top), bottom: Math.round(bar.bottom), height: Math.round(bar.height) };
    if (Math.abs(bar.bottom - vh) > 1) out.problems.push(`Leiste endet bei ${Math.round(bar.bottom)}, Viewport ${vh}`);
    if (Math.abs(bar.height - (tabbar + insets.bottom)) > 1) out.problems.push(`Leistenhöhe ${Math.round(bar.height)} statt ${tabbar + insets.bottom}`);
    if (getComputedStyle(nav).position !== "fixed") out.problems.push("Leiste nicht fixiert");

    // 4. Nothing horizontal.
    if (document.scrollingElement.scrollWidth > vw + 1) out.problems.push(`horizontaler Overflow: ${document.scrollingElement.scrollWidth} > ${vw}`);

    // 5. Tap targets.
    const interactive = [...document.querySelectorAll("button, a, input, textarea, [role=tab]")];
    out.small = interactive
      .filter((el) => {
        const r = el.getBoundingClientRect();
        // An element that only wraps another target is measured through
        // its child: a link around a button is tapped on the button.
        const wrapsOnlyATarget =
          el.children.length === 1 &&
          el.children[0].matches("button, a, input, textarea");
        return r.width > 0 && r.top < vh && !wrapsOnlyATarget;
      })
      .map((el) => ({ el, r: el.getBoundingClientRect() }))
      .filter(({ r }) => r.height < touch - 0.5)
      .map(({ el, r }) => `${el.tagName}.${(el.className || "").toString().slice(0, 24)} "${(el.textContent || "").trim().slice(0, 24)}" ${Math.round(r.height)}px`);

    // 6. Anything sitting under the home indicator, other than the bar.
    const guard = vh - insets.bottom;
    out.underIndicator = interactive
      .map((el) => ({ el, r: el.getBoundingClientRect() }))
      // Only what is on screen right now: an element below the fold has a
      // rect past the viewport and is not under anything.
      .filter(({ el, r }) => r.height > 0 && r.top < vh && r.bottom > guard && !nav.contains(el))
      .map(({ el, r }) => `${el.tagName} "${(el.textContent || "").trim().slice(0, 24)}" bis ${Math.round(r.bottom)} (Grenze ${guard})`);

    // 7. The last element of the scroller must be reachable above the bar.
    const screen = document.querySelector("[data-tempo-scroll]");
    if (screen) {
      screen.scrollTop = screen.scrollHeight;
      out.scrolledTo = Math.round(screen.scrollTop);
    }
    return out;
  }, { tabbar: TABBAR, touch: TOUCH, insets: INSETS });

  await page.waitForTimeout(200);
  const afterScroll = await page.evaluate(() => {
    const nav = document.querySelector("nav[aria-label='Hauptnavigation']");
    const bar = nav.getBoundingClientRect();
    return { bottom: Math.round(bar.bottom), vh: window.innerHeight };
  });
  if (Math.abs(afterScroll.bottom - afterScroll.vh) > 1) {
    result.problems.push(`Leiste wandert beim Scrollen: ${afterScroll.bottom} statt ${afterScroll.vh}`);
  }

  await page.screenshot({ path: `${OUT}/${name}.png` });
  report.push({ name, ...result });
}

for (const r of report) {
  console.log(`\n── ${r.name}`);
  console.log(`   Leiste: ${JSON.stringify(r.bar)}  Body scrollt: ${r.bodyScrolls}  Scroller: ${r.scrollers?.join(",") || "keiner"}`);
  if (r.small?.length) console.log(`   Tap-Ziele < 44: \n     ${r.small.join("\n     ")}`);
  if (r.underIndicator?.length) console.log(`   Unter dem Home-Indicator: \n     ${r.underIndicator.join("\n     ")}`);
  console.log(r.problems.length ? `   ✗ ${r.problems.join("; ")}` : "   ✓ Layout in Ordnung");
}
console.log(problems.length ? `\nKonsolenfehler: ${problems.join("; ")}` : "\nKeine Konsolenfehler");
console.log(`Screenshots: ${OUT}`);
await browser.close();

const failed = report.filter((r) => r.problems.length).map((r) => r.name);
if (failed.length || problems.length) {
  console.error(`\nFehlerhaft: ${[...failed, ...problems].join(", ")}`);
  process.exit(1);
}
