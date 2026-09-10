# Tempo

Selbstgehostete Trainingsanalyse für Läufer. Tempo holt Aktivitäten und
Wellness-Daten von intervals.icu, berechnet daraus Belastung, Form und
Bereitschaft und lässt ein Sprachmodell die fertigen Zahlen einordnen.
Gedacht als Ersatz für die kostenpflichtigen Coaching-Funktionen von
Garmin — auf eigener Hardware, mit den eigenen Daten.

Ein Benutzer, ein Server, ein Container. Läuft auf einem Debian-Rechner
zuhause hinter einem Cloudflare Tunnel.

## Grundsätze

- **Die Berechnung ist deterministisch.** Jede Kennzahl entsteht in
  `tempo/metrics/`, ist einzeln getestet und unabhängig von der
  KI-Schicht korrekt. Das Sprachmodell interpretiert fertige Zahlen, es
  rechnet nichts.
- **Lücken sind der Normalfall.** Wer die Uhr eine Weile nicht getragen
  hat, bekommt keine erfundene Zahl, sondern den Fortschritt: „34 von 42
  Tagen, verfügbar ab 12.10.“ Jede Kennzahl trägt Konfidenz, Historie
  und das Datum ihres letzten Datenpunkts.
- **Gesundheitsdaten bleiben lokal.** Das Verzeichnis `data/`, alle
  FIT-Dateien und die Datenbank sind aus der Versionsverwaltung
  ausgeschlossen und bleiben es.

## Keine medizinische Beratung

Tempo ist ein Analysewerkzeug für das Training, kein Medizinprodukt. Die
angezeigten Werte und die vom Sprachmodell formulierten Empfehlungen
ersetzen keine ärztliche Beratung, Diagnose oder Behandlung. Bei
Schmerzen, Verletzungen, Krankheitszeichen oder auffälligen Messwerten
gehört die Abklärung zu einer Ärztin oder einem Arzt — nicht in diese
App.

## Stand der Entwicklung

Alle sieben Phasen stehen. Fertig: Gerüst, Datenmodell, Migrationen, CLI,
`GET /health`, die Ingestion (FIT-Parser, intervals.icu-Client mit
Wasserstand, optionaler Garmin-Connector) und die Metrik-Engine — TRIMP,
GAP nach Minetti, rTSS und hrTSS, Zeit in Zone, CTL/ATL/Form, ACWR,
Monotonie und Strain, HFV- und Ruhe-HF-Baselines, Bereitschaft,
Bestleistungen, Critical Speed, VDOT und Prognosen. Dazu die REST-API mit
Anmeldung: jede Kennzahl in jeder Antwort trägt Konfidenz, Historie,
Fortschritt und das Datum ihres letzten Datenpunkts, und
`GET /api/thresholds` liefert alle Schwellen, damit die Oberfläche keine
Zahl selbst vorhält. Dazu die KI-Schicht: aus den fertigen Kennzahlen
entsteht ein Feature-Dokument unter 4 KB, Claude formuliert daraus Text,
die Antwort wird gespeichert und wiederverwendet. Dazu der Rückkanal:
bestätigte Einheiten gehen als Kalender-Events an intervals.icu, von wo
Garmin sie auf die Uhr synchronisiert. Und die PWA: sechs Screens, jeder
in seinen sechs Zuständen, installierbar und offline lesbar.

`tempo recompute --all` zeigt den aktuellen Stand direkt an:

```
Bereitschaft: noch nicht verfügbar (0/14, frühestens 22.09.2026) — letzter Wert 16.05.2026
Formkurve: noch nicht verfügbar (0/42, frühestens 20.10.2026) — letzter Wert 16.05.2026
Critical Speed: Stand 15.01.2026 — 5:59 min/km, D' 0 m
``` Der Phasenplan steht in
[`docs/PLAN.md`](docs/PLAN.md), getroffene Architekturentscheidungen in
[`docs/DECISIONS.md`](docs/DECISIONS.md).

