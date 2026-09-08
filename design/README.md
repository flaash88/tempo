# Design — was hier liegt und wie es zu lesen ist

Verbindliche Vorlage für das Frontend (Phase 7). **Als Referenz lesen,
nicht als Markup kopieren.** Die Dateien sind statische Mockups mit
Platzhaltern in `{{ }}` und `‹ ›`, keine Komponenten.

## Dateien

| Datei | Screen | Route | Hauptendpoint |
|---|---|---|---|
| `TempoHeute.dc.html` | Heute | `/` | `GET /api/today` |
| `TempoAktivitaet.dc.html` | Aktivitätsdetail | `/activity/:id` | `GET /api/activities/{id}` + `/streams` |
| `TempoTrends.dc.html` | Trends | `/trends` | `GET /api/trends`, `GET /api/performance` |
| `TempoPlan.dc.html` | Plan | `/plan` | `GET /api/plan`, `POST /api/ai/plan-week` |
| `TempoCoach.dc.html` | Coach | `/coach` | `POST /api/ai/chat` |
| `TempoEinstellungen.dc.html` | Einstellungen | `/settings` | `GET|PUT /api/settings`, `GET /api/sync/status` |
| `Tempo.dc.html` | Übersichtsseite aller Screens | — | — |
| `tempo-tokens.css` | **Design Tokens — verbindlich** | — | — |
| `_ds/` | Nocturne Design System (Basis) | — | — |
| `support.js`, `.thumbnail` | Artefakte des Design-Tools | — | ignorieren |

`tempo-tokens.css` wird unverändert nach `web/src/styles/` übernommen und
als Tailwind-v4-Theme registriert; das Mapping steht in `Tempo.dc.html`,
Abschnitt B.

## Tab-Leiste

Fünf Ziele in dieser Reihenfolge: Heute, Trends, Plan, Coach, Mehr.
Einstellungen liegen unter „Mehr".

## Zustände

Jeder Screen ist in seinen Zuständen angelegt und alle werden
implementiert:

1. **Standard** — Werte vorhanden
2. **Ladezustand** — Skeleton mit Shimmer
3. **Leer** — noch keine Daten, mit Handlungsaufforderung
4. **Im Aufbau** — Kennzahl noch nicht verfügbar, Fortschritt statt Wert
   („34 / 42 Tagen · verfügbar ab 12.10.")
5. **Veraltet** — Datum des letzten Datenpunkts an der Kachel
   („Stand 24.08.")
6. **Offline / Fehler** — Banner mit Zeitstempel, Aktion „Erneut"

Zustand 4 und 5 gelten **pro Kachel**, nicht pro Screen. Mehrere Kacheln
eines Screens können gleichzeitig in unterschiedlichen Zuständen sein —
das ist der Normalfall, nicht die Ausnahme.

## Platzhalter

- `{{ name }}` — Laufzeitwert aus der API
- `‹model›`, `‹tokens›`, `‹cost›`, `‹cap›`, `‹n_activities›` — Werte aus
  der Server-Config bzw. der KI-Antwort. Niemals hartkodieren.

## Widersprüche in den Mockups — so wird aufgelöst

- Der Leerzustand auf „Heute" nennt „mindestens 7 Nächte", andere Kacheln
  nennen 14 / 28 / 42. **Alle Schwellen kommen aus `GET /api/thresholds`**,
  der Text wird zur Laufzeit gefüllt. Keine Zahl im Frontend hartkodieren.
- Die Einstellungen zeigen einen maskierten API-Key. Die API gibt **keinen
  Key zurück**, auch nicht maskiert — nur `{"valid": true, "last4": "7f2c"}`.
- Die Mockzahlen sind erfunden und teils untereinander inkonsistent
  (z. B. „HRV 90 Tage" im Coach gegen „34/42 Tage" daneben). **Nicht als
  Testfixtures übernehmen.**

## Regeln aus dem Design-System

Gelten zusätzlich zu `CLAUDE.md`, Abschnitt „Design":

- Jede Farbe, Größe, Radius, Abstand aus einem Token. Kein Hex, keine
  rohe px-Zahl im Komponentencode.
- Akzent nur für Aktionen und KI-Kennzeichnung, als Linie und Glow, nie
  als Fläche.
- Statusfarben bewerten, Zonenfarben beschreiben — nie vermischen.
- Primäraktionen outlined, nicht gefüllt. Schriftgewicht nie über 500.
- Ziffern immer `tabular-nums`, Einheiten kleiner hinter der Zahl.
- Kein reines Schwarz, kein reines Weiß.
- Icons: Phosphor, inline SVG auf `currentColor`.
- Touchziele mindestens 44 pt, Primäraktionen im unteren Drittel.
- `:focus-visible` mit 2px Akzent-Ring, nie der Browser-Default.
