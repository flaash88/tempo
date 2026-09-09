/**
 * The layout audit: what only goes wrong on the device.
 *
 * Chromium is not iOS Safari, so this cannot reproduce WebKit's viewport
 * arithmetic. What it can do is check the structural invariants that
 * arithmetic punishes — and the first one is that the document must not
 * scroll. A bar positioned against the viewport, over a scrolling
 * document, is the arrangement iOS moves around.
 *
 * Two modes, because the bug was visible in only one of them: the app was
 * right in a Safari tab and wrong as an installed PWA.
 *
 * "Tab" is an ordinary page. "Standalone" is a real one: Chromium is
 * launched with --app=, which makes (display-mode: standalone) genuinely
 * match — CDP's Emulation.setEmulatedMedia does not emulate that feature,
 * checked, it stays false — and the insets iOS reports for an installed
 * app are injected on top, a 59px status bar and a 34px home indicator.
 *
 * What the standalone pass therefore catches is anything that depends on
 * the display mode or on a non-zero bottom inset. What it cannot catch is
 * WebKit's own viewport arithmetic, which is why the app carries a
 * diagnosis screen that reports the device's real numbers.
 *
 * Run against a served build:
 *
 *   npx playwright install chromium      # once, if you have no browser
 *   node web/scripts/audit-layout.mjs    # expects the app on :8331
 *
 * TEMPO_URL, TEMPO_SHOTS and PLAYWRIGHT_CHROMIUM override the defaults.
 *
 * Playwright is deliberately not a dependency of this project: a hundred
 * megabytes for a tool run by hand when the layout changes is not worth
 * carrying in every build.
 */

import { mkdir, mkdtemp } from "node:fs/promises";
import { decodePng, distance, hex } from "./png.mjs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

const BASE = process.env.TEMPO_URL ?? "http://127.0.0.1:8331";
const PASSWORD = process.env.TEMPO_PASSWORD ?? "testpasswort";
const OUT = process.env.TEMPO_SHOTS ?? "/tmp/tempo-layout";
const VIEWPORT = { width: 393, height: 852 };
const TABBAR = 49;
const TOUCH = 44;
// How far apart two 8-bit channels may be and still count as the same
// colour: enough for the blur behind the bar, far less than the step
// between the page ground and the bar's.
const COLOUR_TOLERANCE = 6;
// The bar's reference colour is read above its safe-area padding, so the
// sample sits in the row the labels are in rather than in the strip the
// home indicator occupies.
const INSETS_SAMPLE_GAP = 24;

// What iOS reports in each mode on a notched iPhone. In a Safari tab the
// bottom inset is zero while the toolbar is up; installed, the home
// indicator's strip is there and nothing covers it.
const MODES = [
  { name: "Tab", standalone: false, insets: { top: 0, bottom: 0, left: 0, right: 0 } },
  {
    name: "Standalone",
    standalone: true,
    insets: { top: 59, bottom: 34, left: 0, right: 0 },
  },
];

const SCREENS = [
  ["/", "Heute"],
  ["/trends", "Trends"],
  ["/plan", "Plan"],
  ["/coach", "Coach"],
  ["/more", "Mehr"],
  ["/diagnose", "Diagnose"],
  ["/activities", "Aktivitäten"],
];

/**
 * Reproduce the reported fault on purpose.
 *
 * Chromium's shell does reach the bottom, so the strip is zero rows high
 * here and the checks that look at it never fire — which is precisely how
 * a green run coexisted with a strip on the device. TEMPO_SIMULATE_STRIP=34
 * shortens the shell by that many pixels, and the run must then go red.
 * A check that cannot be made to fail is not a check.
 */
const SIMULATED_STRIP = Number(process.env.TEMPO_SIMULATE_STRIP ?? 0);
const simulationCss = SIMULATED_STRIP
  ? `#root{bottom:${SIMULATED_STRIP}px!important}`
  : "";

const insetCss = (insets) =>
  `:root{--inset-top:${insets.top}px!important;--inset-bottom:${insets.bottom}px!important;` +
  `--inset-left:${insets.left}px!important;--inset-right:${insets.right}px!important}` +
  simulationCss;

await mkdir(OUT, { recursive: true });

const launchOptions = process.env.PLAYWRIGHT_CHROMIUM
  ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM }
  : {};