## Installation auf Debian 13 (Trixie)

### Voraussetzungen

Docker und das Compose-Plugin aus den offiziellen Docker-Paketquellen:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) \
signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian trixie stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

### Tempo einrichten

```bash
git clone https://github.com/flaash88/tempo.git /opt/tempo
cd /opt/tempo
cp .env.example .env
mkdir -p data
# Der Container läuft unter uid 1000, das Volume gehört ihm.
sudo chown -R 1000:1000 data
```

`.env` ausfüllen:

| Variable | Bedeutung |
|---|---|
| `TEMPO_DATA_DIR` | Datenbank und FIT-Dateien, Default `data` — im Container das Volume |
| `TEMPO_WEB_DIR` | Gebautes Frontend, leer = `web/dist` unter dem Arbeitsverzeichnis |
| `INTERVALS_API_KEY` | API-Key aus intervals.icu, Einstellungen → Developer |
| `INTERVALS_ATHLETE_ID` | `0` als Selbstreferenz auf den eigenen Account |
| `ANTHROPIC_API_KEY` | Key aus der Anthropic Console |
| `ANTHROPIC_MODEL_DAILY` | Modell für Tageseinschätzung und Chat |
| `ANTHROPIC_MODEL_PLANNING` | Modell für Wochen- und Blockplanung |
| `TEMPO_MONTHLY_BUDGET_EUR` | Monatsbudget, harter Stopp bei Erreichen, `0` schaltet ab |
| `TEMPO_CHAT_MAX_TURNS` | Mitgeschickte Runden im Chat, Default `6`, Obergrenze `12` |
| `TEMPO_CHAT_MAX_MESSAGE_CHARS` | Zeichen je Nachricht, Default `1000`, Obergrenze `2000` |
| `TEMPO_READINESS_WEIGHT_HRV` | Gewicht der HFV in der Bereitschaft, Default `0.40` |
| `TEMPO_READINESS_WEIGHT_RESTING_HR` | Gewicht des Ruhepulses, Default `0.20` |
| `TEMPO_READINESS_WEIGHT_SLEEP` | Gewicht des Schlafs, Default `0.20` |
| `TEMPO_READINESS_WEIGHT_TSB` | Gewicht der Form, Default `0.20` |
| `TEMPO_PASSWORD_HASH` | Passwort-Hash für die Anmeldung, siehe unten |
| `GARMIN_DIRECT_ENABLED` | Optionaler Garmin-Direktzugriff, Default `false` |
| `GARMIN_EMAIL`, `GARMIN_PASSWORD` | Nur nötig, wenn der Garmin-Zugriff an ist |

Die vier Bereitschaftsgewichte sind eine Entscheidung, keine Herleitung;
nur ihr Verhältnis zählt, und wer eines setzt, behält für die anderen drei
den Default. Die Begründung steht in
[`docs/DECISIONS.md`](docs/DECISIONS.md).

`TEMPO_PASSWORD_HASH` wird nicht von Hand geschrieben, sondern mit
`tempo hash-password` erzeugt. Der Befehl fragt das Passwort zweimal ab und
schreibt das Passwort nirgends hin. **Mit `--write` trägt er den Hash
direkt in die `.env` ein** — richtig escaped und in einer Zeile:

```bash
docker compose run --rm -v "$PWD/.env:/app/.env" tempo \
  tempo hash-password --write
```

Das ist der empfohlene Weg. Ohne `--write` gibt der Befehl die Zeile nur
aus, und dann gelten die Fallstricke unten — Kopieren von Hand ist
dreimal daran gescheitert.

### .env-Fallstricke

Die Datei wird von Docker Compose gelesen, und Compose ist eigen:

- **`$` verdoppeln.** Der Hash besteht aus sechs mit `$` getrennten
  Feldern. Compose liest `$65536` als Variable und ersetzt sie durch
  nichts — der Hash kommt abgeschnitten an und passt danach zu keinem
  Passwort. `$$` ist die Escape-Form; `--write` schreibt sie automatisch,
  und Tempo versteht beide Schreibweisen, damit dieselbe Datei auch unter
  `uv run --env-file .env` funktioniert.
- **Kein Leerzeichen um `=`.** `TEMPO_PASSWORD_HASH = scrypt$$…` setzt eine
  Variable namens `TEMPO_PASSWORD_HASH ` mit einem führenden Leerzeichen im
  Wert. Also `NAME=wert`, ohne Leerzeichen.
- **Der Hash steht in einer Zeile.** Er ist rund hundert Zeichen lang;
  bricht das Terminal ihn beim Kopieren um, ist er in zwei Zeilen
  zerlegt und die zweite wird als eigene, kaputte Variable gelesen.
- **Keine Anführungszeichen nötig.** Compose nimmt sie in den Wert auf.
  (Tempo entfernt sie beim Lesen des Hashes wieder, aber verlassen sollte
  man sich darauf bei den anderen Variablen nicht.)

Ist der Hash trotzdem beschädigt, sagt Tempo es beim Start und beim Login
im Klartext — `TEMPO_PASSWORD_HASH unvollständig — $-Zeichen in .env
verdoppeln` — statt bloß „Passwort falsch".

Starten und Schema anlegen:

```bash
docker compose up -d
docker compose exec tempo tempo init      # Migrationen anwenden
curl -s localhost:8000/health
```

### Erste Daten holen

```bash
docker compose exec tempo tempo sync --full     # gesamte Historie
docker compose exec tempo tempo recompute --all
```

`recompute` rechnet die Kennzahlen aus dem, was in der Datenbank steht —
kein erneuter Import nötig. Damit TRIMP und hrTSS entstehen können,
brauchen es HFmax, Ruhe-HF und Schwellen-HF in `athlete_settings`, für
rTSS zusätzlich die Schwellenpace. Fehlt eines davon, sagt der Befehl
welches, und die betroffene Spalte bleibt `null` statt auf null gesetzt zu
werden.

Der Sync spiegelt zusätzlich den Kalender von intervals.icu nach
`planned_workout` — geplante Einheiten, Wettkämpfe und Notizen. Was dort
gelöscht wird, verschwindet beim nächsten Lauf auch hier.

Danach genügt der inkrementelle Sync; er fragt jede Quelle höchstens
stündlich ab und setzt an dem gespeicherten Wasserstand an:

```bash
docker compose exec tempo tempo sync
```

Eine Garmin-GDPR-Ausfuhr lässt sich zusätzlich einlesen. Die FIT-Dateien
werden ins Datenvolumen kopiert, Einheiten die intervals.icu schon
geliefert hat werden nicht doppelt angelegt:

```bash
docker compose exec tempo tempo import-dir /pfad/zur/ausfuhr
```

### Garmin-Direktzugriff (optional)

Aus, und die App ist ohne ihn vollständig. Er liefert ausschließlich Body
Battery und Training Readiness, die intervals.icu nicht führt. Der Zugriff
läuft höchstens einmal pro Tag und schaltet sich bei 401, 403 oder 429
selbst ab, weil wiederholte Fehlversuche zu Sperren auf Account-Ebene
führen. Die Bibliothek dafür ist ein optionales Extra:

```bash
uv sync --extra garmin        # bzw. im Image mitbauen
```

Die Datenbank und die rohen FIT-Dateien liegen im Volume unter
`./data/`. Dieses Verzeichnis ist das einzige, das gesichert werden muss.

### Cloudflare Tunnel

Tempo bindet nur an `127.0.0.1:8000` und wird nicht direkt ins Netz
gestellt. Der Zugriff von außen läuft über einen Cloudflare Tunnel.

