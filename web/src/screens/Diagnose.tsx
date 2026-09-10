/**
 * What the device actually reports, so nobody has to debug on a phone.
 *
 * The tab bar sat correctly in a Safari tab and wrongly in the installed
 * app. Every plausible explanation for that difference is a number the
 * browser knows and nobody can see: what the display mode really is, how
 * innerHeight relates to the visual viewport, what the safe-area insets
 * resolve to, where the bar's box ends up and what its computed position
 * is. This screen shows them, refreshes them as the viewport changes, and
 * copies the lot as JSON.
 *
 * It is deliberately plain. It is not a screen anyone looks at twice, and
 * it must keep working when the layout it is meant to explain is broken.
 */

import { useCallback, useEffect, useState } from "react";
import { Card, PrimaryButton, SecondaryButton, TileHeader } from "../components/Tile";
import { Screen } from "../components/Chrome";

type Snapshot = {
  gemessen: string;
  anzeigemodus: {
    /** iOS-only, and the historical answer for "is this the installed app". */
    navigatorStandalone: boolean | null;
    /** The standards answer. iOS 16.4+ agrees with the manifest here. */
    displayModeStandalone: boolean;
    displayModeFullscreen: boolean;
    displayModeMinimalUi: boolean;
    displayModeBrowser: boolean;
    /** What index.html asks iOS for. */
    appleCapable: string | null;
    appleStatusBarStyle: string | null;
    viewportMeta: string | null;
  };
  hoehen: {
    innerHeight: number;
    innerWidth: number;
    outerHeight: number;
    screenHeight: number;
    /** The area actually visible, which is what a fixed shell should fill. */
    visualViewportHeight: number | null;
    visualViewportWidth: number | null;
    visualViewportOffsetTop: number | null;
    visualViewportOffsetLeft: number | null;
    visualViewportScale: number | null;
    /** Layout minus visual. Anything but 0 is the divergence that broke this. */
    differenzLayoutZuVisuell: number | null;
    /** True while the two viewports disagree — zoomed, or keyboard open. */
    viewportsWeichenAb: boolean;
    documentClientHeight: number;
    documentScrollHeight: number;
    documentScrollTop: number;
    /** Non-zero means the document scrolls, which is the thing that must not happen. */
    dokumentScrollt: boolean;
    /** What the three viewport units resolve to right now. */
    einheit100vh: number;
    einheit100dvh: number;
    einheit100svh: number;
    einheit100lvh: number;
  };
  sicherheitsabstaende: {
    top: number;
    bottom: number;
    left: number;
    right: number;
  };
  tableiste: {
    gefunden: boolean;
    position: string | null;
    top: number | null;
    bottom: number | null;
    height: number | null;
    /** The gap between the bar's bottom edge and the visible bottom. Should be 0. */
    abstandZumUnterrand: number | null;
  };
  huelle: {
    position: string | null;
    top: number | null;
    bottom: number | null;
    height: number | null;
    /** Visible height minus the shell's bottom edge. Anything but 0 is a strip. */
    streifenDarunter: number | null;
    /** The band between the bar's bottom edge and the bottom of the screen. */
    streifenUnterDerLeiste: number | null;
    /** Set once lib/visualViewport.ts has measured and taken over. */
    anVisualViewportGebunden: boolean;
  };
  scroller: {
    anzahl: number;
    /** True when something other than the screen scrolls. */
    fremderScroller: boolean;
  };
  geraet: {
    devicePixelRatio: number;
    userAgent: string;
    sprache: string;
    plattform: string;
    online: boolean;
    serviceWorker: string;
  };
};

/** Resolve a CSS length by measuring a probe element, not by parsing text. */
function measure(value: string): number {
  const probe = document.createElement("div");
  probe.style.cssText = `position:absolute;visibility:hidden;height:${value};pointer-events:none`;
  document.body.append(probe);
  const height = probe.getBoundingClientRect().height;
  probe.remove();
  return Math.round(height * 100) / 100;
}

