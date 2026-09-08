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

- **HFV 0,40 · Ruhe-HF 0,20 · Schlaf 0,20 · Form 0,20**, als benannte
  Konstanten an einer Stelle. Die Gewichte der *vorhandenen* Eingaben
  werden renormiert, statt den Wert von etwas herunterziehen zu lassen, das
  niemand gemessen hat. Unter zwei Eingaben gibt es keinen Wert: eine
  einzelne Zahl würde mehr über das Fehlende sagen als über den Athleten.
  Die Gewichte selbst sind eine Wahl, nicht eine Ableitung — sie gehören
  ins Review.

### Recompute

- **`recompute --all` ist idempotent und füllt die Lastspalten
  nachträglich.** Es liest die Streams aus der Datenbank; eine Einheit, die
  vor dem Anlegen der Athletenwerte importiert wurde, wird beim nächsten
  Lauf bewertet, ohne Neuimport. Ein Test hält beides fest.
