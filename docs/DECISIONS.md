# Architekturentscheidungen

Jede getroffene Entscheidung mit einem Satz Begründung, neueste zuletzt.
Was hier nicht steht, ist nicht entschieden.

---

## Phase 1 — Gerüst und Datenmodell

### Repo und Werkzeuge

- **`uv` mit `uv.lock` im Repo, Python auf 3.12 festgenagelt.** Damit
  bauen CI, Container und Entwicklungsrechner aus derselben
  Abhängigkeitsauflösung.
- **`argparse` statt Typer oder Click für die CLI.** Die Standardbibliothek
  kann Unterbefehle, und der Stack nennt kein CLI-Framework.
- **Kein `python-dotenv`.** Die Umgebung füllt Docker Compose über
  `env_file`, lokal `uv run --env-file .env`; die Anwendung liest
  ausschließlich `os.environ` und niemals eine Datei.
- **Konfiguration als Pydantic-`BaseModel` mit eigenen Env-Lesern statt
  `pydantic-settings`.** Vermeidet eine Abhängigkeit außerhalb des
  vereinbarten Stacks für rund dreißig Zeilen Code.
- **Secrets als `SecretStr`.** Ein versehentliches `repr` in einem Log
  oder einem Traceback kann den Key dann nicht mehr ausgeben; ein Test
  hält das fest.
- **Strukturiertes Logging als JSON-Zeilen auf stdout, ohne
  Fremdbibliothek.** Der Container-Log-Treiber ist der einzige Abnehmer.
- **`ruff format` gehört zur Definition von „grün".** Formatabweichungen
  sollen nicht in Reviews landen; CI prüft `ruff check`, `ruff format
  --check`, `mypy` und `pytest`.
- **`mypy` läuft über `tempo/` und `tests/`, `--strict` nur für
  `tempo/metrics/`.** So gilt der strenge Maßstab dort, wo die Zahlen
  entstehen, ohne den Rest zu blockieren.

### Datenmodell

- **SQLite mit `journal_mode=WAL`, `foreign_keys=ON`,
  `busy_timeout=5000`, gesetzt bei jedem Verbindungsaufbau.** Die
  nächtliche Neuberechnung soll die API nicht blockieren, und Kaskaden
  greifen in SQLite nur mit eingeschalteten Fremdschlüsseln.
- **Die intervals.icu-Aktivitäts-ID ist der Primärschlüssel von
  `activity`.** Ein wiederholter Import aktualisiert dieselbe Zeile,
  statt Duplikate anzulegen — Idempotenz ohne zusätzliche Logik.
- **`activity_stream` und `lap` haben zusammengesetzte Primärschlüssel
  (`activity_id`, `offset_s` bzw. `index`).** Der Index auf `activity_id`
  fällt dabei ab, und die Tabelle bleibt schmal.
- **`start_local` wird naiv gespeichert, `imported_at` und alle
  Protokollzeitstempel zeitzonenbewusst in UTC.** Der Lauf um sechs Uhr
  früh bleibt der Lauf um sechs Uhr früh, auch nach einem Ortswechsel.
- **`None` heißt „unbekannt", niemals „null".** In `daily_load` bekommt
  ein Tag ohne Einheit ausdrücklich `duration_s = 0` und
  `distance_m = 0.0`, damit CTL und ATL korrekt abklingen; ein `NULL` in
  `trimp` heißt dagegen, dass eine Einheit existiert, die Kennzahl aber
  nicht ableitbar war, etwa bei einem Lauf ohne Herzfrequenz.
- **In `fitness_day` ist jeder Wert optional, `confidence` und
  `days_of_history` sind es nicht.** Unterhalb der Mindesthistorie gibt
  es keinen verteidigbaren Wert, wohl aber einen Fortschritt.
- **`athlete_settings` ist ein Singleton mit `CHECK (id = 1)`.** Die App
  ist ausdrücklich für einen Benutzer gebaut; die Datenbank soll das
  durchsetzen, nicht die Anwendungsschicht.
- **Einheiten stehen im Spaltennamen** (`distance_m`,
  `avg_pace_s_per_km`, `sleep_secs`). Der Plan schreibt für `lap` nur
  `avg_pace`; die Spalte heißt hier `avg_pace_s_per_km`, damit keine Zahl
  ohne Einheit im Schema steht.
- **Benannte Constraint-Konventionen auf der Metadata.** SQLite kann
  unbenannte Constraints nicht ändern, Alembic braucht deshalb
  deterministische Namen.

