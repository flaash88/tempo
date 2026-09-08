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

---

## Phase 2 — Ingestion

### Aus dem Review übernommen

- **Der Wasserstand bekommt eine eigene Tabelle `sync_state`**
  (`source` PK, `last_activity_start`, `last_wellness_date`,
  `last_success_at`, `cursor`). `sync_log` bleibt reines Audit-Log und
  wird für das Fortsetzen nie gelesen.
- **Aktivitäten und Wellness haben getrennte Marken**, weil beide
  unabhängig voneinander abbrechen können. Ein Fehler bei den
  Aktivitäten darf den Fortschritt bei Wellness nicht verwerfen.
- **Eine Marke wandert erst nach vollständiger und committeter
  Verarbeitung weiter.** Aktivitäten werden dafür von alt nach neu
  abgearbeitet; ein Abbruch lässt die Marke bei der letzten Einheit
  stehen, die ganz durchgelaufen ist, nie bei einer späteren.
- **CI baut das Image** (`docker build`, ohne Push) und prüft es mit
  `tempo --version` und einem Import der App.
- **`tempo hash-password` steht in der Installationsanleitung**, samt
  Hinweis, dass `TEMPO_PASSWORD_HASH` nicht von Hand geschrieben wird.

### Wasserstand — die Ausnahmen von der Regel

- **Eine Einheit ohne FIT-Datei auf dem Server blockiert die Marke
  nicht.** Eine manuell auf intervals.icu eingetragene Einheit hat keine
  Aufzeichnung; das ist ein normaler Zustand und wird gezählt, nicht als
  Fehler behandelt. Sonst würde eine einzige solche Einheit jeden
  künftigen Sync für immer an derselben Stelle festhalten.
- **Eine unlesbare FIT-Datei wird nach `<name>.fit.invalid` beiseite
  gelegt** und die Marke wandert weiter. Ein erneuter Download derselben
  kaputten Datei hilft nicht, aber die verschobene Datei bleibt zur
  Ansicht liegen und der nächste Lauf holt sie neu.
- **Auth-Fehler und Rate Limiting brechen den Lauf sofort ab.** Danach
  wird nichts mehr gelingen, und die Marke bleibt, wo sie war.
- **Ein fehlgeschlagener Lauf setzt `last_success_at` nicht**, ein
  teilweise erfolgreicher schon. Die Stundensperre gilt also nach einem
  Teilerfolg — der Wasserstand sorgt dafür, dass der nächste Lauf genau
  dort weitermacht.
- **Überlappung von 7 Tagen** hinter der Marke. Nachträglich eingetragene
  Wellness-Tage und korrigierte Aktivitäten kommen so noch mit; jeder
  Schreibvorgang ist idempotent, die Überlappung kostet also nur eine
  Anfrage.
- **Die Wellness-Marke steht auf dem Ende des verarbeiteten Fensters,
  nicht auf dem letzten Tag mit Daten.** Sonst würde bei einer langen
  Datenlücke jeder Lauf wieder das ganze Jahr abfragen.

### FIT-Parser

- **Streams werden auf ein dichtes 1-Hz-Raster gelegt.** Eine Sekunde
  ohne Aufzeichnung wird als Zeile mit ausschließlich `None` gespeichert.
  Eine Pause ist damit sichtbar ein Loch und keine Linie, die darüber
  gezogen wurde — und die Fensterlogik in Phase 3 kann eine halbstündige
  Pause nicht versehentlich als durchgehende Belastung lesen.
- **Ein HF-Aussetzer ist keine Lücke.** Fehlt nur ein Kanal, bleiben die
  Nachbarkanäle stehen; `is_empty` unterscheidet die beiden Fälle.
- **Mehrere Records in derselben Sekunde: der erste gewinnt.** Ein
  Mittelwert wäre ein Wert, der nie gemessen wurde.
- **Zusammenfassungswerte kommen aus der Session-Message der Datei.** Nur
  wenn die Message ganz fehlt, werden Mittel- und Maximal-HF aus den
  Samples abgeleitet, und `derived_summary` sagt das. Eine Session, die
  ausdrücklich keine HF nennt, wird nicht aus den Records „korrigiert".
- **Die Ortszeit kommt aus der Activity-Message** (Differenz zwischen
  `local_timestamp` und `timestamp`). Fehlt sie, gilt UTC als Ortszeit
  und `local_time_assumed` markiert das.
- **`MAX_ACTIVITY_SPAN_S` (48 h) ist eine Parser-Schranke, keine
  Kennzahlschwelle**, und steht deshalb nicht in `thresholds.py`. Sie
  verhindert, dass ein kaputter Zeitstempel Millionen Stream-Zeilen
  erzeugt.
- **Die ID einer Einheit, die nur als Datei existiert, ist
  `fit-<sha256[:16]>` über den Dateiinhalt.** Derselbe Export ein zweites
  Mal eingelesen aktualisiert dieselbe Zeile.
- **`import-dir` kopiert die Dateien ins Datenvolumen**, weil
  `activity.fit_path` auch nach dem Löschen des Export-Ordners noch
  stimmen muss. Eine Einheit, die intervals.icu für dieselbe Startzeit
  und Sportart schon geliefert hat, wird übersprungen statt ein zweites
  Mal unter anderer ID angelegt.

### Tests des Parsers

- **Keine echte FIT-Datei im Repo, auch keine kleine.** `*.fit` ist
  gitignored und bleibt es, also erzeugen die Tests ihre Eingabe selbst:
  `tests/fixtures/fit_writer.py` ist ein minimaler FIT-Encoder (Header,
  Definition- und Data-Messages, beide CRCs). Der Umweg ist die
  Alternative dazu, Gesundheitsdaten einzuchecken.

