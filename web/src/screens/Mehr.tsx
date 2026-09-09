/**
 * Screen 6 — Mehr / Einstellungen.
 *
 * Thresholds, credentials as status rather than as secrets, the AI budget,
 * the sync state, and the one action the athlete has when something looks
 * wrong: sync again.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, EmptyState, PrimaryButton, SecondaryButton, TileHeader, TileSkeleton } from "../components/Tile";
import { Screen, StatusBanner } from "../components/Chrome";
import { useResource } from "../lib/useResource";
import { apiSend, ApiError, clearCache } from "../lib/api";
import type { SettingsResponse } from "../lib/types";
import { formatDate, formatEuro, formatNumber, formatPace, formatTime } from "../lib/format";
import { germanUnit, useThresholds } from "../lib/thresholds";

// The API sends the model's key; the screen shows what it means.
const ZONE_MODEL_DE: Record<string, string> = {
  friel_run_lthr: "Friel (Laufen, Schwellen-HF)",
  percent_hr_max: "Prozent der HFmax",
};

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2">
      <span className="text-sub" style={{ color: "var(--t-ink-3)" }}>
        {label}
      </span>
      <span className="num text-sub">{value}</span>
    </div>
  );
}

export default function Mehr() {
  const settings = useResource<SettingsResponse>("/api/settings");
  const { thresholds } = useThresholds();
  const [syncing, setSyncing] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const data = settings.data;

  async function sync() {
    setSyncing(true);
    setProblem(null);
    try {
      await apiSend("/api/sync");
      setTimeout(() => settings.reload(), 1500);
    } catch (cause) {
      setProblem(cause instanceof ApiError ? cause.message : "Der Sync ließ sich nicht starten.");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <Screen title="Mehr" subtitle={data ? `Athlet ${data.intervals_athlete_id}` : undefined}>
      <StatusBanner
        kind={settings.error === null ? "none" : navigator.onLine ? "error" : "offline"}
        receivedAt={settings.receivedAt}
        detail={settings.error?.message}
        onRetry={settings.reload}
      />

      <Card>
        <TileHeader title="Schwellenwerte" />
        <div className="mt-1">
          {settings.loading ? <TileSkeleton lines={3} /> : null}
          {data ? (
            <>
              <Row label="HFmax" value={data.hr_max ? `${formatNumber(data.hr_max)} bpm` : "—"} />
              <Row label="Ruhe-HF" value={data.hr_rest ? `${formatNumber(data.hr_rest)} bpm` : "—"} />
              <Row label="Schwellen-HF" value={data.lthr ? `${formatNumber(data.lthr)} bpm` : "—"} />
              <Row
                label="Schwellenpace"
                value={
                  data.threshold_pace_s_per_km
                    ? `${formatPace(data.threshold_pace_s_per_km)} min/km`
                    : "—"
                }
              />
              <Row label="Zonenmodell" value={ZONE_MODEL_DE[data.zone_model] ?? data.zone_model} />
              {data.updated_at ? (
                <Row label="Zuletzt geändert" value={formatDate(data.updated_at)} />
              ) : null}
            </>
          ) : null}
        </div>
      </Card>

      <Card>
        <TileHeader title="Mindesthistorien" />
        <div className="mt-1">
          {!thresholds ? (
            <EmptyState message="Die Schwellen konnten nicht geladen werden." />
          ) : (
            Object.entries(thresholds.minimum_history).map(([key, entry]) => (
              <Row
                key={key}
                label={entry.label_de}
                value={`${entry.required} ${germanUnit(entry.unit, entry.required, "nominative")}`}
              />
            ))
          )}
        </div>
      </Card>

      <Card>
        <TileHeader title="Zugänge" />
        <div className="mt-1">
          {data ? (
            <>
              <Row
                label="intervals.icu"
                value={
                  data.intervals.valid
                    ? `hinterlegt · …${data.intervals.last4 ?? ""}`
                    : "nicht hinterlegt"
                }
              />
              <Row
                label="Anthropic"
                value={
                  data.anthropic.valid
                    ? `hinterlegt · …${data.anthropic.last4 ?? ""}`
                    : "nicht hinterlegt"
                }
              />
              <Row label="Garmin direkt" value={data.garmin_direct_enabled ? "an" : "aus"} />
              <p className="m-0 pt-1 text-micro" style={{ color: "var(--t-ink-3)" }}>
                Schlüssel stehen in der Umgebung und kommen aus keiner Antwort zurück.
              </p>
            </>
          ) : null}
        </div>
      </Card>

      <Card>
        <TileHeader title="KI-Verbrauch" />
        <div className="mt-1">
          {data ? (
            <>
              <Row label="Monat" value={data.ai_usage.month} />
              <Row
                label="Verbraucht"
                value={`${formatEuro(data.ai_usage.cost_eur)} von ${formatEuro(data.ai_usage.budget_eur)}`}
              />
              <Row label="Aufrufe" value={formatNumber(data.ai_usage.calls)} />
              <Row label="Modell (täglich)" value={data.ai_model_daily} />
              <Row label="Modell (Planung)" value={data.ai_model_planning} />
            </>
          ) : null}
        </div>
      </Card>

      <Card>
        <TileHeader title="Sync" />
        <div className="mt-1 flex flex-col gap-2">
          {data?.sync.sources.length === 0 ? (
            <EmptyState message="Noch kein Sync gelaufen." />
          ) : null}
          {data?.sync.sources.map((source) => (
            <div key={source.source} className="flex flex-col">
              <div className="flex items-baseline justify-between">
                <span className="text-sub">{source.source}</span>
                <span
                  className="num text-micro"
                  style={{
                    color:
                      source.status === "ok"
                        ? "var(--st-good)"
                        : source.status === "failed"
                          ? "var(--st-warn)"
                          : "var(--t-ink-3)",
                  }}
                >
                  {source.running ? "läuft" : (source.status ?? "—")}
                  {source.last_success_at ? ` · ${formatTime(source.last_success_at)}` : ""}
                </span>
              </div>
              {source.detail ? (
                <span className="text-micro" style={{ color: "var(--t-ink-3)" }}>
                  {source.detail}
                </span>
              ) : null}
            </div>
          ))}
          {data && data.fit_files_pending > 0 ? (
            <p className="m-0 text-micro" style={{ color: "var(--st-caution)" }}>
              {data.fit_files_pending} FIT-Datei(en) im Volume gehören zu keiner Aktivität.
            </p>
          ) : null}
          {problem ? (
            <p className="m-0 text-sub" style={{ color: "var(--st-warn)" }}>
              {problem}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <PrimaryButton onClick={sync} disabled={syncing}>
              {syncing ? "Startet …" : "Jetzt synchronisieren"}
            </PrimaryButton>
            <SecondaryButton
              onClick={() => {
                clearCache();
                settings.reload();
              }}
            >
              Zwischenspeicher leeren
            </SecondaryButton>
          </div>
        </div>
      </Card>

      <Card>
        <TileHeader title="Aktivitäten" />
        <div className="mt-3">
          <Link to="/activities" style={{ textDecoration: "none" }}>
            <PrimaryButton>Alle Aktivitäten</PrimaryButton>
          </Link>
        </div>
      </Card>
    </Screen>
  );
}