```bash
sudo apt install -y cloudflared          # oder das .deb von Cloudflare
cloudflared tunnel login
cloudflared tunnel create tempo
cloudflared tunnel route dns tempo tempo.example.com
```

`/etc/cloudflared/config.yml`:

```yaml
tunnel: tempo
credentials-file: /root/.cloudflared/<TUNNEL-ID>.json

ingress:
  - hostname: tempo.example.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Als Dienst einrichten:

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

### Anmeldung

Tempo hat eine eigene Anmeldung für **einen** Benutzer: Passwort-Hash aus
der Umgebung, Session-Cookie mit `httpOnly`, `Secure` und
`SameSite=Lax`. Cloudflare Access wird nicht vorausgesetzt. Wer es
zusätzlich davorschaltet, bekommt eine zweite Schicht — die App verlässt
sich nicht darauf.

```bash
curl -c cookies -X POST https://tempo.example.com/api/auth/login \
  -H 'Content-Type: application/json' -d '{"password":"…"}'
curl -b cookies https://tempo.example.com/api/today
```

Der Cookie-Schlüssel wird aus dem Passwort-Hash abgeleitet: ein
Passwortwechsel beendet damit jede laufende Sitzung. `GET /health` bleibt
ohne Anmeldung erreichbar, alles unter `/api` nicht.

Zugangsdaten kommen aus keiner Antwort zurück, auch nicht maskiert — die
Einstellungen liefern nur `{"valid": true, "last4": "7f2c"}` — und lassen
sich über die API auch nicht setzen: sie stehen in der Umgebung.

## KI-Auswertung

Vier Endpunkte, alle `POST` und alle hinter der Anmeldung:
`/api/ai/daily` für die Tageseinschätzung, `/api/ai/activity/{id}` für
eine einzelne Einheit, `/api/ai/plan-week` für die kommende Woche und
`/api/ai/chat` für eine Rückfrage. `GET /api/ai/budget` sagt, was der
Monat noch hergibt, ohne dafür einen Aufruf zu verbrauchen.

Was das Modell zu sehen bekommt, ist eng begrenzt: aggregierte Kennzahlen
samt ihrer Konfidenz-Metadaten, höchstens zehn zusammengefasste
Einheiten, Wochenvolumen, Bestleistungen und der Plan — zusammen unter
4 KB. **Rohe Sekundendaten verlassen die Datenbank nie.** Passt das
Dokument nicht, wird in fester Reihenfolge gekürzt und die Kürzung im
Dokument selbst vermerkt.

Gerechnet wird nichts von der KI. Jede Zahl entsteht vorher deterministisch
in `tempo/metrics/`; das Modell interpretiert fertige Werte und formuliert
Text. Kennzahlen unter ihrer Mindesthistorie kommen als `null` mit
Fortschritt an, und der System-Prompt verbietet ausdrücklich, sie als
Trend oder Baseline zu lesen. Medizinische Aussagen sind ausgeschlossen;
bei Hinweisen auf Schmerz, Verletzung oder Krankheit verweist die Antwort
auf ärztliche Abklärung.

### Der Wochenplan kommt strukturiert

`POST /api/ai/plan-week` antwortet nicht mit Prosa, sondern mit einem
JSON-Objekt: eine Begründung von höchstens drei Sätzen, sieben Tage mit
Datum, Wochentag, Typ (Einheit oder Ruhetag), Dauer, Zielzone,
Zielherzfrequenz und Zweck in je einem Satz, dazu die Einschränkungen der
Datenlage als eigene Liste. Die Rohantwort des Modells steht weiter in
`text`; der Plan-Screen zeigt sie als Rohfassung, nicht als Standardansicht.

**Die Struktur wird erzwungen, nicht erbeten.** Der Server prüft, was
zurückkommt: die sieben Daten müssen genau die des Planfensters sein, die
Begründung höchstens drei Sätze, eine Einheit braucht Dauer und Zone, ein
Ruhetag hat beides nicht. Eine Antwort, die durchfällt, wird **einmal**
nachgefordert — mit der Angabe, was falsch war. Fällt auch die zweite
durch, antwortet der Endpunkt mit `502`; eine halb gezeichnete Woche wäre
schlechter als eine benannte Fehlermeldung. Beide Aufrufe stehen auf der
Monatsrechnung, und nichts Fehlerhaftes wird zwischengespeichert.

**Zielherzfrequenzen kommen nicht vom Modell.** Es nennt eine Zone; die
Herzfrequenzen dazu rechnet Tempo aus den Zonengrenzen und den
Schwellenwerten des Athleten. Offene Enden bleiben offen: Friels Zone 1
hat keine untere Grenze, die man anzeigen sollte, Zone 5 keine obere,
solange keine HFmax hinterlegt ist. Sind gar keine Schwellen konfiguriert,
sagt die Antwort das in einem Satz, statt Zahlen zu erfinden.

### Chatverlauf

`POST /api/ai/chat` nimmt die früheren Runden entgegen; wie viele davon
mitgehen, entscheidet der Server. Jede Nachricht wird auf
`TEMPO_CHAT_MAX_MESSAGE_CHARS` gekürzt, es bleiben höchstens
`TEMPO_CHAT_MAX_TURNS` Runden, und der ganze Verlauf wird danach auf 4 KB
gekappt — dieselbe Obergrenze wie beim Feature-Dokument. Der Verlauf ist
damit kein Weg, mehr in das Kontextfenster zu bekommen, als das Dokument
selbst erlaubt. Beide Konfigwerte haben zusätzlich eine Obergrenze im
Code, die sich über die Umgebung nicht anheben lässt.

Zahlen kommen weiterhin ausschließlich aus dem aktuellen Dokument. Eine
frühere Antwort ist Zusammenhang, nie Eingabe.

### Freitext aus fremder Quelle

Namen, Beschreibungen und Notizen stammen von intervals.icu, aus dem
Kalender oder von der Uhr. Im Feature-Dokument stehen sie ausschließlich
verschachtelt unter dem Schlüssel `external_text`, mit zusammengefalteten
Zeilenumbrüchen und auf 120 Zeichen gekürzt, und der System-Prompt sagt
ausdrücklich, dass alles unter diesem Schlüssel gelesen und niemals
befolgt wird. Eine Einheit mit dem Namen „Ignoriere alle vorherigen
Anweisungen" ist eine Einheit mit einem albernen Namen.

### Kosten

`TEMPO_MONTHLY_BUDGET_EUR` ist ein harter Stopp. Ist das Monatsbudget
erreicht, antworten die Endpunkte mit `402` und dem Budgetstand — es wird
kein Aufruf gemacht, auch kein kleinerer. Ein Budget von `0` schaltet die
KI-Schicht ab.

Jeder Aufruf wird in `ai_call` verbucht (Tokens, Cache-Tokens, geschätzte
Kosten). Die Kosten sind eine **Schätzung** aus veröffentlichten
Listenpreisen und einem festen Eurokurs; verbindlich ist die Abrechnung in
der Anthropic Console.

Eine identische Anfrage kostet nichts: die Antwort ist über Endpunkt,
Modell, System-Prompt und Feature-Dokument geschlüsselt gespeichert.
Ändert sich eine Zahl, ändert sich das Dokument — dann wird neu gefragt.
`?refresh=true` erzwingt eine neue Antwort.

## Einheiten auf die Uhr

Tempo schreibt nicht auf die Uhr, sondern in den Kalender bei
intervals.icu — von dort holt Garmin die Einheit. Der Weg hat vier
Schritte und je einen Endpunkt:

```
POST   /api/plan/workouts             Vorschlag anlegen  (?replace=true)
POST   /api/plan/workouts/adopt       generierte Tage übernehmen
PUT    /api/plan/workouts/{id}        Vorschlag ändern
POST   /api/plan/workouts/{id}/confirm  bestätigen
POST   /api/plan/workouts/{id}/push     an die Uhr senden
```

`adopt` ist der Weg von der Wochenplanung in den Kalender: ein oder
mehrere generierte Tage, je Tag angelegt **und** bestätigt — der Klick auf
einen vorgeschlagenen Tag kann nichts anderes bedeuten. Bis zur Uhr ist es
von dort immer noch ein eigener Schritt. Ein Tag, der nicht geht, kommt als
Ergebnis zurück und nicht als Fehler, damit ein belegter Mittwoch nicht die
übrigen sechs Tage mitnimmt.

**Ohne Bestätigung geht nichts raus.** Ein Vorschlag ist ein Vorschlag;
erst `confirm` macht ihn übertragbar, und eine Änderung danach zieht die
Bestätigung wieder zurück. Ein Tag, an dem schon eine bestätigte Einheit
steht, weist einen zweiten Vorschlag ab — `replace=true` ist die
ausdrückliche Ausnahme, und der Ersatz muss neu bestätigt werden. Ein
Eintrag, den der Athlet in intervals.icu selbst angelegt hat, wird nie
überschrieben.

Jede Einheit trägt in `GET /api/plan` und `GET /api/today` ihren
Übertragungszustand: `not_sent`, `sending`, `on_watch`, `outdated`
(auf der Uhr, aber seit der Übertragung geändert) oder `failed`, dazu
Zeitstempel und Fehlergrund.

Übertragen wird idempotent: die `external_id` steht schon beim Anlegen
fest, und eine geänderte Einheit ersetzt ihr Event per `PUT`, statt ein
zweites daneben anzulegen.

## Die App

React + Vite + Tailwind v4, gebaut nach `design/tempo-tokens.css`. Sechs
Screens — Heute, Trends, Plan, Coach, Mehr und das Aktivitätsdetail — und
jede Kachel in dem Zustand, in dem ihre Kennzahl gerade ist: Wert,
Ladezustand, leer, **im Aufbau**, **veraltet** oder Fehler.

Die letzten beiden sind derzeit die einzigen, die man wirklich zu sehen
bekommt, und sie sind entsprechend gebaut: „0 / 14 Nächten · verfügbar ab
23.09." steht dort, wo sonst eine Zahl stünde, mit dem Datum des letzten
Datenpunkts darunter. **Keine dieser Zahlen steht im Frontend.** Alle
Schwellen und Mindesthistorien kommen aus `GET /api/thresholds`, die
Fortschrittswerte aus der Konfidenz-Hülle der jeweiligen Kennzahl.

### Installieren und offline

`manifest.webmanifest`, `display: standalone`, Apple-Touch-Icon und
Safe-Area-Insets: auf dem iPhone „Zum Home-Bildschirm" und die App läuft
ohne Browser-Chrom, mit korrektem Abstand zu Notch und Home-Indicator.

Der Service Worker (über `vite-plugin-pwa`) hält API-Antworten
stale-while-revalidate vor: ohne Verbindung ist der letzte Stand lesbar,
und **jeder Screen sagt, von wann er ist** — Banner mit Uhrzeit oben,
Datum des letzten Datenpunkts an jeder Kachel. Ein Reload nach einer
Änderung geht am Cache vorbei, damit nach „Bestätigen" nicht der Zustand
davor stehen bleibt.

`sw.js` und `manifest.webmanifest` werden **nie** gecacht — weder im
Precache noch über HTTP-Header. Ein gecachter Service Worker lässt sich
nicht mehr ersetzen, und ein gecachtes Manifest friert die
Installationsabfrage ein. Ein Test liest das am gebauten Artefakt nach,
nicht an der Konfiguration.

Eine neue Version übernimmt nicht von selbst: sie wird angeboten, und der
Athlet tippt darauf. Die App unter dem Daumen auszutauschen, während
jemand eine Zahl liest, ist genau die Überraschung, die eine
Trainingsapp nicht machen sollte.

### Layout am Gerätemaß prüfen

```bash
npx playwright install chromium          # einmalig
node web/scripts/audit-layout.mjs        # erwartet die App auf :8331
```

Fährt die sieben Screens **in zwei Modi** an — als Tab und als
installierte App (Chromium mit `--app=`, echtes `display-mode:
standalone`, dazu die Safe-Area-Werte eines iPhone) — und prüft, was nur
am Gerät auffällt: scrollt das Dokument (es darf nicht), gibt es genau
einen Scroller, sitzt die Leiste am unteren Rand, horizontaler Overflow,
Tap-Ziele unter 44 pt, Elemente unter dem Home-Indicator.

Gemessen wird bei **Zoom 1.0, 1.5 und 2.0** und mit simulierter Tastatur,
jeweils gegen den **sichtbaren** Viewport — nicht gegen `innerHeight`.
Sobald hineingezoomt wird, sind das zwei verschiedene Dinge, und die Hülle
muss dem sichtbaren folgen. `TEMPO_DISABLE_VV=1` hebt die Bindung auf und
muss den Lauf rot machen.

Geprüft wird auch, was **gemalt** wird: der Streifen zwischen Unterkante
Tab-Leiste und Bildschirmrand wird pixelweise gegen eine Referenzfarbe aus
der Leiste verglichen. `TEMPO_SIMULATE_STRIP=34` verkürzt die Hülle
absichtlich und muss den Lauf rot machen — sonst prüft die Prüfung nichts.

WebKits eigene Viewport-Arithmetik kann Chromium nicht nachstellen. Dafür
gibt es die Diagnose in der App: **Mehr → Diagnose** zeigt, was das Gerät
über Anzeigemodus, Höhen, Sicherheitsabstände und die Lage der Tab-Leiste
meldet, und legt alles als JSON in die Zwischenablage. Bei einem
Layoutproblem, das nur auf dem Telefon auftritt, ist das der erste
Griff — nicht der Debugger am Gerät.

### Bauen

```bash
cd web
npm ci
npm run build      # nach web/dist/
npm test           # vitest, liest u. a. den gebauten Service Worker
```

Im Container passiert das in einer eigenen Build-Stufe; das Laufzeit-Image
enthält kein Node, nur die fertigen Dateien unter `/app/web/dist`. Die API
liefert sie unter `/` aus — ein Ursprung für App und API, damit der
Session-Cookie ohne CORS-Geschichte funktioniert. `TEMPO_WEB_DIR`
überschreibt den Pfad, falls nötig; findet sich nichts, steht im Log, wo
gesucht wurde.

### Nachsehen, ob die App wirklich ausgeliefert wird

```bash
scripts/smoke-http.sh https://tempo.example.com   # gegen die Instanz
scripts/smoke-image.sh tempo:latest               # gegen das Image
```

Geprüft wird `/` auf 200 mit der App-Hülle, Manifest und Service Worker
auf 200 mit `no-store`, eine Client-Route auf 200 und ein unbekannter
`/api/`-Pfad auf 404. In der CI läuft das als eigener Schritt hinter dem
Image-Build: **ein Build, dessen Ergebnis nicht abrufbar ist, ist nicht
grün.**

## Entwicklung

```bash
uv sync
uv run --env-file .env tempo init
uv run --env-file .env tempo hash-password --write
uv run --env-file .env tempo sync --full
uv run --env-file .env tempo recompute --all
uv run --env-file .env uvicorn tempo.api.app:app --reload

cd web && npm run dev      # Frontend mit Proxy auf :8000

uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

Tests laufen ausschließlich gegen synthetische Fixtures in
`tests/fixtures/` und gegen temporäre Datenbanken, niemals gegen die
Produktivdatenbank, und ohne jeden Netzwerkzugriff.

## Lizenz

[AGPL-3.0-or-later](LICENSE).
