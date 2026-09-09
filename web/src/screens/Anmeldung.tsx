/**
 * The login. One password, and — when the deployment's hash never arrived
 * intact — the server's own explanation rather than "Passwort falsch".
 */

import { useState } from "react";
import { Card, PrimaryButton, TileHeader } from "../components/Tile";
import { apiSend, ApiError } from "../lib/api";

export default function Anmeldung({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [configuration, setConfiguration] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      await apiSend("/api/auth/login", { body: { password } });
      onDone();
    } catch (cause) {
      if (cause instanceof ApiError) {
        // 503 is the server saying the configuration is wrong, not the
        // password — the difference decides where the athlete looks next.
        setConfiguration(cause.status === 503);
        setProblem(cause.message);
      } else {
        setProblem("Die Anmeldung ist fehlgeschlagen.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      data-tempo-scroll
      className="flex flex-col justify-center gap-4 px-4"
      style={{
        paddingTop: "calc(var(--inset-top) + var(--sp-6))",
        // No tab bar here, but the home indicator is still in the way.
        paddingBottom: "calc(var(--inset-bottom) + var(--sp-6))",
        paddingLeft: "calc(var(--inset-left) + var(--sp-4))",
        paddingRight: "calc(var(--inset-right) + var(--sp-4))",
      }}
    >
      <Card>
        <TileHeader title="Tempo" />
        <form onSubmit={submit} className="mt-4 flex flex-col gap-3">
          <label htmlFor="password" className="text-sub" style={{ color: "var(--t-ink-2)" }}>
            Passwort
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full p-3 text-body"
            style={{
              borderRadius: "var(--r-md)",
              background: "var(--t-surface-2)",
              color: "var(--t-ink)",
              border: "none",
              minHeight: "var(--touch)",
            }}
          />
          {problem ? (
            <div className="flex flex-col gap-1">
              <p className="m-0 text-sub" style={{ color: "var(--st-warn)" }}>
                {problem}
              </p>
              {configuration ? (
                <p className="m-0 text-micro" style={{ color: "var(--t-ink-3)" }}>
                  Das ist ein Konfigurationsfehler, kein falsches Passwort — siehe
                  „.env-Fallstricke" im README.
                </p>
              ) : null}
            </div>
          ) : null}
          <PrimaryButton type="submit" disabled={busy || password.length === 0}>
            {busy ? "Prüft …" : "Anmelden"}
          </PrimaryButton>
        </form>
      </Card>
    </div>
  );
}
