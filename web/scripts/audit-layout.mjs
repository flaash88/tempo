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
// Zoom levels every screen is measured at. The fault only appears above
// 1.0: the layout viewport stays the whole page while the visual one
// shrinks, and a shell that follows the first reaches past the second.
// A run at 1.0 alone is a run that stops before the fault — which is
// what the previous one did.
const ZOOMS = [1, 1.5, 2];
// How far the iOS keyboard shrinks the visual viewport from below, near
// enough. Simulated, see the note where it is used.
const KEYBOARD_HEIGHT = 320;
// Every screen with a field. The third entry says whether the screen owes
// a composer pinned to the bottom — only the chat does; the others just
// have to get the bar out of the way.
const KEYBOARD_SCREENS = [
  ["/coach", "Coach", true],
  ["/plan", "Plan (Formular)", false],
  ["/diagnose", "Diagnose", false],
];
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
/**
 * Neutralise the binding, leaving the shell on inset: 0.
 *
 * That is the behaviour before this fix, and the zoom passes must go red
 * under it — otherwise the zoom checks are unfalsifiable here, which is
 * the trap the 1.0-only run fell into.
 *
 * Deliberately CSS rather than deleting window.visualViewport: taking the
 * API away would also blind the audit's own measurements, and a check
 * that loses its reference along with the feature proves nothing. Tried
 * that first; it reported "Zoom 1" at page scale 2.
 */
const UNBIND_CSS =
  process.env.TEMPO_DISABLE_VV === "1"
    ? "#root[data-vv]{inset:0!important;top:auto!important;left:auto!important;" +
      "width:auto!important;height:auto!important}"
    : "";
const simulationCss = SIMULATED_STRIP
  ? `#root{bottom:${SIMULATED_STRIP}px!important}` +
    // The bound shell sets its own height, so shortening it by moving
    // `bottom` no longer reaches it. Caught by this run going green when
    // it had to stay red — a simulation that stops simulating is the
    // same failure as a check that stops checking.
    `#root[data-vv]{height:calc(var(--vv-height) - ${SIMULATED_STRIP}px)!important}`
  : "";