### intervals.icu-Client

- **Basic Auth mit dem wörtlichen Benutzernamen `API_KEY`.** Ein Bearer
  Token wird mit 403 abgelehnt, deshalb gibt es diese Option nicht; die
  403-Meldung nennt die Ursache.
- **Anfragen laufen sequenziell.** Die Historie eines einzelnen Nutzers
  ist klein, und parallele Last auf einer kostenlosen API ist der Weg zu
  einer Sperre.
- **Bei 429 zählt `Retry-After` des Servers**, sonst exponentielles
  Backoff mit Deckel. 5xx und abgebrochene Verbindungen werden wiederholt,
  4xx nicht — die kommen beim zweiten Versuch genauso zurück.
- **`avg_pace_s_per_km` wird aus Distanz und Bewegungszeit gerechnet**,
  nicht aus dem `pace`-Feld der Antwort, dessen Einheit nicht belastbar
  dokumentiert ist.
- **`hrv` von intervals.icu ist rMSSD** und landet in `hrv_rmssd`.
  `hrvSDNN` ist ein anderes Maß und wird nicht in dieselbe Spalte
  gemischt.
- **Leere Wellness-Tage werden nicht gespeichert.** Die API antwortet auf
  einen Zeitraum mit einer Zeile pro Tag, die meisten davon ohne Werte;
  sie zu schreiben würde „nicht gemessen" in einen Messwert verwandeln.
- **Eine unbrauchbare Nutzlast lässt den Sync nicht scheitern.** Fehlt
  ID oder Startzeit, wird der Eintrag übersprungen, der Grund landet in
  `sync_log`, und der Lauf endet als `partial`.
- **Streams kommen aus der FIT-Datei, nicht aus `/streams`.** Die
  FIT-Datei ist die Aufzeichnung; die Client-Methode für `/streams`
  existiert für den Fall, dass auf dem Server keine FIT-Datei liegt, und
  ist nicht in die Pipeline verdrahtet.
- **`GET /athlete/0/events` ist implementiert, wird aber nicht
  gespeichert.** Für geplante Einheiten gibt es noch keine Tabelle; sie
  anzulegen ist eine Entscheidung der Phase, die den Plan-Screen baut.

### Garmin-Direktconnector

- **`garminconnect` ist ein optionales Extra, keine Abhängigkeit.** Ist
  der Connector aus, importiert niemand die Bibliothek; ist er an und sie
  fehlt, sagt das Modul das und schaltet sich ab wie bei jeder anderen
  Abweisung.
- **`wellness_day` bekommt `body_battery` und `training_readiness`.** Der
  Tabellenplan aus Phase 1 hatte für die beiden Werte, die nur dieser
  Connector liefert, keine Spalten — ohne sie wäre der Connector sinnlos.
- **Höchstens ein Versuch pro Tag**, über die `garmin`-Zeile in
  `sync_state`. Bei 401, 403 oder 429 schaltet sich das Modul ab und
  schreibt den Grund nach `sync_log`; es gibt hier bewusst keine
  Retry-Schleife, weil wiederholte Fehlversuche zu Sperren auf
  Account-Ebene führen.
- **Erkannt wird eine Abweisung am Statuscode und am Klassennamen.** Die
  Fehlerklassen der Bibliothek tragen nicht immer einen Status.
- **Als Body Battery des Tages gilt der Tageshöchstwert.** Garmin liefert
  eine Reihe über den Tag; der Höchstwert liegt nach dem Schlaf, und das
  ist die Erholungsaussage, um die es geht.
- **Ein bestehender Wellness-Tag behält seine `source`.** Dass Garmin ein
  Feld ergänzt, macht den Tag nicht zu Garmins Tag.
- **Der Connector ist „aus" ohne Zeile in `sync_log`.** Ein stündliches
  „immer noch aus" wäre Rauschen, kein Audit-Trail.

### Recompute

- **`recompute --all` legt den Tageskalender an**: für jeden Tag vom
  ersten Datenpunkt bis heute eine Zeile in `daily_load`, kein Tag
  übersprungen. Tage ohne Einheit stehen ausdrücklich auf Last 0, damit
  CTL und ATL abklingen statt über eine Lücke hinweg festzuhängen.
- **Nur die Aggregation liegt hier, nicht die Formeln.** Dauer und
  Distanz der Einheiten eines Tages werden summiert; TRIMP, hrTSS und
  rTSS bleiben an Tagen mit Einheit `null`, weil „noch nicht gerechnet"
  eine andere Aussage ist als „keine Belastung". Die Formeln kommen mit
  der Metrik-Engine in Phase 3.
- **Der Kalender endet bei heute**, auch wenn der letzte Datenpunkt
  Monate alt ist — das ist der Normalfall dieser App, und genau daran
  klingt die Formkurve korrekt ab.

### Sonstiges

- **Eine Sportart-Vokabular an einer Stelle** (`tempo/ingest/sports.py`).
  intervals.icu sagt `Run`, FIT sagt `running`; beide werden beim Import
  normalisiert, damit kein Verbraucher zwei Schreibweisen kennen muss.
  Unbekanntes wird `Other` und nicht in eine Kategorie gezwungen.
- **Die Kopfrevision kommt aus dem Migrationsverzeichnis**
  (`head_revision()`), damit eine neue Revision nicht an einer zweiten
  Stelle nachgetragen werden muss.
- **FIT-Downloads werden atomar geschrieben** (`.part`, dann `rename`).
  Ein abgebrochener Download kann so nie als vollständige Datei
  missverstanden werden.

### Nachtrag aus dem zweiten Review