function meta(name: string): string | null {
  return (
    document.querySelector<HTMLMetaElement>(`meta[name="${name}"]`)?.content ?? null
  );
}

function take(): Snapshot {
  const view = window.visualViewport;
  const doc = document.scrollingElement ?? document.documentElement;
  const nav = document.querySelector<HTMLElement>("nav[aria-label='Hauptnavigation']");
  const shell = document.getElementById("root");
  const bar = nav?.getBoundingClientRect() ?? null;
  const shellBox = shell?.getBoundingClientRect() ?? null;
  // The bottom of what can be seen, in the same coordinates the boxes
  // above are measured in — offsetTop matters as soon as the page is
  // zoomed and panned.
  const visibleBottom = view ? view.offsetTop + view.height : window.innerHeight;

  const scrollers = [...document.querySelectorAll<HTMLElement>("*")].filter((el) => {
    const style = getComputedStyle(el);
    return /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 1;
  });

  return {
    gemessen: new Date().toISOString(),
    anzeigemodus: {
      navigatorStandalone:
        "standalone" in navigator
          ? ((navigator as Navigator & { standalone?: boolean }).standalone ?? null)
          : null,
      displayModeStandalone: matchMedia("(display-mode: standalone)").matches,
      displayModeFullscreen: matchMedia("(display-mode: fullscreen)").matches,
      displayModeMinimalUi: matchMedia("(display-mode: minimal-ui)").matches,
      displayModeBrowser: matchMedia("(display-mode: browser)").matches,
      appleCapable: meta("apple-mobile-web-app-capable"),
      appleStatusBarStyle: meta("apple-mobile-web-app-status-bar-style"),
      viewportMeta: meta("viewport"),
    },
    hoehen: {
      innerHeight: window.innerHeight,
      innerWidth: window.innerWidth,
      outerHeight: window.outerHeight,
      screenHeight: window.screen.height,
      visualViewportHeight: view ? Math.round(view.height * 100) / 100 : null,
      visualViewportWidth: view ? Math.round(view.width * 100) / 100 : null,
      visualViewportOffsetTop: view ? Math.round(view.offsetTop * 100) / 100 : null,
      visualViewportOffsetLeft: view ? Math.round(view.offsetLeft * 100) / 100 : null,
      visualViewportScale: view ? view.scale : null,
      differenzLayoutZuVisuell: view
        ? Math.round((window.innerHeight - view.height) * 100) / 100
        : null,
      viewportsWeichenAb: view
        ? Math.abs(window.innerHeight - view.height) > 1 ||
          Math.abs(view.offsetTop) > 1 ||
          Math.abs(view.scale - 1) > 0.01
        : false,
      documentClientHeight: doc.clientHeight,
      documentScrollHeight: doc.scrollHeight,
      documentScrollTop: Math.round(doc.scrollTop),
      dokumentScrollt: doc.scrollHeight > doc.clientHeight + 1,
      einheit100vh: measure("100vh"),
      einheit100dvh: measure("100dvh"),
      einheit100svh: measure("100svh"),
      einheit100lvh: measure("100lvh"),
    },
    sicherheitsabstaende: {
      top: measure("env(safe-area-inset-top, 0px)"),
      bottom: measure("env(safe-area-inset-bottom, 0px)"),
      left: measure("env(safe-area-inset-left, 0px)"),
      right: measure("env(safe-area-inset-right, 0px)"),
    },
    tableiste: {
      gefunden: nav !== null,
      position: nav ? getComputedStyle(nav).position : null,
      top: bar ? Math.round(bar.top * 100) / 100 : null,
      bottom: bar ? Math.round(bar.bottom * 100) / 100 : null,
      height: bar ? Math.round(bar.height * 100) / 100 : null,
      abstandZumUnterrand: bar ? Math.round((visibleBottom - bar.bottom) * 100) / 100 : null,
    },
    huelle: {
      position: shell ? getComputedStyle(shell).position : null,
      top: shellBox ? Math.round(shellBox.top * 100) / 100 : null,
      bottom: shellBox ? Math.round(shellBox.bottom * 100) / 100 : null,
      height: shellBox ? Math.round(shellBox.height * 100) / 100 : null,
      streifenDarunter: shellBox
        ? Math.round((visibleBottom - shellBox.bottom) * 100) / 100
        : null,
      streifenUnterDerLeiste: bar
        ? Math.round((visibleBottom - bar.bottom) * 100) / 100
        : null,
      anVisualViewportGebunden: shell !== null && shell.hasAttribute("data-vv"),
    },
    scroller: {
      anzahl: scrollers.length,
      fremderScroller: scrollers.some((el) => el.dataset.tempoScroll === undefined),
    },
    geraet: {
      devicePixelRatio: window.devicePixelRatio,
      userAgent: navigator.userAgent,
      sprache: navigator.language,
      plattform: navigator.platform,
      online: navigator.onLine,
      serviceWorker:
        "serviceWorker" in navigator
          ? navigator.serviceWorker.controller
            ? "aktiv"
            : "registriert, steuert nicht"
          : "nicht unterstützt",
    },
  };
}

