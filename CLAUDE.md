# Tempo

Selbstgehostete Trainingsanalyse für Läufer. Ersetzt die kostenpflichtigen
Coaching-Features von Garmin. Open Source, Single-User, läuft auf einem
Debian-Server zuhause hinter einem Cloudflare Tunnel.

Code und Kommentare auf Englisch. Alle Texte im Interface auf Deutsch.

---

## Nicht verhandelbar

**Keine echten Gesundheitsdaten im Repo.** `data/`, `*.fit`, `*.db`, `.env`
sind in `.gitignore` und bleiben es. Tests laufen gegen synthetische
Fixtures in `tests/fixtures/`, niemals gegen die Produktivdatenbank.

**Keine Secrets im Code, in Logs oder in Fehlermeldungen.** Alles über
Umgebungsvariablen, `.env.example` gepflegt halten.

**Keine Live-Calls in Tests.** Weder intervals.icu noch api.anthropic.com.
HTTP-Clients werden gemockt. Ein Test, der Netzwerk braucht, ist ein
kaputter Test.

**Die KI rechnet nichts.** Jede Kennzahl entsteht deterministisch in
`tempo/metrics/`, ist einzeln getestet und unabhängig von der KI-Schicht
korrekt. Die KI interpretiert fertige Zahlen und formuliert Text.

**Keine medizinischen Aussagen.** Bei Hinweisen auf Schmerz, Verletzung
oder Krankheit verweist die App auf ärztliche Abklärung.

---

## Stack

- Python 3.12, `uv` als Paketmanager
- FastAPI + uvicorn, Pydantic v2
- SQLite im WAL-Modus, SQLAlchemy 2.0, Alembic
- APScheduler für periodische Jobs
- `fitdecode` für FIT-Parsing, `httpx` für ausgehende Calls
- Frontend: React + Vite + Tailwind v4, `vite-plugin-pwa`
- pytest, ruff, mypy (strict für `tempo/metrics/`)
- Docker Compose, ein Service, Volume für `data/`

Keine Abweichung vom Stack ohne Rückfrage. Keine neue Abhängigkeit für
etwas, das die Standardbibliothek kann.

---

## Datenlage — Auslegungsgrundlage

Der Athlet hat lange nicht getragen. Tatsächlich vorhanden:

- 7 Aktivitäten insgesamt, davon 1 Lauf (3,5 km), letzte im Januar 2026
- Wellness in drei Blöcken: 2025-09 (24 Tage), 2026-01 (25), 2026-05 (16)
- seit Juni 2026 nichts

**Lücken sind der Normalfall, nicht der Sonderfall.** Eine Implementierung,
die nur mit lückenloser Historie korrekt ist, ist falsch. Der Zustand
"Wert noch nicht verfügbar, 34 von 42 Tagen" ist der häufigste Zustand
der App und wird zuerst gebaut, nicht zuletzt.

Die Mockwerte im Design (61 Bereitschaft, 42 Aktivitäten, 90 Tage HRV,
ACWR 1,28) sind erfunden. Nicht als Fixtures übernehmen.

---

## Konfidenz und Veraltung

Gilt für jede Kennzahl, ohne Ausnahme. Rückgabeform:

```
value | null
confidence: 0..1          Fensterfüllgrad × Aktualität
days_of_history: int      im aktuellen, ungebrochenen Fenster
have / required: int      für den Fortschrittszustand
last_data_point: date
stale: bool               letzter Datenpunkt älter als 7 Tage
```

Regeln:

- **Baseline-Reset:** nach über 14 Tagen ohne Wellness-Daten wird die
  HRV- und Ruhe-HF-Baseline verworfen und neu aufgebaut. Niemals über
  eine Lücke hinweg mitteln.
- **Mindesthistorie**, bevor überhaupt ein Wert ausgeliefert wird:
  Bereitschaft 14 Nächte, HRV-Baseline 21 Tage, Formkurve 42 Tage,
  Critical Speed 3 Bestleistungen. Darunter liefert die API `value: null`
  plus `have`/`required`/`available_from`.
- **Die Schwellen stehen an genau einer Stelle** (`tempo/metrics/thresholds.py`)
  und werden über die API ans Frontend geliefert. Das Frontend hält keine
  eigenen Zahlen vor.
- **Veraltete Werte** werden nie als aktueller Zustand dargestellt. Die
  API liefert `stale` und `last_data_point`, das Frontend zeigt das Datum
  an der Kachel.
- Trainingstage ohne Einheit zählen als Last 0. CTL/ATL klingen dadurch
  korrekt ab. Kein Überspringen leerer Tage.

---

## Design

Verbindlich sind `design/tempo-tokens.css` und das Nocturne-System in
`design/_ds/`. Screen 1 "Heute" liegt als Referenz in `design/` — als
Vorlage lesen, nicht Markup kopieren.

- Jede Farbe, Größe, Radius, Abstand kommt aus einem Token. Kein Hex,
  keine rohe px-Zahl im Komponentencode.
- Akzent `--t-accent` nur für Aktionen und KI-Kennzeichnung, als Linie
  und Glow, nie als Fläche.
- Statusfarben bewerten, Zonenfarben beschreiben. Die beiden Rollen
  werden nicht vermischt.
- Primäraktionen sind outlined, nicht gefüllt. Schriftgewicht nie über
  500. Ziffern immer `tabular-nums`.
- Kein reines Schwarz, kein reines Weiß.
- Icons: Phosphor, inline SVG auf `currentColor`.
- Touchziele mindestens 44 pt, Primäraktionen im unteren Drittel.
- Jeder Screen in sechs Zuständen: Standard, Ladezustand, Leer,
  **Im Aufbau (Teilverfügbarkeit)**, Veraltet/Offline, Fehler.

Das Modell in der KI-Fußzeile wird aus der Config gerendert, niemals
hartkodiert.

---

## Arbeitsweise im Auto-Mode

- Ein Branch je Phase: `phase-1-scaffold`, `phase-2-ingest`, …
  Niemals direkt auf `main`, niemals force-push.
- Am Ende jeder Phase: `ruff`, `mypy`, `pytest` grün, dann Commit, PR
  öffnen, **stoppen und auf Review warten**. Nicht in die nächste Phase
  weiterlaufen.
- Commits klein und thematisch, Conventional Commits.
- Bei unklarer Spezifikation nachfragen statt raten. Eine falsche Annahme,
  die durch acht Dateien wandert, kostet mehr als eine Rückfrage.
- Wenn ein Test nicht grün wird: den Test nicht abschwächen und nicht
  `skip` setzen, sondern das Problem benennen.
- `.env` und `data/` werden nicht gelesen und nicht geschrieben.

Der Phasenplan steht in `docs/PLAN.md`. Aktueller Stand und offene
Entscheidungen in `docs/DECISIONS.md` — dort wird jede getroffene
Architekturentscheidung mit einem Satz Begründung festgehalten.