- **`planned_workout` spiegelt den Kalender der Quelle** (Migration
  `0003`). `GET /athlete/0/events` schreibt jetzt dorthin, idempotent über
  `external_id`; wo kein `external_id` existiert — Einträge, die in der
  Oberfläche der Quelle entstanden sind — dient die eigene ID als
  Schlüssel. Keine Sync-Status-Spalten: der Rückkanal ist Phase 6 und
  bringt seine Buchhaltung selbst mit.
- **Einträge, die nicht Trainingseinheiten sind, werden mitgespeichert.**
  Der Kalender enthält auch `NOTE` und `RACE_A`; genau dafür ist die
  Spalte `category` da, und Filtern würde Information wegwerfen, die der
  Plan-Screen braucht.
- **Der Kalender hat keinen eigenen Wasserstand.** Ein Plan liegt in der
  Zukunft, das Fenster reicht also immer nach vorn und wird jeden Lauf
  ganz neu gelesen. Das ist eine Anfrage, jeder Schreibvorgang ist
  idempotent — und es ist die einzige Möglichkeit, dass eine an der Quelle
  gelöschte Einheit hier ebenfalls verschwindet.
- **Einträge, die die Quelle im abgefragten Fenster nicht mehr hat, werden
  gelöscht.** Ein Plan ist ein Spiegel; eine Einheit, die niemand mehr
  vorhat, wäre schlimmer als keine. Der Abgleich ist auf das tatsächlich
  abgefragte Fenster und auf diese Quelle beschränkt. Das geht über den
  Auftrag „idempotent über `external_id`" hinaus und ist bewusst so
  gebaut — ohne den Abgleich sammelt die Tabelle Phantomeinheiten an.
- **`EVENT_HORIZON_DAYS` (90) ist eine operative Grenze, keine
  Kennzahlschwelle**, und steht deshalb in `sync.py`. Ein Quartal deckt
  einen vollständigen Trainingsblock ab, ohne Jahre leeren Kalenders
  mitzuziehen.
- **`wellness_day.hrv_rmssd` heißt jetzt `hrv`, dazu kommt
  `hrv_source_field`.** Welches HFV-Maß ein Wert ist, hängt von der Quelle
  ab; der Feldname der Quelle reist deshalb mit dem Wert, statt dass der
  Spaltenname eine Metrik behauptet, die niemand geprüft hat. Ein Wert aus
  `hrv` und einer aus `hrvSDNN` sind verschiedene Größen — die Spalte
  pooled sie nicht stillschweigend, und ein Wechsel des Feldnamens ist
  für die Baseline ein Bruch.
- **Der Einheitenname im Spaltennamen weicht hier bewusst.** Ohne die
  Metrik ist die Einheit nicht bekannt; `hrv_ms` wäre eine Behauptung.

---

## Phase 3 — Metrik-Engine

### Reihenfolge und Zuschnitt

- **`confidence.py` kommt vor allem anderen**, weil jedes andere Modul es
  benutzt. Es ist die einzige Implementierung der Regeln aus `CLAUDE.md`
  und fasst nichts an außer Sequenzen von Datumswerten und Zahlen — die
  Datenbank kommt darin nicht vor.
- **`tempo/metrics/` bleibt rein.** Datenbankzugriff liegt in
  `tempo/recompute.py` (Schreiben) und `tempo/snapshot.py` (Lesen). Nur so
  bleibt `mypy --strict` über die Engine sinnvoll und jede Formel einzeln
  gegen handgerechnete Eingaben testbar.
