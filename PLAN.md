# Tempo — Phasenplan

Eine Phase pro Branch und PR. Nach jeder Phase stoppen.
Grundregeln in `CLAUDE.md`, die gelten für jede Phase.

---

## Phase 1 — Gerüst und Datenmodell

Repo-Layout:

```
tempo/
  ingest/    intervals_client.py  fit_parser.py  garmin_optional.py
  metrics/   thresholds.py  zones.py  load.py  fitness.py
             performance.py  wellness.py  confidence.py
  api/       routes/  schemas.py  auth.py
  ai/        (Phase 5)
  db/        models.py  migrations/
  cli.py
web/         (Phase 7)
tests/       fixtures/
data/fit/    (gitignored)
```

Tabellen:

- `activity` — id (intervals-ID als PK), start_local, sport, distance_m,
  moving_s, elapsed_s, elevation_gain_m, avg_hr, max_hr,
  avg_pace_s_per_km, source, fit_path, imported_at
- `activity_stream` — activity_id, offset_s, hr, speed_m_s, altitude_m,
  cadence, power, lat, lon (schmal, Index auf activity_id)
- `lap` — activity_id, index, distance_m, duration_s, avg_hr, avg_pace
- `wellness_day` — date PK, resting_hr, hrv_rmssd, sleep_secs,
  sleep_score, vo2max, weight_kg, source
- `daily_load` — date PK, trimp, hr_tss, r_tss, duration_s, distance_m
- `fitness_day` — date PK, ctl, atl, tsb, acwr, monotony, strain,
  confidence, days_of_history
- `athlete_settings` — hr_max, hr_rest, lthr, threshold_pace_s_per_km,
  zone_model, updated_at
- `ai_call` — ts, endpoint, model, input_tokens, output_tokens, cost_eur
- `sync_log` — source, started_at, finished_at, status, detail

CLI: `tempo init`, `tempo sync`, `tempo recompute --all`,
`tempo import-dir <pfad>`.

**Fertig, wenn:** Migrationen laufen, `GET /health` antwortet, CLI-Gerüst
steht.

---

## Phase 2 — Ingestion

### intervals.icu

Base `https://intervals.icu/api/v1`. **Basic Auth**, Username wörtlich
`API_KEY`, Passwort der Key. Kein Bearer — das gibt 403. Athlete-ID `0`
funktioniert als Selbstreferenz.

```
GET  /athlete/0/activities?oldest&newest[&fields]
GET  /activity/{id}
GET  /activity/{id}/streams
GET  /activity/{id}/fit-file        -> roh nach data/fit/ speichern
GET  /athlete/0/wellness?oldest&newest
GET  /athlete/0/events?oldest&newest
POST /athlete/0/events              (Phase 6)
```

Inkrementell über gespeicherten Wasserstand, Vollsync per Flag.
Sequenziell, Backoff bei 429, nicht öfter als stündlich.

### FIT-Parser

Liest die roh gespeicherten Dateien. Streams auf 1 Hz normalisieren,
**Lücken nicht interpolieren, sondern als None markieren**. Muss mit
fehlendem GPS, HF-Aussetzern und Pausen umgehen. Zusätzlich
`tempo import-dir` für einen Garmin-GDPR-Export.

### Garmin-Direktconnector — optional, Default AUS

Hinter `GARMIN_DIRECT_ENABLED`. Nur für Body Battery und Training
Readiness, die intervals.icu nicht liefert. `python-garminconnect` mit
persistiertem Token, **maximal ein Lauf pro Tag**. Bei 401/403/429
deaktiviert sich das Modul selbst und schreibt nach `sync_log`. Kein
Retry-Hammering — wiederholte Fehlversuche führen zu Sperren auf
Account-Ebene. Die App funktioniert ohne dieses Modul vollständig.

**Fertig, wenn:** Sync holt die vorhandenen Aktivitäten samt FIT, Parser
füllt Streams, `recompute --all` läuft durch.

---

## Phase 3 — Metrik-Engine

Kernstück. `mypy --strict`, hohe Testabdeckung, jede Formel mit
synthetischem Input und bekanntem Erwartungswert getestet.

### thresholds.py

Alle Mindesthistorien, Fenstergrößen und Reset-Regeln als Konstanten an
einer Stelle. Wird von der API ans Frontend geliefert.

### zones.py

HF-Zonen über %HRmax oder %LTHR (Friel-Laufmodell als Default),
Pace-Zonen über Schwellenpace. Zeit-in-Zone aus dem 1-Hz-Stream.

### load.py

- TRIMP (Banister): `t_min · HRr · 0.64 · e^(1.92·HRr)`,
  `HRr = (HRavg − HRrest) / (HRmax − HRrest)`
- TRIMP (Edwards): Minuten je Zone, Gewicht 1..5
- GAP: metabolische Kostenkurve nach Minetti et al. 2002, Steigung aus
  medianggeglätteter Höhe, Ausreißer über ±30 % verwerfen
- rTSS: `(moving_s · IF²) / 3600 · 100`, `IF = NGP / Schwellenpace`
- hrTSS aus TRIMP, normiert auf 100 = eine Stunde an der Schwelle
- Efficiency Factor: `NGP (m/s) / Ø HF`
- Decoupling Pa:HR: `(EF_1.Hälfte − EF_2.Hälfte) / EF_1.Hälfte`,
  nur für Läufe ab 30 min mit durchgehender HF

### fitness.py