### Migrationen

- **Alembic-Revisionen werden fortlaufend nummeriert (`0001`), nicht mit
  Hashes.** Die Reihenfolge ist dann im Verzeichnis ablesbar.
- **`render_as_batch=True`.** SQLite kann Spalten nicht direkt ändern;
  Alembic muss die Tabelle dafür neu schreiben.
- **Ein Test lässt `compare_metadata` gegen die migrierte Datenbank
  laufen.** Ein Modell, das ohne Migration geändert wurde, fällt damit
  sofort auf, statt erst beim Deployment.
- **Die Migrationen liegen im Paket (`tempo/db/migrations/`) und werden
  programmatisch angestoßen.** `tempo init` funktioniert dadurch auch im
  Container ohne `alembic.ini` im Arbeitsverzeichnis; die `alembic.ini`
  im Wurzelverzeichnis ist nur für die interaktive Entwicklung da.

### API und CLI

- **`/health` liegt außerhalb von `/api` und ohne Authentifizierung.** Der
  Tunnel und Docker prüfen damit die Erreichbarkeit; die Antwort enthält
  ausschließlich Version, Datenbankzustand und Schema-Revision, keine
  Athletendaten und keine Konfiguration.
- **Eine nicht migrierte Datenbank ist für `/health` kein Fehler, sondern
  `database: "missing"`.** Das ist der Zustand direkt nach der
  Installation, und der Endpunkt soll ihn benennen statt zu scheitern.
- **`MetricEnvelope` steht ab Phase 1 in `api/schemas.py`.** Die
  Konfidenz-Hülle ist Teil des Datenmodells, nicht eine Zutat der
  späteren Endpunkte — kein Endpunkt gibt je eine nackte Zahl zurück.
- **Noch nicht gebaute Unterbefehle beenden sich mit Code 2 und nennen
  ihre Phase.** Unterscheidbar von Code 1 („fehlgeschlagen") und ehrlicher
  als ein stiller Erfolg.
- **`tempo hash-password` kommt zusätzlich zu den vier Befehlen aus dem
  Plan.** Ohne ihn lässt sich `TEMPO_PASSWORD_HASH` nicht befüllen, und
  die Installationsanleitung wäre unvollständig.
- **Passworthashing mit `hashlib.scrypt` aus der Standardbibliothek,
  Parameter im Hash-String mitgeführt.** Kein `passlib`, kein `bcrypt`,
  und der Kostenfaktor lässt sich später anheben, ohne alte Hashes
  ungültig zu machen.
- **`verify_password` scheitert leise mit `False`.** Eine nicht
  konfigurierte Installation muss die Anmeldung verweigern, nicht einen
  Traceback zeigen.

### Schwellen

- **`tempo/metrics/thresholds.py` ist die einzige Stelle mit Zahlen,
  inklusive `as_dict()` für `GET /api/thresholds` ab Phase 4.** Ein Test
  sucht die Konstanten in allen anderen Modulen und schlägt an, wenn eine
  Schwelle dupliziert wurde.

### Tests

- **Jeder Test läuft gegen ein temporäres Datenverzeichnis;
  `TEMPO_DATA_DIR` wird umgebogen und der Settings-Cache geleert.** Die
  Produktivdatenbank wird nie geöffnet.
- **Die Fixtures in `tests/fixtures/synthetic.py` sind erfunden und
  bewusst nicht aus den Design-Mockups übernommen.** Deren Zahlen sind
  Platzhalter und untereinander inkonsistent.
- **Der Container läuft unter uid 1000, nicht als root.** Das Volume
  `./data` muss dem Benutzer gehören; die Installationsanleitung sagt das.

### Umfang von Phase 1

- **`tempo/metrics/` enthält nur `thresholds.py`, `tempo/ingest/` und
  `tempo/ai/` nur ihre `__init__.py`.** Der Plan listet die späteren
  Module im Layout; leere Platzhalterdateien wären aber nur Rauschen, das
  in Phase 2, 3 und 5 ohnehin ersetzt wird. Die Pakete stehen, der Inhalt
  kommt in seiner Phase.
- **`apscheduler` und `fitdecode` sind schon jetzt Abhängigkeiten,
  obwohl noch nichts sie importiert.** Sie stehen im vereinbarten Stack;
  ein festgeschriebener Lockfile jetzt erspart die Diskussion in Phase 2.