- **`tempo/snapshot.py` ist neu und ist das Lesemodell.** Die Pflichttests
  fragen nach dem, was der Athlet zu sehen bekäme („Bereitschaft null mit
  Fortschritt"), und das entsteht erst beim Zusammensetzen. Phase 4 legt
  ihre Endpunkte darüber, statt die Logik dort zu wiederholen.

### Das aktuelle Fenster — die zentrale Auslegungsentscheidung

- **Ein Fenster ist die ununterbrochene Reihe von Datentagen, die bis heute
  reicht.** Nicht „alle Historie, die es gibt". Eine Lücke von über 14
  Tagen beendet es, und ein Fenster, dessen jüngster Tag selbst so weit
  zurückliegt, ist ebenfalls zu Ende. Ein Block von 16 getragenen Tagen aus
  dem Mai ist Historie, kein Fenster, und kann keinen aktuellen Wert
  liefern — genau das verlangt die Reset-Regel.
- **Für die Formkurve zählt der Tag als Datentag, an dem Wellness *oder*
  eine Einheit vorliegt.** Das ist die Unterscheidung zwischen „der Athlet
  hat eine Woche pausiert" und „die Uhr lag drei Monate in der Schublade":
  ein Ruhetag mit Wellness-Daten ist eine Information, Last 0 an ihm ist
  eine Tatsache, und nur ein Tag ganz ohne Daten bricht das Fenster. Ohne
  diese Regel würde entweder ein Taper die Formkurve wegwerfen oder ein
  untracked Quartal eine liefern. Der Plan sagt dazu nichts; das hier ist
  die Auslegung, und sie steht in einer Funktion (`tracked_window`).
- **Konfidenz fällt linear auf null über dieselben 14 Tage**, an denen die
  Reset-Regel die Baseline verwirft. Damit gibt es keine zweite erfundene
  Zahl für denselben Gedanken.
- **`stale` ist bei fehlenden Daten `False`.** Es gibt nichts, was veraltet
  sein könnte; das ist der Leerzustand, und den sagt `have == 0`.
- **`fitness_day` trägt die Konfidenz *des jeweiligen Tages*, nicht die von
  heute.** Eine Zeile von vor sechs Monaten beschreibt, was damals bekannt
  war. `window_lengths_by_day` rechnet das in einem Durchgang statt
  quadratisch.

### Zwei Mindesthistorien für dieselbe Arithmetik

- **Bereitschaft vergleicht gegen eine Baseline über ihr eigenes
  14-Nächte-Fenster; die veröffentlichte HFV-Baseline behält 21 Tage.**
  Ohne diese Trennung wäre die im Plan genannte Mindesthistorie für
  Bereitschaft unerreichbar: jede ihrer vier Eingaben hätte auf ein
  längeres Fenster gewartet als sie selbst. Es sind zwei Produkte derselben
  Rechnung — „wie steht heute zur jüngeren Norm" ist mit 14 Nächten
  beantwortbar, eine Trendlinie mit Streuband braucht mehr Punkte, bevor
  das Band etwas bedeutet. Ein Test hält beide Zustände gleichzeitig fest.

### Keine Interpretation der HFV-Metrik

- **`wellness.py` sagt nirgends, welches Maß der HFV-Wert ist.** Der
  Feldname der Quelle reist mit jedem Messwert; das Einzige, was das Modul
  damit tut, ist sich zu weigern, zwei davon zu mischen. Ein Wechsel des
  Feldnamens beendet das Fenster wie eine Datenlücke, weil eine Baseline
  aus zwei verschiedenen Größen eine Mischung aus zwei Größen wäre.
- **Die ln-Transformation ist Arithmetik, keine Aussage.** Sie steht auf der
  Baseline vermerkt (`log_transformed`), damit jeder spätere Vergleich im
  selben Raum passiert; getestet ist, dass eine Verdoppelung aller Werte
  dieselbe Streuung ergibt, die Skala also herausfällt.
- **Die Ruhe-HF-Baseline wird nicht ln-transformiert.** Gleitendes Mittel
  und Band übertragen sich, die Transformation nicht.

### Formeln und ihre Konstanten

- **Alle Koeffizienten stehen in `thresholds.py`**, auch die
  Minetti-Polynomkoeffizienten, die Edwards-Zonengewichte und die
  Bereitschaftsgewichte. `as_dict()` liefert sie ans Frontend.
- **Friels Laufmodell ist der Default**, %HRmax der Rückfall. Ohne
  Schwellen-HF und ohne HFmax gibt es keine Zonen; aus dem Alter wird
  nichts geschätzt.
- **Pace-Zonen werden als Geschwindigkeiten gehalten, nicht als Paces.**
  Schneller ist eine höhere Zone, und Geschwindigkeit steigt mit der
  Belastung wie die Herzfrequenz — beides gleich zu halten nimmt eine
  ganze Klasse vertauschter Vergleiche aus dem Weg.
- **Unter Zone 1 wird getrennt gezählt**, nicht als Zone 1. Beim
  %HRmax-Modell ist das der Ruhebereich, und ihn als leichtes Training zu
  zählen würde jede darauf gebaute Lastzahl aufblähen. Unbekannte Sekunden
  werden ebenfalls getrennt geführt: gemessen-aber-leicht und nie-gemessen
  sind zwei verschiedene Dinge.
- **Ein Gradient über ±30 % wird verworfen, nicht gekappt**, und die
  Sekunde geht mit ihrer Rohgeschwindigkeit ein — das ist der Flachfall.
  Ein barometrischer Ausreißer darf kein erfundener Hügel werden.
- **Ein gleitendes Fenster, das eine unaufgezeichnete Sekunde überlappt,
  wird übersprungen, nicht verkürzt.** Genau das ist der Unterschied
  zwischen einer Pause und einem langsamen Abschnitt.
- **NGP ist das gleitende 30-Sekunden-Mittel der steigungskorrigierten
  Geschwindigkeit, dann deren Mittel vierter Potenz.** Die 30 Sekunden sind
  Coggans Definition; die Zahl steht als benannte Konstante.
- **CTL/ATL/TSB laufen über *jeden* Kalendertag**, Tage ohne Einheit mit
  Last 0. Gerechnet wird überall, ausgeliefert wird selektiv — eine Kurve
  über ein untracked Quartal ist Arithmetik, keine Information.
- **TSB ist die Bilanz von gestern**, wie der Plan es schreibt: die
  heutige Einheit ist noch nicht verarbeitet, wenn der Athlet morgens
  entscheidet.
- **Monotonie verwendet die Populationsstandardabweichung.** Die Woche ist
  die ganze Population, keine Stichprobe einer längeren. Eine Woche ohne
  Streuung hat keine Monotonie — das Verhältnis wäre unendlich.
- **Die Formkurve wird aus rTSS gebaut, sonst aus hrTSS.** Beide sind auf
  „eine Stunde an der Schwelle = 100" normiert und damit in einer Reihe
  austauschbar. Rohes TRIMP ist es nicht: es hat seine eigene Skala, und
  Skalen zu mischen würde jedes Mal eine Stufe in die Kurve setzen, wenn
  der Brustgurt vergessen wurde.
- **Riegel und VDOT werden beide ausgewiesen und nie gemittelt.** Wo sie
  auseinandergehen, ist die Differenz die Information.
- **Die VDOT-Umkehrung bisektiert über die Geschwindigkeit, nicht über die
  Dauer.** Die Sauerstoffkostenrelation wird unter etwa 25 m/min negativ;
  ein Dauerintervall, das für einen Marathon weit genug ist, läuft für
  einen 5000er aus dem Modell heraus. Gegen Daniels' Tabelle geprüft:
  VDOT 50 ist 5000 m in 19:57, 10 000 m in etwa 41:21, Marathon in etwa
  3:10:49.
- **Critical Speed wird nur aus Läufen gefittet.** Eine kritische
  *Lauf*geschwindigkeit aus einer Radfahrt ist keine leicht falsche Zahl,
  sondern eine andere Größe — und in der echten Historie dieses Athleten
  sind die Radfahrten das Schnellste auf der Platte. Der Rauchtest gegen
  die echte Datenlage hat genau das aufgedeckt.
- **Critical Speed wird auch veraltet ausgeliefert.** Eine Bestleistung ist
  ein Rekord, kein aktueller Zustand: sie kommt mit `stale` und dem Datum,
  wie `CLAUDE.md` es für veraltete Werte vorschreibt, während
  Bereitschaft und Formkurve als Zustandsgrößen zurückgehalten werden.

### Was `None` bedeutet, nochmal

- **Ein Tag ohne Einheit trägt Last 0**, ein Tag *mit* Einheit ohne
  ableitbare Last trägt `None`. Ein Lauf ohne HF hat kein TRIMP und kein
  hrTSS, kann aber rTSS haben; ohne konfigurierte Schwellenpace ist es
  umgekehrt; ohne beides bleiben Dauer und Distanz, und mehr wird nicht
  behauptet.
- **Ein Tag mit zwei Einheiten, von denen nur eine bewertbar ist, meldet
  die eine**, die er hat. Ein Tag, an dem keine bewertbar war, bleibt
  unbekannt statt null.

### Bereitschaftsgewichte

- **HFV 0,40 · Ruhe-HF 0,20 · Schlaf 0,20 · Form 0,20 als dokumentierter
  Default, aber konfigurierbar** über
  `TEMPO_READINESS_WEIGHT_HRV` und die drei Geschwister. Die Gewichte sind
  eine Wahl und keine Ableitung, und der einzige Weg herauszufinden, ob sie
  für einen bestimmten Athleten stimmen, ist sie zu ändern und mit dem
  Empfinden zu vergleichen. Jeder Wert lässt sich einzeln setzen, die
  anderen drei behalten ihren Default.
- **Nur die Verhältnisse zählen.** Die Gewichte der *vorhandenen* Eingaben
  werden renormiert, statt den Wert von etwas herunterziehen zu lassen, das
  niemand gemessen hat; ein mit zehn multiplizierter Satz verhält sich
  deshalb identisch. Ein negatives Gewicht und ein Satz aus Nullen werden
  abgelehnt.
- **Unter zwei Eingaben gibt es keinen Wert.** Eine einzelne Zahl würde
  mehr über das Fehlende sagen als über den Athleten.
- **In der Aufbauphase trägt der Form-Term praktisch nichts bei.** Er
  braucht 42 Tage getrackte Historie; solange die nicht da sind, ist er
  nicht vorhanden und wird wegrenormiert. **Die Bereitschaft ruht in dieser
  Zeit faktisch auf HFV, Ruhepuls und Schlaf** — das Gewicht für die Form
  fängt erst an zu wirken, wenn die Uhr sechs Wochen lang getragen wurde.
  Für diesen Athleten, der gerade wieder einsteigt, ist das der Zustand der
  nächsten Wochen und nicht die Ausnahme. Ein Test hält es fest, statt es
  nur hier zu behaupten: mit Gewicht 0,7 auf der Form kommt derselbe Wert
  heraus wie mit Gewicht 0.
- **Das Schlafziel steht in `athlete_settings.sleep_target_s`**, Default
  acht Stunden. Sieben Stunden sind eine volle Nacht für jemanden, der
  sieben anstrebt; eine feste Zahl im Code hätte das zu 87,5 Punkten
  gemacht.

### Subjektive Tagesform

- **`wellness_day` bekommt `fatigue`, `soreness` und `mood`** (Migration
  `0004`), gespeichert und von nichts gelesen. Sie werden ab jetzt
  mitgeführt, weil sie später der einzige Weg sind, die Gewichte gegen
  echtes Empfinden zu kalibrieren statt gegeneinander — und Kalibrierung
  braucht Daten aus der Vergangenheit, nicht ab dem Tag, an dem eine
  Auswertung geschrieben wird.
- **Die Skala ist die der Quelle und ungeprüft.** intervals.icu liefert
  diese Felder als kleine Ganzzahlen; was 1 gegenüber 4 bedeutet und in
  welche Richtung, ist gegen keinen echten Account bestätigt. Deshalb wird
  der Rohwert gespeichert und nichts interpretiert — dieselbe Haltung wie
  beim HFV-Feldnamen. Eine Auswertung wartet auf die Bestätigung.

### Recompute

- **`recompute --all` ist idempotent und füllt die Lastspalten
  nachträglich.** Es liest die Streams aus der Datenbank; eine Einheit, die
  vor dem Anlegen der Athletenwerte importiert wurde, wird beim nächsten
  Lauf bewertet, ohne Neuimport. Ein Test hält beides fest.

---

## Phase 4 — REST-API

### Authentifizierung

- **Ein Benutzer, ein Passwort, ein Cookie.** Der Session-Cookie ist
  `httpOnly`, `Secure` und `SameSite=Lax` und trägt nichts als ein
  Ablaufdatum und eine Signatur — keine Identität, keine Claims, nichts,
  was für sich genommen wertvoll wäre.
- **Der Signaturschlüssel wird aus dem Passwort-Hash abgeleitet**
  (`blake2b` mit Domain-Trennung). Das spart ein zweites Geheimnis in der
  Konfiguration, und ein Passwortwechsel beendet damit jede laufende
  Sitzung — was ein Passwortwechsel tun soll. Der Hash verlässt den Server
  nicht und ist aus dem Schlüssel nicht zurückrechenbar.
- **Cloudflare Access wird nicht vorausgesetzt.** Wer es davorschaltet,
  bekommt eine zweite Schicht; diese hier stützt sich auf keinen
  Proxy-Header und lehnt eine Anfrage ohne gültiges Cookie in jedem Fall
  ab.
- **Eine unfertige Installation ist keine offene Tür.** Ohne
  `TEMPO_PASSWORD_HASH` wird alles abgelehnt, statt alles durchzulassen;
  `GET /api/auth/session` sagt zusätzlich, ob überhaupt eine Anmeldung
  konfiguriert ist, damit die Oberfläche „Einrichtung abschließen" von
  „Passwort falsch" unterscheiden kann.
- **`/health` bleibt offen** und außerhalb von `/api`. Tunnel und
  Container-Healthcheck brauchen es, und es enthält keine Athletendaten.
- **Ein Test prüft, dass `Secure` wirkt**, indem er dieselbe Anmeldung über
  `http` fährt: der Client sendet das Cookie dann nicht zurück, und die
  Anfrage wird abgelehnt. Das Flag ist keine Dekoration.

### Keine Zugangsdaten in Antworten

- **Kein Key kommt zurück, auch nicht maskiert.** Das Mockup zeichnet
  `sk-ant-•••• 7f2c`; die API liefert `{"valid": true, "last4": "7f2c"}`
  und die Punkte malt die Oberfläche. Ein maskierter Key ist immer noch
  ein Präfix eines Keys.
- **Keys lassen sich über die API auch nicht setzen.** Sie stehen in der
  Umgebung, eine Rotation ist damit eine Deployment-Handlung und nie etwas,
  das ein Session-Cookie auslösen kann. `SettingsUpdate` lehnt unbekannte
  Felder ab, statt sie stillschweigend zu ignorieren.
- **Ein Test läuft über jede Antwort** und sucht nach dem Key, dem Präfix
  und dem Passwort — nicht pro Route, sondern über alle.

### Die Konfidenz-Hülle

- **Jede Kennzahl in jeder Antwort trägt die vollständigen Metadaten.** Eine
  einzige Umwandlung (`api/envelope.py`) wird von allen Routen benutzt,
  damit keine versehentlich eine nackte Zahl oder eine halbe Hülle
  ausliefert.
- **Zwei Tests sichern das strukturell**: einer läuft über die Antworten und
  prüft jede Hülle, einer über das OpenAPI-Schema und stellt fest, dass
  kein als Kennzahl benanntes Feld etwas anderes als eine `MetricEnvelope`
  ist.
- **Baselines werden in den Einheiten der Messwerte ausgeliefert.** Mittel
  und Band werden aus der Transformation zurückgerechnet, in der sie
  gebaut wurden, damit die Oberfläche Millisekunden zeigt und keine
  Logarithmen — während die Baseline selbst weiter mitführt, in welchem
  Raum sie entstanden ist.

### `GET /api/thresholds`

- **Alle Mindesthistorien, Fenster und Schwellen**, dazu die
  konfigurierbaren Werte so, wie sie tatsächlich in Kraft sind, *und* der
  dokumentierte Default daneben. Das Frontend hält damit keine Zahl vor,
  nicht einmal als Rückfallwert — die Sätze der Mockups („mindestens 7
  Nächte", „34 / 42 Tagen") werden zur Laufzeit gefüllt.

### `GET /api/performance`

- **Prognosen werden als veraltet ausgewiesen, wenn die zugrundeliegende
  Bestleistung älter als 90 Tage ist** (`PREDICTION_STALE_AFTER_DAYS`,
  ebenfalls über `/api/thresholds` ausgeliefert). Bewusst länger als die
  allgemeine 7-Tage-Grenze: eine Bestleistung ist ein Rekord und veraltet
  nicht in einer Woche, aber ein Quartal ohne Wettkampf macht sie zur
  Historie.
- **Ausgeliefert wird sie trotzdem**, mit Kennzeichnung und Datum — dieselbe
  Haltung wie bei Critical Speed. Zurückgehalten werden Zustandsgrößen,
  nicht Rekorde.
- **Die längste Bestleistung ist der Anker der Prognosen.** Je weiter außen
  der Anker, desto weniger hängt die Vorhersage am Verhalten des Modells
  bei kurzen Dauern.
- **Riegel und VDOT stehen nebeneinander, nie gemittelt** — auch in der
  Antwort, wo jede Distanz beide Methoden als Liste trägt.

### Streams

- **`resolution` dezimiert, es glättet nicht.** Eine gröbere Auflösung
  behält jede n-te aufgezeichnete Sekunde und lässt den Rest weg. Einen
  Bucket zu mitteln würde einen Messwert für eine Sekunde erfinden, die nie
  gemessen wurde, und würde eine Lücke schließen, die der Parser
  ausdrücklich bewahrt hat. Jeder gelieferte Punkt ist eine echte Messung
  oder ein echtes `null`.
- **`fields` prüft gegen eine Liste bekannter Kanäle** und lehnt Unbekanntes
  mit 400 ab, statt es still zu ignorieren.

### `POST /api/sync`

- **Läuft im Hintergrund und antwortet mit 202.** Ein Vollsync dauert
  Minuten; `GET /api/sync/status` liefert den Rest aus `sync_state` und
  `sync_log`.
- **Ein zweiter Lauf während eines laufenden wird mit 409 abgelehnt.**
- **Der ausgehende Client kommt über `app.state.client_factory` herein.**
  Das ist kein Testartefakt, sondern die einzige verlässliche Art, „keine
  Live-Calls in Tests" durchzusetzen: ein Test kann keinen echten Aufruf
  machen, weil er keinen Weg dazu hat.

### Netzwerksperre in der Testsuite

- **Eine `autouse`-Fixture blockiert Sockets in jedem Test.** Aufgefallen
  ist das an einem Test, der 15 Sekunden brauchte: der Hintergrund-Sync
  hat wirklich versucht, intervals.icu zu erreichen, und die Zeit war das
  Backoff. Die Regel steht in `CLAUDE.md`; sie durchzusetzen statt sich an
  sie zu erinnern ist der Unterschied zwischen einer Regel und einer
  Absicht. Ein Test, der jetzt nach dem Netz greift, scheitert sofort mit
  dem Grund.

### Schemata gegen `design/README.md`

- **Ein Test liest die Endpoint-Tabelle aus `design/README.md`** und prüft
  gegen das OpenAPI-Schema, dass jeder dort genannte Endpoint existiert.
  Die `/api/ai/`-Endpunkte sind ausgenommen und als Phase 5 benannt — auch
  das prüft ein Test, damit die Ausnahme nicht stillschweigend zur
  Dauerlösung wird.
- **Die Feldlisten folgen den Mockups.** Was ein Screen anzeigt, liefert
  sein Endpoint: Heute die sechs Kacheln samt Schlaf und geplanter Einheit,
  das Aktivitätsdetail GAP, TRIMP, hrTSS, EF, Decoupling, Kadenz und
  Zonenverteilung, Trends die CTL/ATL/TSB-Reihe, das HFV-Band, Wochenvolumen
  mit Zonenanteilen, die Einstellungen Schwellenwerte, Zonengrenzen,
  Modell, Verbrauch und Budget.
- **Das Modell in der KI-Fußzeile kommt aus der Config** und steht in
  `GET /api/today` als `ai_model` — nirgends hartkodiert.

### Bekannter Optimierungskandidat: Zonenanteile in `/api/trends`

- **Der Endpunkt liest für die Wochen-Zonenanteile jeden Stream im
  Fenster.** Das ist eine Aggregation über Rohdaten zur Anfragezeit, und
  sie wächst linear mit der Fensterlänge und der Datendichte.
- **Gemessen mit der echten Datenlage** (7 Aktivitäten, 371 Tage
  Kalender, insgesamt rund 23 000 Stream-Sekunden), Median aus fünf
  Läufen: `6w` 6 ms, `12w` 6 ms, **`52w` 26 ms**. Auf einem Debian-Rechner
  zuhause ist das nichts.
- **Die Schwelle ist 300 ms bei `window=52w`.** Wird sie überschritten,
  ist der Zeitpunkt gekommen, die Zonenanteile vorzuberechnen — je
  Aktivität beim Recompute in eine eigene Tabelle, statt sie bei jeder
  Anfrage neu aus den Streams zu rechnen. Der aktuelle Abstand zu dieser
  Schwelle ist Faktor zwölf, also wird hier nichts gebaut, was noch
  niemand braucht; die Zahl steht hier, damit die Entscheidung beim
  nächsten Mal gemessen und nicht geschätzt wird.

### Kleinigkeiten

- **`GET /api/plan` ohne Zeitraum liefert die laufende Woche**, Montag bis
  Sonntag. Zielwettkämpfe (`RACE_*`) werden von den Einheiten getrennt
  geliefert, weil der Screen sie getrennt zeigt.
- **Eine geplante Einheit gilt als erledigt**, wenn am selben Tag eine
  Aktivität derselben Sportart aufgezeichnet ist.
- **Pro Aktivität abgeleitete Werte haben ein Fenster von eins.** Sie
  brauchen genau eine Einheit, und die ist ihre ganze Historie — aber die
  Veraltung gilt weiter, weil ein Lauf vom Januar keine Aussage über heute
  ist.

### Subjektive Tagesform

- **`GET /api/today` liefert `fatigue`, `soreness` und `mood` roh aus**,
  ohne Deutung: keine Skala wird behauptet, keine Richtung unterstellt.
  Was eine 2 bedeutet und ob sie besser ist als eine 3, hängt von der
  Quelle ab und ist gegen keine bestätigt.
- **`PUT /api/wellness/{date}` schreibt dieselben drei Felder.** Ein nicht
  genanntes Feld bleibt, ein als `null` gesendetes wird geleert. Die Zeile
  wird angelegt, wenn der Tag noch keine hat — der Athlet kann sagen, wie
  ein Tag war, an dem die Uhr nichts zu sagen hat.
- **Die Grenzen 1 bis 10 sind eine Plausibilitätsprüfung, keine
  Skalenaussage.** Sie halten einen Tippfehler und eine versehentliche
  Null aus der Spalte, mehr nicht.
- **`wellness_day.subjective_source` trägt die Herkunft der drei Felder
  getrennt** (Migration `0005`). Sobald der Athlet einen Tag bewerten
  kann, dessen Messwerte von intervals.icu stammen, hat eine einzige
  `source`-Spalte zwei Antworten — und für die spätere Kalibrierung ist
  genau die Unterscheidung zwischen eigener Eingabe und synchronisiertem
  Wert die interessante.
- **Ein Sync ohne subjektive Werte überschreibt eine Handeingabe nicht.**
  Der Import setzt die drei Felder nur, wenn die Quelle überhaupt welche
  liefert.

---

## Phase 5 — KI-Schicht

### Der HTTP-Weg statt des offiziellen SDK

- **`tempo/ai/client.py` spricht die Messages-API über `httpx`**, nicht
  über das `anthropic`-Paket. CLAUDE.md nennt `httpx` für ausgehende
  Calls; eine dort nicht gelistete Abhängigkeit aufzunehmen wäre eine
  Stack-Abweichung und die ist rückfragepflichtig.
- **Der Preis dafür ist klein und der Nutzen konkret.** Benutzt wird ein
  Endpunkt mit einer Antwortform, und der Transport ist injizierbar — was
  überhaupt erst dafür sorgt, dass die Testsuite die API nie erreicht.
- **Umkehrbar.** Wer das SDK will, tauscht ein Modul und lässt alles
  andere stehen; die Entscheidung steht hier, damit sie im Review eine
  Entscheidung ist und kein Versehen.

### Das Feature-JSON ist die ganze Weltsicht der KI

- **Unter 4 KB, hart geprüft.** Passt es nicht, wird in fester Reihenfolge
  gekürzt — erst die Einzelaktivitäten, dann die Wochenvolumen, dann
  Bestleistungen, dann der Plan — und das Dokument sagt selbst, was
  fehlt (`omitted_for_size`). Ein gekürztes Dokument, das sich als
  vollständiges ausgibt, würde als "zwei Wochen Pause" gelesen.
- **`tempo/ai/features.py` liest `activity_stream` nirgends.** Nicht als
  Regel im Prompt, sondern als Eigenschaft des Moduls: es gibt keinen
  Codepfad, über den eine Sekundenreihe ins Kontextfenster kommt. Die
  Einordnung einer Einheit bekommt die fertigen Kennzahlen aus demselben
  Report, den der Detailscreen benutzt.
- **Die Aktivitätsliste ist gedeckelt** (`MAX_RECENT_ACTIVITIES = 10`),
  die Runden einer Einheit ebenfalls (`MAX_LAPS = 12`); mehr Runden werden
  als Anzahl genannt statt weggelassen.
- **Die Konfidenz-Metadaten reisen mit jeder Kennzahl mit.** Ein Wert, den
  die App nicht anzeigt, ist ein Wert, den das Modell nicht deuten darf —
  und erkennen kann es das nur an `have`, `required`, `confidence` und
  `stale`, die im Dokument stehen.
- **Was Gegenstand der Anfrage ist, wird nie gekürzt.** Die Einheit, über
  die gefragt wird, steht außerhalb der Kürzungsreihenfolge.

### Der System-Prompt

- **Die vier geforderten Regeln stehen als benannte Konstanten** in
  `tempo/ai/prompts.py` und werden je Aufgabe einzeln durch einen Test
  geprüft. Wiedereinstieg und vorsichtiger Aufbau, keine Trend- oder
  Baseline-Deutung unter der Mindesthistorie, keine medizinischen
  Aussagen, konkrete Empfehlungen mit Dauer, Zielzone und Zweck. Eine
  Regel, die nur im Fließtext eines Prompts steht, verschwindet bei der
  nächsten Umformulierung unbemerkt.
- **Der stabile Teil trägt den Cache-Breakpoint**, der volatile Teil — das
  Feature-JSON — steht in der Nutzernachricht. Ob das Prefix lang genug
  ist, damit der Cache greift, wird nicht behauptet: `ai_call` führt
  `cache_read_tokens` mit, und eine Folge von Aufrufen mit lauter Nullen
  ist der Beleg dafür, dass er es nicht tut.

### Budget mit hartem Stopp

- **Geprüft wird vor dem Bauen des Dokuments, nicht vor dem Senden.** Bei
  erreichtem Limit gibt es keinen Call, keine kleinere Anfrage und keine
  billigere Antwort — nur den definierten Zustand.
- **`402 Payment Required`** ist die Antwort, mit dem Budgetstand im Body.
  Nicht `429`, weil hier nichts rate-limitiert ist und Warten nichts
  ändert, und nicht `200`, weil ein Interface, das eine Absage als Antwort
  darstellt, sie irgendwann als Rat darstellt.
- **Ein Budget von 0 erlaubt nichts.** Das ist die beabsichtigte Lesart
  und der einfachste Weg, die KI-Schicht abzuschalten.
- **Die Kosten sind eine Schätzung und heißen so.** Listenpreise in
  `tempo/ai/pricing.py`, ein konstanter Eurokurs — einen Wechselkurs
  abzurufen wäre ein Live-Call in genau dem Pfad, der funktionieren muss,
  wenn Calls scheitern. Ein unbekanntes Modell wird mit dem teuersten
  Tarif gerechnet, damit es das Budget nicht still überzieht.

### Antwort-Cache

- **`ai_response` (Migration `0006`) schlüsselt auf alles, was die Antwort
  erzeugt hat**: Endpunkt, Modell, vollständiger System-Prompt und
  Feature-Dokument. Ändert sich eine Zahl, ändert sich das Dokument;
  ändert sich eine Regel, ändert sich der Prompt. Beides invalidiert den
  Eintrag.
- **Deshalb gibt es keine Ablaufzeit.** Es bleibt nichts übrig, wogegen
  eine schützen müsste. `refresh=true` erzwingt trotzdem eine neue
  Antwort, wenn der Athlet eine zweite Meinung will.
- **Das Dokument wird neben der Antwort aufbewahrt.** Ohne es lässt sich
  später nicht mehr feststellen, worauf das Modell geschaut hat, als es
  das gesagt hat.

### Kein Gesprächsverlauf im Chat

- **Jede Frage wird allein aus den Kennzahlen beantwortet.** Kein
  Verlauf heißt: das Kontextfenster bleibt beschränkt, die Kosten je Frage
  bleiben vorhersagbar, und eine frühere Antwort kann nicht zur Eingabe
  der nächsten werden — was die einzige Art wäre, wie eine erfundene Zahl
  im System bliebe.

### Modelle

- **Aus der Config, nie hartkodiert.** `plan_week` nimmt
  `ANTHROPIC_MODEL_PLANNING`, alles andere `ANTHROPIC_MODEL_DAILY`. Die
  Zuordnung sagt nur, welche der beiden konfigurierten ein Task benutzt.
- **Geliefert wird das Modell aus der Antwort**, nicht das angefragte. Die
  Fußzeile im Interface nennt das Modell und darf keines nennen, das den
  Text nicht geschrieben hat.
