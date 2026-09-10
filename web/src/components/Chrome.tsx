/**
 * The frame: the offline banner, the tab bar, and the safe areas.
 *
 * The banner is the screen-level half of the offline story — the tiles say
 * how old each number is, the banner says when the screen as a whole last
 * reached the server, and offers the one action that helps.
 */

import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { formatClock, formatRelative } from "../lib/format";

export function Screen({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  /**
   * Pinned below the scrolling area, inside the shell.
   *
   * A sibling in normal flow, not an overlay: content cannot end up
   * underneath something that takes its own space. The Coach screen's
   * composer is the one that needs this — with the keyboard open it has
   * to sit at the bottom of the visible window while the conversation
   * scrolls above it.
   */
  footer?: ReactNode;
}) {
  return (
    <>
      <div
        data-tempo-scroll
        // The height and the scrolling come from the [data-tempo-scroll]
        // rule: this is the flex child that fills the shell, and it is the
        // only thing on the screen that scrolls. It must not be a
        // min-height box — one of those grows with its content, never
        // scrolls itself, and hands the scrolling to the document, which is
        // where iOS starts moving the fixed tab bar around.
        className="flex flex-col gap-4"
        style={{
          paddingTop: "calc(var(--inset-top) + var(--sp-4))",
          // No room reserved for the tab bar any more: it is a sibling in
          // normal flow below this box, not something floating over it.
          // What is left is breathing room at the end of the list.
          paddingBottom: "var(--sp-6)",
          paddingLeft: "calc(var(--inset-left) + var(--sp-4))",
          paddingRight: "calc(var(--inset-right) + var(--sp-4))",
        }}
      >
        <header className="flex flex-col gap-1">
          <h1 className="m-0 text-metric-xs" style={{ fontWeight: 500 }}>
            {title}
          </h1>
          {subtitle ? (
            <p className="num m-0 text-sub" style={{ color: "var(--t-ink-3)" }}>
              {subtitle}
            </p>
          ) : null}
        </header>
        {children}
      </div>
      {footer}
    </>
  );
}

export type BannerKind = "offline" | "error" | "none";

/**
 * The screen-wide state, with the timestamp that makes it honest.
 *
 * Offline is not an error: the data on screen is real, it is simply from
 * earlier, and the banner says from when. A failed request over a working
 * connection is an error and says so differently.
 */
export function StatusBanner({
  kind,
  receivedAt,
  detail,
  onRetry,
  busy,
}: {
  kind: BannerKind;
  receivedAt: number | null;
  detail?: string;
  onRetry?: () => void;
  busy?: boolean;
}) {
  if (kind === "none") return null;
  const offline = kind === "offline";
  const stamp =
    receivedAt === null
      ? "kein gespeicherter Stand"
      : `Stand ${formatClock(receivedAt)} · ${formatRelative(receivedAt)}`;
  return (
    <div
      role="status"
      className="flex items-center justify-between gap-3 px-3 py-2"
      style={{
        borderRadius: "var(--r-md)",
        background: offline ? "var(--st-neutral-soft)" : "var(--st-warn-soft)",
        boxShadow: `inset 0 0 0 1px ${offline ? "var(--t-line-strong)" : "var(--st-warn)"}`,
      }}
    >
      <div className="flex flex-col">
        <span
          className="text-sub"
          style={{ color: offline ? "var(--t-ink-2)" : "var(--st-warn)" }}
        >
          {offline ? "Offline" : (detail ?? "Verbindung fehlgeschlagen")}
        </span>
        <span className="num text-micro" style={{ color: "var(--t-ink-3)" }}>
          {stamp}
        </span>
      </div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          disabled={busy}
          className="px-3 text-sub disabled:opacity-50"
          style={{
            minHeight: "var(--touch)",
            borderRadius: "var(--r-md)",
            color: "var(--t-accent-ink)",
            boxShadow: "inset 0 0 0 1px var(--t-accent-line)",
          }}
        >
          {busy ? "…" : "Erneut"}
        </button>
      ) : null}
    </div>
  );
}

const TABS = [
  { to: "/", label: "Heute", icon: "sun" },
  { to: "/trends", label: "Trends", icon: "chart" },
  { to: "/plan", label: "Plan", icon: "calendar" },
  { to: "/coach", label: "Coach", icon: "sparkle" },
  { to: "/more", label: "Mehr", icon: "dots" },
] as const;

/** Phosphor-style glyphs, inline, on currentColor. */
function Icon({ name }: { name: (typeof TABS)[number]["icon"] }) {
  const common = {
    width: 22,
    height: 22,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (name) {
    case "sun":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4" />
        </svg>
      );
    case "chart":
      return (
        <svg {...common}>
          <path d="M4 19V5M4 19h16" />
          <polyline points="7,15 11,10 14,13 19,6" />
        </svg>
      );
    case "calendar":
      return (
        <svg {...common}>
          <rect x="3.5" y="5" width="17" height="15" rx="2.5" />
          <path d="M3.5 9.5h17M8 3.5v3M16 3.5v3" />
        </svg>
      );
    case "sparkle":
      return (
        <svg {...common}>
          <path d="M12 4l1.8 4.7L18.5 10.5 13.8 12.3 12 17l-1.8-4.7L5.5 10.5l4.7-1.8z" />
          <path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" />
        </svg>
      );
    case "dots":
      return (
        <svg {...common}>
          <circle cx="6" cy="12" r="1.3" />
          <circle cx="12" cy="12" r="1.3" />
          <circle cx="18" cy="12" r="1.3" />
        </svg>
      );
  }
}

export function TabBar() {
  return (
    <nav
      aria-label="Hauptnavigation"
      // Not positioned at all: the bar is the last child of the fixed
      // shell, so it sits at its bottom because that is where the layout
      // puts it. Nothing here resolves against a viewport.
      //
      // It used to be `fixed bottom-0`, which is the arrangement iOS
      // standalone gets wrong — the coordinate is resolved against a
      // viewport that does not match the visible area, and the bar creeps
      // upwards. In a Safari tab the same markup was fine, which is
      // exactly the difference that was reported.
      className="z-10 flex shrink-0 justify-around"
      style={{
        // The bar is one touch target tall, and the home indicator's strip
        // is added below it as padding rather than taken out of it.
        height: "calc(var(--tabbar-h) + var(--inset-bottom))",
        paddingBottom: "var(--inset-bottom)",
        paddingLeft: "var(--inset-left)",
        paddingRight: "var(--inset-right)",
        background: "color-mix(in srgb, var(--t-bg-deep) 92%, transparent)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        boxShadow: "inset 0 1px 0 0 var(--t-line)",
      }}
    >
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.to === "/"}
          className="flex flex-1 flex-col items-center justify-center gap-1"
          style={({ isActive }) => ({
            // The whole cell is the target, not just the glyph.
            minHeight: "var(--touch)",
            height: "100%",
            color: isActive ? "var(--t-accent-ink)" : "var(--t-ink-3)",
            textDecoration: "none",
          })}
        >
          <Icon name={tab.icon} />
          <span className="text-micro">{tab.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}