/** The two numbers that decide whether the bottom edge is right. */
function verdict(snapshot: Snapshot): { text: string; tone: string } {
  const gap = snapshot.tableiste.abstandZumUnterrand;
  const shellGap = snapshot.huelle.streifenDarunter;
  if (gap === null) return { text: "Keine Tab-Leiste gefunden.", tone: "var(--st-warn)" };

  if (snapshot.hoehen.viewportsWeichenAb && !snapshot.huelle.anVisualViewportGebunden) {
    // The cause, named: the shell is following the laid-out page while
    // the visible window is somewhere else.
    return {
      text:
        `Layout- und sichtbarer Viewport weichen um ` +
        `${snapshot.hoehen.differenzLayoutZuVisuell} px ab (Zoom ` +
        `${snapshot.hoehen.visualViewportScale}), und die Hülle folgt dem ` +
        `Layout-Viewport. Genau dann wird oben abgeschnitten und unten bleibt ein Streifen.`,
      tone: "var(--st-warn)",
    };
  }
  if (shellGap !== null && Math.abs(shellGap) > 1) {
    // The reported fault: the shell stops short and leaves a band. It is
    // painted in the bar's colour now, so it is not visible — but it is
    // still there, and this is where that stays visible.
    return {
      text:
        `Die Hülle endet ${shellGap} px über dem sichtbaren Rand. Der Streifen ` +
        `darunter ist in der Farbe der Leiste gefüllt, fällt also nicht auf — ` +
        `die Hülle reicht aber nicht bis zum Rand.`,
      tone: "var(--st-caution)",
    };
  }
  if (Math.abs(gap) <= 1) {
    return { text: "Die Leiste schließt bündig mit dem sichtbaren Rand ab.", tone: "var(--st-good)" };
  }
  return {
    text:
      gap > 0
        ? `Die Leiste endet ${gap} px über dem sichtbaren Rand.`
        : `Die Leiste ragt ${Math.abs(gap)} px über den sichtbaren Rand hinaus.`,
    tone: "var(--st-warn)",
  };
}

function Zeile({ label, value }: { label: string; value: unknown }) {
  const shown =
    typeof value === "boolean" ? (value ? "ja" : "nein") : String(value ?? "—");
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-micro" style={{ color: "var(--t-ink-3)" }}>
        {label}
      </span>
      <span
        className="num text-micro"
        style={{ color: "var(--t-ink-2)", textAlign: "right", wordBreak: "break-all" }}
      >
        {shown}
      </span>
    </div>
  );
}