/**
 * A browsing context in the requested mode.
 *
 * Standalone needs its own browser: the app window is a launch flag, not
 * a context option.
 */
async function openMode(mode) {
  if (!mode.standalone) {
    const browser = await chromium.launch(launchOptions);
    const context = await browser.newContext({
      viewport: VIEWPORT,
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
    });
    return { page: await context.newPage(), close: () => browser.close() };
  }

  // Playwright's viewport emulation and --app= do not coexist: with a
  // viewport set, the app window never opens and only about:blank is
  // there. So the window is sized by the browser instead, and every
  // assertion below is made against window.innerHeight rather than
  // against a hard-coded height.
  const profile = await mkdtemp(join(tmpdir(), "tempo-audit-"));
  const context = await chromium.launchPersistentContext(profile, {
    ...launchOptions,
    args: [`--app=${BASE}/`, `--window-size=${VIEWPORT.width},${VIEWPORT.height}`],
    viewport: null,
    hasTouch: true,
  });

  const deadline = Date.now() + 10_000;
  let page;
  while (Date.now() < deadline) {
    page = context.pages().find((candidate) => candidate.url().startsWith(BASE));
    if (page) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (!page) throw new Error("das App-Fenster ist nicht aufgegangen");
  return { page, close: () => context.close() };
}

/** Everything measured on one screen, in one mode. */
async function inspect(page, { tabbar, touch, insets }) {
  return page.evaluate(
    ({ tabbar, touch, insets }) => {
      const out = { problems: [] };
      const vh = window.innerHeight;
      const vw = window.innerWidth;
      const doc = document.scrollingElement ?? document.documentElement;

      // 1. The document must not scroll. If it does, whatever sits at the
      //    bottom is riding on a moving surface.
      out.bodyScrolls = doc.scrollHeight > doc.clientHeight + 1;
      if (out.bodyScrolls) {
        out.problems.push(`Dokument scrollt (${doc.scrollHeight} > ${doc.clientHeight})`);
      }

      // 2. Only the screen scrolls the layout. A form control with more
      //    content than room — the diagnosis screen's JSON field — scrolls
      //    its own contents and cannot move anything around it, so it is
      //    not what this is looking for.
      const scrollers = [...document.querySelectorAll("*")].filter((el) => {
        const style = getComputedStyle(el);
        return /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 1;
      });
      const layoutScrollers = scrollers.filter(
        (el) => !el.matches("textarea, select, pre, code"),
      );
      out.scrollers = layoutScrollers.map(
        (el) => el.tagName + (el.dataset.tempoScroll !== undefined ? "[screen]" : ""),
      );
      if (layoutScrollers.some((el) => el.dataset.tempoScroll === undefined)) {
        out.problems.push("ein anderes Element als der Screen scrollt");
      }

      // 3. The shell fills the visible area, whatever the mode thinks that
      //    is — and its *bottom edge* has to be on the bottom edge, not
      //    merely its height be right. A shell of the correct height that
      //    sits 34px too high passes a height check and still leaves a
      //    strip at the bottom, which is exactly what was reported.
      const shell = document.getElementById("root");
      const shellBox = shell.getBoundingClientRect();
      out.shell = {
        top: Math.round(shellBox.top),
        bottom: Math.round(shellBox.bottom),
        height: Math.round(shellBox.height),
        position: getComputedStyle(shell).position,
      };
      if (Math.abs(shellBox.height - vh) > 1) {
        out.problems.push(`Hülle ${Math.round(shellBox.height)} hoch statt ${vh}`);
      }
      if (Math.abs(shellBox.bottom - vh) > 1) {
        out.problems.push(
          `Hülle endet bei ${Math.round(shellBox.bottom)}, sichtbar bis ${vh} — ` +
            `${Math.round(vh - shellBox.bottom)}px Streifen darunter`,
        );
      }
      if (Math.abs(shellBox.top) > 1) {
        out.problems.push(`Hülle beginnt bei ${Math.round(shellBox.top)} statt 0`);
      }

      // 4. The bar sits on the bottom edge, whatever the content length.
      const nav = document.querySelector("nav[aria-label='Hauptnavigation']");
      if (!nav) {
        out.noBar = true;
        return out;
      }
      const bar = nav.getBoundingClientRect();
      out.bar = {
        top: Math.round(bar.top),
        bottom: Math.round(bar.bottom),
        height: Math.round(bar.height),
        position: getComputedStyle(nav).position,
      };
      if (Math.abs(bar.bottom - vh) > 1) {
        out.problems.push(`Leiste endet bei ${Math.round(bar.bottom)}, sichtbar bis ${vh}`);
      }
      if (Math.abs(bar.height - (tabbar + insets.bottom)) > 1) {
        out.problems.push(`Leistenhöhe ${Math.round(bar.height)} statt ${tabbar + insets.bottom}`);
      }
      // The labels must clear the home indicator's strip.
      const label = nav.querySelector("span");
      if (label && label.getBoundingClientRect().bottom > vh - insets.bottom + 0.5) {
        out.problems.push("Beschriftung ragt in den Home-Indicator");
      }

      // 4b. Whatever lies between the bar's bottom edge and the bottom of
      //     the screen must be the bar. Anything else — the shell, the
      //     body, nothing at all — is a strip the page shows through.
      out.strip = { from: Math.round(bar.bottom), to: vh, uncovered: [] };
      for (let y = Math.ceil(bar.bottom) + 1; y < vh; y += 2) {
        for (const x of [2, Math.round(vw / 2), vw - 3]) {
          const el = document.elementFromPoint(x, y);
          const covered = el !== null && (el === nav || nav.contains(el));
          if (!covered) {
            out.strip.uncovered.push(`${x}/${y}: ${el ? el.tagName + (el.id ? "#" + el.id : "") : "nichts"}`);
          }
        }
      }
      if (out.strip.uncovered.length) {
        out.problems.push(
          `unter der Leiste liegt nicht die Leiste: ${out.strip.uncovered.slice(0, 3).join(", ")}`,
        );
      }

      // 5. Nothing horizontal.
      if (doc.scrollWidth > vw + 1) {
        out.problems.push(`horizontaler Overflow: ${doc.scrollWidth} > ${vw}`);
      }

      // 6. Tap targets, and nothing hiding under the home indicator.
      const interactive = [...document.querySelectorAll("button, a, input, textarea, [role=tab]")];
      const onScreen = interactive
        .map((el) => ({ el, r: el.getBoundingClientRect() }))
        .filter(({ r }) => r.width > 0 && r.top < vh);

      out.small = onScreen
        .filter(({ el }) => {
          // A link that only wraps another target is tapped on the target.
          const wrapsOnlyATarget =
            el.children.length === 1 && el.children[0].matches("button, a, input, textarea");
          return !wrapsOnlyATarget;
        })
        .filter(({ r }) => r.height < touch - 0.5)
        .map(({ el, r }) => `${el.tagName} "${(el.textContent || "").trim().slice(0, 24)}" ${Math.round(r.height)}px`);
      if (out.small.length) out.problems.push(`${out.small.length} Tap-Ziel(e) unter ${touch}pt`);

      const guard = vh - insets.bottom;
      out.underIndicator = onScreen
        .filter(({ el, r }) => r.bottom > guard && !nav.contains(el))
        .map(({ el, r }) => `${el.tagName} "${(el.textContent || "").trim().slice(0, 24)}" bis ${Math.round(r.bottom)}`);
      if (out.underIndicator.length) out.problems.push("etwas liegt unter dem Home-Indicator");

      // 7. Scroll to the end; the bar must not follow.
      const screen = document.querySelector("[data-tempo-scroll]");
      if (screen) screen.scrollTop = screen.scrollHeight;
      out.scale = window.devicePixelRatio;
      out.viewport = { width: vw, height: vh };
      return out;
    },
    { tabbar, touch, insets },
  );
}

/**
 * What the screenshot actually shows below the tab bar.
 *
 * The DOM can be right and the paint still wrong, and the strip that was
 * reported is a painting problem: the band under the bar showed the page
 * ground instead of the bar's. So the reference colour is read from
 * inside the bar — from its left edge, clear of the labels — and every
 * row below the bar is compared against it.
 */
function inspectStrip(buffer, bar, scale) {
  const image = decodePng(buffer);
  const px = (value) => Math.round(value * scale);

  const barSampleY = px(bar.top + (bar.height - INSETS_SAMPLE_GAP) / 2);
  const reference = image.at(2, Math.min(barSampleY, image.height - 1));

  const from = px(bar.bottom);
  const rows = [];
  for (let y = from + 1; y < image.height; y += 1) {
    for (const x of [2, Math.round(image.width / 2), image.width - 3]) {
      const colour = image.at(x, y);
      if (distance(colour, reference) > COLOUR_TOLERANCE) {
        rows.push(`y=${Math.round(y / scale)} ${hex(colour)} statt ${hex(reference)}`);
        break;
      }
    }
  }

  const out = {
    referenz: hex(reference),
    zeilen: Math.max(0, image.height - from - 1),
    abweichend: rows.length,
  };
  if (rows.length) {
    out.problem =
      `unter der Leiste ist ${rows.length}px anders gemalt als die Leiste ` +
      `(${rows.slice(0, 2).join(", ")})`;
  }
  return out;
}

const report = [];
for (const mode of MODES) {
  const { page, close } = await openMode(mode);
  page.on("pageerror", (error) =>
    report.push({ mode: mode.name, name: "—", problems: [`Konsole: ${error}`] }),
  );

  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await page.addStyleTag({ content: insetCss(mode.insets) });

  // Confirm the mode is what it claims to be, rather than assuming it.
  const reported = await page.evaluate(() =>
    matchMedia("(display-mode: standalone)").matches,
  );
  if (reported !== mode.standalone) {
    console.error(
      `Modus ${mode.name}: display-mode meldet standalone=${reported}, erwartet ${mode.standalone}`,
    );
    process.exitCode = 1;
  }

  if (await page.locator("#password").count()) {
    await page.fill("#password", PASSWORD);
    await page.click("button[type=submit]");
    await page.waitForTimeout(900);
  }

  for (const [route, name] of SCREENS) {
    await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" });
    await page.addStyleTag({ content: insetCss(mode.insets) });
    await page.waitForTimeout(500);

    const result = await inspect(page, { tabbar: TABBAR, touch: TOUCH, insets: mode.insets });

    // After scrolling to the end, the bar must still be where it was.
    await page.waitForTimeout(200);
    const settled = await page.evaluate(() => {
      const nav = document.querySelector("nav[aria-label='Hauptnavigation']");
      return nav
        ? { bottom: Math.round(nav.getBoundingClientRect().bottom), vh: window.innerHeight }
        : null;
    });
    if (settled && Math.abs(settled.bottom - settled.vh) > 1) {
      result.problems.push(`Leiste wandert beim Scrollen: ${settled.bottom} statt ${settled.vh}`);
    }

    const shot = await page.screenshot({ path: `${OUT}/${mode.name}-${name}.png` });
    if (result.bar) {
      const painted = inspectStrip(shot, result.bar, result.scale ?? 1);
      result.paint = painted;
      if (painted.problem) result.problems.push(painted.problem);
    }
    report.push({ mode: mode.name, name, ...result });
  }
  await close();
}

let currentMode = null;
for (const entry of report) {
  if (entry.mode !== currentMode) {
    currentMode = entry.mode;
    console.log(`\n══ ${currentMode}`);
  }
  const bar = entry.bar
    ? `Leiste ${entry.bar.top}–${entry.bar.bottom} (${entry.bar.height}px, ${entry.bar.position})`
    : entry.noBar
      ? "keine Leiste"
      : "";
  console.log(`\n── ${entry.name}`);
  if (bar) {
    console.log(
      `   ${bar}  Hülle ${entry.shell?.top}–${entry.shell?.bottom} ${entry.shell?.position}  Dokument scrollt: ${entry.bodyScrolls}`,
    );
    if (entry.paint) {
      console.log(
        `   Streifen unter der Leiste: ${entry.paint.zeilen}px, davon ${entry.paint.abweichend}px anders als ${entry.paint.referenz}`,
      );
    }
  }
  if (entry.small?.length) console.log(`   Tap-Ziele: ${entry.small.join(" · ")}`);
  if (entry.underIndicator?.length) console.log(`   Unter dem Home-Indicator: ${entry.underIndicator.join(" · ")}`);
  console.log(entry.problems.length ? `   ✗ ${entry.problems.join("; ")}` : "   ✓ Layout in Ordnung");
}

console.log(`\nScreenshots: ${OUT}`);
const failed = report.filter((entry) => entry.problems.length);
if (failed.length) {
  console.error(`\nFehlerhaft: ${failed.map((entry) => `${entry.mode}/${entry.name}`).join(", ")}`);
  process.exit(1);
}