const insetCss = (insets) =>
  `:root{--inset-top:${insets.top}px!important;--inset-bottom:${insets.bottom}px!important;` +
  `--inset-left:${insets.left}px!important;--inset-right:${insets.right}px!important}` +
  simulationCss +
  UNBIND_CSS;

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
      const doc = document.scrollingElement ?? document.documentElement;

      // Everything about the bottom edge is measured against the *visual*
      // viewport, not innerHeight. At any zoom other than 1 those are two
      // different things, and innerHeight is the wrong one: it is the
      // layout viewport, which is exactly what the shell used to follow
      // and what left a band below the bar. Measuring against it would
      // have kept the audit green through the whole fault.
      const view = window.visualViewport;
      const vw = view ? view.width : window.innerWidth;
      const visualTop = view ? view.offsetTop : 0;
      const visualBottom = view ? view.offsetTop + view.height : window.innerHeight;
      const vh = visualBottom - visualTop;
      out.visual = {
        top: Math.round(visualTop),
        bottom: Math.round(visualBottom),
        height: Math.round(vh),
        scale: view ? Math.round(view.scale * 100) / 100 : 1,
        layoutHeight: window.innerHeight,
      };

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
        gebunden: shell.hasAttribute("data-vv"),
      };
      if (Math.abs(shellBox.top - visualTop) > 1) {
        out.problems.push(
          `Hülle beginnt bei ${Math.round(shellBox.top)}, sichtbar ab ${Math.round(visualTop)}` +
            ` — ${Math.round(visualTop - shellBox.top)}px oben abgeschnitten`,
        );
      }
      if (Math.abs(shellBox.bottom - visualBottom) > 1) {
        out.problems.push(
          `Hülle endet bei ${Math.round(shellBox.bottom)}, sichtbar bis ${Math.round(visualBottom)}` +
            ` — ${Math.round(visualBottom - shellBox.bottom)}px Streifen darunter`,
        );
      }
      if (Math.abs(shellBox.height - vh) > 1) {
        out.problems.push(`Hülle ${Math.round(shellBox.height)} hoch statt ${Math.round(vh)}`);
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
      if (Math.abs(bar.bottom - visualBottom) > 1) {
        out.problems.push(
          `Leiste endet bei ${Math.round(bar.bottom)}, sichtbar bis ${Math.round(visualBottom)}`,
        );
      }
      if (Math.abs(bar.height - (tabbar + insets.bottom)) > 1) {
        out.problems.push(`Leistenhöhe ${Math.round(bar.height)} statt ${tabbar + insets.bottom}`);
      }
      // The labels must clear the home indicator's strip.
      const label = nav.querySelector("span");
      if (label && label.getBoundingClientRect().bottom > visualBottom - insets.bottom + 0.5) {
        out.problems.push("Beschriftung ragt in den Home-Indicator");
      }

      // 4b. Whatever lies between the bar's bottom edge and the bottom of
      //     the screen must be the bar. Anything else — the shell, the
      //     body, nothing at all — is a strip the page shows through.
      const visualLeft = view ? view.offsetLeft : 0;
      const sampleX = [
        visualLeft + 2,
        Math.round(visualLeft + vw / 2),
        Math.round(visualLeft + vw - 3),
      ];
      out.strip = { from: Math.round(bar.bottom), to: Math.round(visualBottom), uncovered: [] };
      for (let y = Math.ceil(bar.bottom) + 1; y < visualBottom; y += 2) {
        for (const x of sampleX) {
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

      // 5. Nothing horizontal — measured against the layout viewport.
      //    Panning sideways at a zoom above 1 is the point of zooming;
      //    what must not happen is the document being wider than the
      //    page itself.
      if (doc.scrollWidth > window.innerWidth + 1) {
        out.problems.push(`horizontaler Overflow: ${doc.scrollWidth} > ${window.innerWidth}`);
      }

      // 6. Tap targets, and nothing hiding under the home indicator.
      const interactive = [...document.querySelectorAll("button, a, input, textarea, [role=tab]")];
      const onScreen = interactive
        .map((el) => ({ el, r: el.getBoundingClientRect() }))
        .filter(({ r }) => r.width > 0 && r.top < visualBottom);

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

      const guard = visualBottom - insets.bottom;
      out.underIndicator = onScreen
        .filter(({ el, r }) => {
          if (nav.contains(el) || r.bottom <= guard) return false;
          // Overlapping the band on paper is not the same as being seen
          // in it: the screen clips what runs past its bottom, and the
          // bar covers the band itself. So ask what is actually painted
          // at the point in question.
          const y = Math.min(Math.max(guard + 2, r.top + 1), visualBottom - 1);
          const x = Math.min(Math.max(r.left + r.width / 2, visualLeft + 1), visualLeft + vw - 1);
          const hit = document.elementFromPoint(x, y);
          return hit !== null && (hit === el || el.contains(hit));
        })
        .map(({ el, r }) => `${el.tagName} "${(el.textContent || "").trim().slice(0, 24)}" bis ${Math.round(r.bottom)}`);
      if (out.underIndicator.length) out.problems.push("etwas liegt unter dem Home-Indicator");

      // 7. Scroll to the end; the bar must not follow.
      const screen = document.querySelector("[data-tempo-scroll]");
      if (screen) screen.scrollTop = screen.scrollHeight;
      out.scale = window.devicePixelRatio;
      out.viewport = { width: Math.round(vw), height: Math.round(vh) };
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

  const cdp = await page.context().newCDPSession(page);

  for (const [route, name] of SCREENS) {
    await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" });
    await page.addStyleTag({ content: insetCss(mode.insets) });
    await page.waitForTimeout(400);

    for (const zoom of ZOOMS) {
      await cdp.send("Emulation.setPageScaleFactor", { pageScaleFactor: zoom });
      await page.waitForTimeout(zoom === 1 ? 300 : 450);

      const result = await inspect(page, {
        tabbar: TABBAR,
        touch: TOUCH,
        insets: mode.insets,
      });

      // After scrolling to the end, the bar must still be where it was.
      await page.waitForTimeout(150);
      const settled = await page.evaluate(() => {
        const nav = document.querySelector("nav[aria-label='Hauptnavigation']");
        if (!nav) return null;
        const view = window.visualViewport;
        return {
          bottom: Math.round(nav.getBoundingClientRect().bottom),
          visible: Math.round(view ? view.offsetTop + view.height : window.innerHeight),
        };
      });
      if (settled && Math.abs(settled.bottom - settled.visible) > 1) {
        result.problems.push(
          `Leiste wandert beim Scrollen: ${settled.bottom} statt ${settled.visible}`,
        );
      }

      const label = zoom === 1 ? name : `${name}@${zoom}x`;
      const shot = await page.screenshot({ path: `${OUT}/${mode.name}-${label}.png` });
      // The painted check only at 1.0: above it the screenshot is scaled
      // and the mapping from CSS pixels to image pixels stops being the
      // device pixel ratio.
      if (result.bar && zoom === 1) {
        const painted = inspectStrip(shot, result.bar, result.scale ?? 1);
        result.paint = painted;
        if (painted.problem) result.problems.push(painted.problem);
      }
      report.push({ mode: mode.name, name: label, zoom, ...result });
    }

    await cdp.send("Emulation.setPageScaleFactor", { pageScaleFactor: 1 });
  }

  // The keyboard, as far as it goes without a device.
  //
  // Every screen that has a field gets the same treatment, because the
  // rule is the same everywhere: the bar stands down while something
  // covers the bottom. Only the Coach screen also has a composer to pin
  // there, so only it is checked for one.
  //
  // iOS shrinks the visual viewport from below when the keyboard opens
  // and leaves the layout viewport alone — the same divergence zoom
  // produces, from the other end. Chromium has no keyboard to open, so
  // the reported height is overridden and the resize event fired: what
  // this checks is that the shell follows a shrunk visual viewport, not
  // that WebKit shrinks it. The expected height is the injected constant,
  // so the assertion cannot agree with itself by accident.
  for (const [route, label, expectComposer] of KEYBOARD_SCREENS) {
  await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" });
  await page.addStyleTag({ content: insetCss(mode.insets) });
  await page.waitForTimeout(400);
  if (route === "/plan") {
    // The form only exists once it has been opened.
    const opener = page.locator("text=Einheit anlegen");
    if (await opener.count()) {
      await opener.first().click();
      await page.waitForTimeout(300);
    }
  }
  const keyboard = await page.evaluate(({ keyboardHeight, expectComposer }) => {
    const view = window.visualViewport;
    if (!view) return { skipped: "kein visualViewport" };

    const full = view.height;
    const shrunk = full - keyboardHeight;
    Object.defineProperty(view, "height", { configurable: true, get: () => shrunk });
    view.dispatchEvent(new Event("resize"));

    return new Promise((resolve) =>
      requestAnimationFrame(() =>
        requestAnimationFrame(() =>
          // Two frames for the measurement, a third for React's render.
          requestAnimationFrame(() => {
            const problems = [];
            const shell = document.getElementById("root").getBoundingClientRect();
            const visibleBottom = view.offsetTop + view.height;

            // 1. The shell ends at the top of the keyboard.
            if (Math.abs(shell.height - shrunk) > 1) {
              problems.push(`Hülle ${Math.round(shell.height)} statt ${Math.round(shrunk)}`);
            }

            // 2. The tab bar is gone. Over the keyboard belongs the field.
            const nav = document.querySelector("nav[aria-label='Hauptnavigation']");
            if (nav && nav.getBoundingClientRect().height > 0) {
              problems.push("die Tab-Leiste steht noch über der Tastatur");
            }

            // 3. The composer sits at the bottom of what is visible.
            const field = document.getElementById("coach-question");
            const composer = field?.closest("div");
            const box = composer?.getBoundingClientRect();
            if (!expectComposer) {
              // Nothing to pin here; the screens with ordinary fields only
              // owe the first two promises.
              resolve({
                erwartet: Math.round(shrunk),
                huelle: Math.round(shell.height),
                leisteSichtbar: Boolean(nav && nav.getBoundingClientRect().height > 0),
                eingabefeldUnterkante: null,
                sichtbarBis: Math.round(visibleBottom),
                problems,
              });
              return;
            }
            if (!box) {
              problems.push("kein Eingabefeld gefunden");
            } else if (Math.abs(box.bottom - visibleBottom) > 2) {
              problems.push(
                `Eingabefeld endet bei ${Math.round(box.bottom)}, sichtbar bis ${Math.round(visibleBottom)}`,
              );
            }

            // 4. Nothing of the conversation is hidden underneath it. The
            //    composer takes its own space rather than overlaying, so
            //    what would break this is content painted behind it.
            if (box) {
              for (const x of [
                Math.round(view.offsetLeft + 8),
                Math.round(view.offsetLeft + view.width / 2),
              ]) {
                for (const y of [box.top + 4, (box.top + visibleBottom) / 2]) {
                  const hit = document.elementFromPoint(x, y);
                  if (hit && !composer.contains(hit) && hit !== composer) {
                    problems.push(
                      `unter dem Eingabefeld liegt ${hit.tagName}${hit.id ? "#" + hit.id : ""}`,
                    );
                  }
                }
              }
            }

            resolve({
              erwartet: Math.round(shrunk),
              huelle: Math.round(shell.height),
              leisteSichtbar: Boolean(nav && nav.getBoundingClientRect().height > 0),
              eingabefeldUnterkante: box ? Math.round(box.bottom) : null,
              sichtbarBis: Math.round(visibleBottom),
              problems,
            });
          }),
        ),
      ),
    );
  }, { keyboardHeight: KEYBOARD_HEIGHT, expectComposer });

  report.push({
    mode: mode.name,
    name: `${label} + Tastatur (simuliert)`,
    keyboard,
    problems: keyboard.skipped ? [keyboard.skipped] : keyboard.problems,
  });
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
  if (entry.keyboard && !entry.keyboard.skipped) {
    console.log(
      `   Hülle ${entry.keyboard.huelle}px (erwartet ${entry.keyboard.erwartet})` +
        `  Leiste sichtbar: ${entry.keyboard.leisteSichtbar}` +
        `  Eingabefeld bis ${entry.keyboard.eingabefeldUnterkante}, sichtbar bis ${entry.keyboard.sichtbarBis}`,
    );
  }
  if (bar) {
    console.log(
      `   ${bar}  Hülle ${entry.shell?.top}–${entry.shell?.bottom}` +
        `  sichtbar ${entry.visual?.top}–${entry.visual?.bottom} (Zoom ${entry.visual?.scale},` +
        ` Layout ${entry.visual?.layoutHeight})  gebunden: ${entry.shell?.gebunden}`,
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