function Block({ title, values }: { title: string; values: Record<string, unknown> }) {
  return (
    <Card>
      <TileHeader title={title} />
      <div className="mt-2">
        {Object.entries(values).map(([key, value]) => (
          <Zeile key={key} label={key} value={value} />
        ))}
      </div>
    </Card>
  );
}

export default function Diagnose() {
  const [snapshot, setSnapshot] = useState<Snapshot>(() => take());
  const [copied, setCopied] = useState<string | null>(null);

  const refresh = useCallback(() => setSnapshot(take()), []);

  useEffect(() => {
    // The interesting values move: the visual viewport changes when the
    // keyboard opens or the page is zoomed, and in standalone that is
    // exactly when a wrongly anchored bar goes astray.
    const view = window.visualViewport;
    window.addEventListener("resize", refresh);
    window.addEventListener("orientationchange", refresh);
    view?.addEventListener("resize", refresh);
    view?.addEventListener("scroll", refresh);
    return () => {
      window.removeEventListener("resize", refresh);
      window.removeEventListener("orientationchange", refresh);
      view?.removeEventListener("resize", refresh);
      view?.removeEventListener("scroll", refresh);
    };
  }, [refresh]);

  const json = JSON.stringify(snapshot, null, 2);

  async function copy() {
    try {
      await navigator.clipboard.writeText(json);
      setCopied("In die Zwischenablage kopiert.");
    } catch {
      // Safari refuses the clipboard outside a user gesture, and in a few
      // other cases besides. Selecting the text is the fallback that
      // always works.
      const field = document.getElementById("diagnose-json") as HTMLTextAreaElement | null;
      field?.select();
      setCopied("Kopieren nicht erlaubt — der Text ist markiert, bitte manuell kopieren.");
    }
  }

  const result = verdict(snapshot);

  return (
    <Screen title="Diagnose" subtitle="Werte des Geräts, für die Fehlersuche">
      <Card>
        <TileHeader title="Befund" />
        <div className="mt-3 flex flex-col gap-2">
          <p className="m-0 text-sub" style={{ color: result.tone }}>
            {result.text}
          </p>
          <p className="m-0 text-micro" style={{ color: "var(--t-ink-3)" }}>
            {snapshot.anzeigemodus.displayModeStandalone ||
            snapshot.anzeigemodus.navigatorStandalone
              ? "Als installierte App gestartet."
              : "Im Browser-Tab gestartet."}{" "}
            {snapshot.hoehen.dokumentScrollt
              ? "Das Dokument scrollt — das ist der Fehlerfall."
              : "Das Dokument scrollt nicht."}
          </p>
        </div>
      </Card>

      <Block title="Anzeigemodus" values={snapshot.anzeigemodus} />
      <Block title="Höhen" values={snapshot.hoehen} />
      <Block title="Sicherheitsabstände" values={snapshot.sicherheitsabstaende} />
      <Block title="Tab-Leiste" values={snapshot.tableiste} />
      <Block title="Hülle" values={snapshot.huelle} />
      <Block title="Scroller" values={snapshot.scroller} />
      <Block title="Gerät" values={snapshot.geraet} />

      <Card>
        <TileHeader title="Weitergeben" />
        <div className="mt-3 flex flex-col gap-3">
          <label className="sr-only" htmlFor="diagnose-json">
            Diagnosewerte als JSON
          </label>
          <textarea
            id="diagnose-json"
            readOnly
            value={json}
            rows={6}
            className="num w-full p-3 text-micro"
            style={{
              borderRadius: "var(--r-md)",
              background: "var(--t-surface-2)",
              color: "var(--t-ink-2)",
              border: "none",
            }}
          />
          {copied ? (
            <p className="m-0 text-micro" style={{ color: "var(--t-ink-3)" }}>
              {copied}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <PrimaryButton onClick={copy}>Als JSON kopieren</PrimaryButton>
            <SecondaryButton onClick={refresh}>Neu messen</SecondaryButton>
          </div>
        </div>
      </Card>
    </Screen>
  );
}
