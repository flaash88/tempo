/** The activity list, and one activity in detail. Screen 2 of the design. */

import { Link, useParams } from "react-router-dom";
import {
  BuildingState,
  Card,
  EmptyState,
  PrimaryButton,
  StaleMark,
  TileHeader,
  TileSkeleton,
} from "../components/Tile";
import { Screen, StatusBanner } from "../components/Chrome";
import { useResource } from "../lib/useResource";
import { tileState } from "../lib/state";
import { apiSend, ApiError } from "../lib/api";
import { useState } from "react";
import { AiText } from "./Plan";
import type { ActivityDetail, ActivityList, AiAnswer } from "../lib/types";
import {
  formatDate,
  formatDistance,
  formatDuration,
  formatNumber,
  formatPace,
} from "../lib/format";

export function ActivityListScreen() {
  const list = useResource<ActivityList>("/api/activities?limit=50");
  const data = list.data;

  return (
    <Screen title="Aktivitäten" subtitle={data ? `${data.total} insgesamt` : undefined}>
      <StatusBanner
        kind={list.error === null ? "none" : navigator.onLine ? "error" : "offline"}
        receivedAt={list.receivedAt}
        detail={list.error?.message}
        onRetry={list.reload}
      />
      <Card>
        <div>
          {list.loading ? <TileSkeleton lines={4} /> : null}
          {data && data.activities.length === 0 ? (
            <EmptyState message="Noch nichts aufgezeichnet. Nach dem ersten Sync steht hier etwas." />
          ) : null}
          {data?.activities.map((activity) => (
            <Link
              key={activity.id}
              to={`/activity/${activity.id}`}
              className="flex items-baseline justify-between gap-3 py-3"
              style={{ borderTop: "1px solid var(--t-line)", textDecoration: "none", color: "inherit" }}
            >
              <div className="flex flex-col">
                <span className="text-body">{activity.sport}</span>
                <span className="num text-micro" style={{ color: "var(--t-ink-3)" }}>
                  {formatDate(activity.start_local)}
                </span>
              </div>
              <span className="num text-sub" style={{ color: "var(--t-ink-2)" }}>
                {formatDistance(activity.distance_m)} · {formatDuration(activity.moving_s)}
              </span>
            </Link>
          ))}
        </div>
      </Card>
    </Screen>
  );
}

export function ActivityDetailScreen() {
  const { id } = useParams();
  const detail = useResource<ActivityDetail>(id ? `/api/activities/${id}` : null, [id]);
  const [answer, setAnswer] = useState<AiAnswer | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [thinking, setThinking] = useState(false);
  const data = detail.data;

  const trimp = tileState<number>({
    envelope: data?.trimp,
    loading: detail.loading,
    error: !data && detail.error !== null,
  });
  const decoupling = tileState<number>({
    envelope: data?.decoupling,
    loading: detail.loading,
    error: !data && detail.error !== null,
  });

  async function interpret() {
    if (!id) return;
    setThinking(true);
    setProblem(null);
    try {
      setAnswer(await apiSend<AiAnswer>(`/api/ai/activity/${id}`));
    } catch (cause) {
      setProblem(cause instanceof ApiError ? cause.message : "Die Einordnung ist fehlgeschlagen.");
    } finally {
      setThinking(false);
    }
  }

  const zoneTotal = (data?.zones ?? []).reduce((sum, share) => sum + share.seconds, 0);

  return (
    <Screen
      title={data ? data.sport : "Aktivität"}
      subtitle={data ? formatDate(data.start_local) : undefined}
    >
      <StatusBanner
        kind={detail.error === null ? "none" : navigator.onLine ? "error" : "offline"}
        receivedAt={detail.receivedAt}
        detail={detail.error?.message}
        onRetry={detail.reload}
      />

      <Card>
        <TileHeader title="Einheit" right={trimp.state === "stale" ? <StaleMark date={trimp.lastDataPoint} /> : null} />
        <div className="mt-3">
          {detail.loading ? <TileSkeleton lines={3} /> : null}
          {data ? (
            <div className="grid grid-cols-2 gap-3">
              <Figure label="Distanz" value={formatDistance(data.distance_m)} />
              <Figure label="Dauer" value={formatDuration(data.moving_s)} />
              <Figure
                label="Pace"
                value={data.avg_pace_s_per_km ? `${formatPace(data.avg_pace_s_per_km)} /km` : "—"}
              />
              <Figure label="Ø HF" value={data.avg_hr ? `${formatNumber(data.avg_hr)} bpm` : "—"} />
            </div>
          ) : null}
        </div>
      </Card>

      <Card>
        <TileHeader title="Belastung" />
        <div className="mt-3 grid grid-cols-2 gap-3">
          {trimp.state === "building" ? <BuildingState metric="form" view={trimp} /> : null}
          {trimp.value !== null ? (
            <Figure label="TRIMP" value={formatNumber(trimp.value, 1)} />
          ) : null}
          {data?.hr_tss.value !== null && data?.hr_tss.value !== undefined ? (
            <Figure label="hrTSS" value={formatNumber(data.hr_tss.value, 1)} />
          ) : null}
          {decoupling.value !== null ? (
            <Figure label="Decoupling" value={`${formatNumber(decoupling.value, 1)} %`} />
          ) : null}
        </div>
      </Card>

      {zoneTotal > 0 ? (
        <Card>
          <TileHeader title="Zeit in Zone" />
          <div className="mt-3 flex flex-col gap-2">
            <div className="flex h-3 overflow-hidden" style={{ borderRadius: "var(--r-pill)" }}>
              {data?.zones.map((share) => (
                <div
                  key={share.zone}
                  style={{
                    width: `${(share.seconds / zoneTotal) * 100}%`,
                    background: `var(--z${share.zone})`,
                  }}
                />
              ))}
            </div>
            <div className="flex flex-wrap gap-2">
              {data?.zones.map((share) => (
                <span
                  key={share.zone}
                  className="num text-micro"
                  style={{ color: `var(--z${share.zone})` }}
                >
                  Z{share.zone} {formatDuration(share.seconds)}
                </span>
              ))}
            </div>
          </div>
        </Card>
      ) : null}

      {data && data.laps.length > 0 ? (
        <Card>
          <TileHeader title="Runden" />
          <div className="mt-1">
            {data.laps.map((lap) => (
              <div
                key={lap.index}
                className="flex items-baseline justify-between py-2"
                style={{ borderTop: "1px solid var(--t-line)" }}
              >
                <span className="num text-sub" style={{ color: "var(--t-ink-3)" }}>
                  {lap.index + 1}
                </span>
                <span className="num text-sub">
                  {formatDistance(lap.distance_m)} · {formatDuration(lap.duration_s)}
                  {lap.avg_pace_s_per_km ? ` · ${formatPace(lap.avg_pace_s_per_km)}` : ""}
                </span>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      <Card>
        <TileHeader title="Einordnung" />
        <div className="mt-3 flex flex-col gap-3">
          {answer ? <AiText answer={answer} /> : null}
          {problem ? (
            <p className="m-0 text-sub" style={{ color: "var(--st-warn)" }}>
              {problem}
            </p>
          ) : null}
          <PrimaryButton onClick={interpret} disabled={thinking}>
            {thinking ? "Denkt nach …" : "Einheit einordnen"}
          </PrimaryButton>
        </div>
      </Card>
    </Screen>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="num text-metric-sm" style={{ fontWeight: 500 }}>
        {value}
      </span>
      <span className="label-micro">{label}</span>
    </div>
  );
}