```
CTL_t = CTL_{t-1} + (Last_t − CTL_{t-1}) / 42
ATL_t = ATL_{t-1} + (Last_t − ATL_{t-1}) / 7
TSB_t = CTL_{t-1} − ATL_{t-1}
ACWR  = Σ7d / (Σ28d / 4)
Monotonie = Mittel(Tageslasten Woche) / SD(Tageslasten Woche)
Strain    = Wochenlast · Monotonie
```

Tage ohne Training zählen als Last 0.

### performance.py

- Bestleistungen je Dauer (1/2/5/10/20/30/60 min) aus rollierenden Fenstern
- Critical Speed und D' aus Regression `d = CS·t + D'`, mindestens 3
  Punkte zwischen 3 und 30 min, sonst `None`
- VDOT nach Daniels
- Prognosen 5/10/HM/M nach Riegel (`T2 = T1·(D2/D1)^1.06`) **und** aus
  VDOT — beide Werte ausweisen, nicht mitteln

### wellness.py

- HRV-Baseline: rMSSD ln-transformiert, 7-Tage-Rollmittel, Streuband
  ±0,5 SD über maximal 60 Tage Referenzfenster
- Ruhe-HF-Trend analog
- Bereitschaft 0..100 aus HRV-Abweichung, Ruhe-HF-Abweichung,
  Schlafdauer und TSB. Gewichtungen als benannte Konstanten an einer
  Stelle, dokumentiert.

### confidence.py

Die in `CLAUDE.md` beschriebene Konfidenz- und Veraltungslogik, einmal
implementiert und von allen Modulen genutzt.

**Pflichttests:** Lücke über 14 Tagen, Baseline-Reset, Kaltstart, leere
DB, Aktivität ohne HF, Aktivität ohne GPS, ein einzelner Datenpunkt,
Werte nur aus einem drei Monate alten Block.

**Fertig, wenn:** `recompute --all` liefert für die echten Daten
plausible Werte oder sauber `null` mit Fortschritt, alle Kaltstart- und
Lückentests grün.

---

## Phase 4 — REST-API

```
GET  /api/today
GET  /api/activities
GET  /api/activities/{id}
GET  /api/activities/{id}/streams?fields=&resolution=
GET  /api/trends?window=6w|12w|52w
GET  /api/performance
GET  /api/plan?from=&to=
GET  /api/settings        PUT /api/settings
GET  /api/thresholds
POST /api/sync            GET /api/sync/status
```

Jede Kennzahl in jeder Antwort trägt die Konfidenz-Metadaten aus
`CLAUDE.md`. Beispiel:

```json
{
  "readiness": {
    "value": null, "confidence": 0.0,
    "have": 3, "required": 14, "available_from": "2026-09-19",
    "last_data_point": "2026-06-03", "stale": true
  }
}
```

Auth: ein Benutzer, Passwort-Hash aus env, Session-Cookie
httpOnly/Secure/SameSite=Lax. Kein Cloudflare Access vorausgesetzt.

---

## Phase 5 — KI-Schicht

Ablauf: Metriken aggregieren → kompaktes Feature-JSON (Ziel unter 4 KB)
→ Claude interpretiert → Antwort speichern und cachen.
**Niemals Rohstreams oder ungefilterte Aktivitätslisten ins Kontextfenster.**

Modell aus der Config, Default `claude-sonnet-5` für Tageseinschätzung
und Chat, `claude-opus-5` für Wochen- und Blockplanung. Prompt Caching
für das statische Athletenprofil. Verbrauch je Aufruf nach `ai_call`,
Monatsbudget als Konfigwert mit hartem Stopp.

Der System-Prompt muss enthalten:

- Der Athlet steigt nach längerer Pause wieder ein. Belastung vorsichtig
  aufbauen, Schwerpunkt Grundlagenausdauer.
- Kennzahlen unter der Mindesthistorie oder mit niedriger Konfidenz
  dürfen nicht als Trend oder Baseline interpretiert werden. Fehlende
  Daten werden benannt, nicht überspielt.
- Keine medizinischen Aussagen.
- Empfehlungen konkret: Dauer, Zielzone, Zweck.

Endpunkte: `POST /api/ai/daily`, `/api/ai/activity/{id}`,
`/api/ai/plan-week`, `/api/ai/chat`.

---

## Phase 6 — Rückkanal zur Uhr

Generierte Einheiten als Kalender-Events an intervals.icu
(`POST /athlete/0/events` mit `workout_doc`), von dort synchronisiert
Garmin auf die Uhr. Sync-Status je Einheit in der DB, idempotent über
`external_id`.

---

## Phase 7 — PWA

React + Vite + Tailwind v4, `tempo-tokens.css` als Basis, Nocturne-Regeln
aus `CLAUDE.md`. Screen 1 nach der Design-Vorlage, Screens 2–6 nach
Nachlieferung des Designs.

- `manifest.webmanifest`, `display: standalone`, apple-touch-icon,
  Safe-Area-Insets
- Service Worker über `vite-plugin-pwa`, Stale-while-revalidate für
  API-Antworten, letzter Stand offline lesbar mit Zeitstempel
- Web Push erst nach expliziter Nutzergeste
- **`sw.js` und `manifest.webmanifest` dürfen nicht gecacht werden**

---

## Deployment

Docker Compose, ein Service, Volume für `data/`. Ausgehend nur
intervals.icu und api.anthropic.com. Eingehend Port 8000 hinter
Cloudflare Tunnel. Strukturiertes Logging nach stdout, keine Secrets.

Scheduler: Sync stündlich, Neuberechnung nachts, KI-Tagesanalyse 6 Uhr.
Optional hinter Flag: Exporter im InfluxDB Line Protocol.
