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

Phase 2 von 7. Fertig: Gerüst, Datenmodell, Migrationen, CLI,
`GET /health` sowie die Ingestion — FIT-Parser, intervals.icu-Client mit
Wasserstand und der optionale Garmin-Connector. Die Belastungskennzahlen
selbst (TRIMP, hrTSS, rTSS, CTL/ATL, Bereitschaft) kommen mit der
Metrik-Engine in Phase 3; bis dahin steht in `daily_load` die aggregierte
Dauer und Distanz, und die Kennzahlspalten stehen auf `null`. API,
KI-Schicht und die PWA folgen danach. Der Phasenplan steht in
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
| `INTERVALS_API_KEY` | API-Key aus intervals.icu, Einstellungen → Developer |
| `INTERVALS_ATHLETE_ID` | `0` als Selbstreferenz auf den eigenen Account |
| `ANTHROPIC_API_KEY` | Key aus der Anthropic Console |
| `ANTHROPIC_MODEL_DAILY` | Modell für Tageseinschätzung und Chat |
| `ANTHROPIC_MODEL_PLANNING` | Modell für Wochen- und Blockplanung |
| `TEMPO_MONTHLY_BUDGET_EUR` | Monatsbudget, harter Stopp bei Erreichen |
| `TEMPO_PASSWORD_HASH` | Passwort-Hash für die Anmeldung, siehe unten |
| `GARMIN_DIRECT_ENABLED` | Optionaler Garmin-Direktzugriff, Default `false` |
| `GARMIN_EMAIL`, `GARMIN_PASSWORD` | Nur nötig, wenn der Garmin-Zugriff an ist |

`TEMPO_PASSWORD_HASH` wird nicht von Hand geschrieben, sondern mit
`tempo hash-password` erzeugt. Der Befehl fragt das Passwort zweimal ab,
gibt die fertige Zeile aus und schreibt das Passwort nirgends hin:

```bash
docker compose run --rm tempo tempo hash-password
# Ausgabe: TEMPO_PASSWORD_HASH=scrypt$65536$8$1$… → nach .env kopieren
```

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

## Entwicklung

```bash
uv sync
uv run --env-file .env tempo init
uv run --env-file .env tempo hash-password
uv run --env-file .env tempo sync --full
uv run --env-file .env tempo recompute --all
uv run --env-file .env uvicorn tempo.api.app:app --reload

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
