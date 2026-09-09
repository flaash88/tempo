"""Command line interface.

Operator-facing output is German, like the rest of the interface. Code and
comments stay English.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import math
import sys
from pathlib import Path

from tempo import __version__
from tempo.api.auth import hash_password
from tempo.config import Settings, get_settings
from tempo.db.migrate import current_revision, upgrade_to_head
from tempo.db.session import engine_for, ensure_data_dirs
from tempo.ingest.errors import IngestError
from tempo.ingest.garmin_optional import sync_garmin
from tempo.ingest.sync import import_fit_directory, sync_intervals
from tempo.logging import configure_logging
from tempo.metrics.confidence import MetricResult
from tempo.metrics.wellness import Baseline
from tempo.recompute import recompute_all
from tempo.snapshot import Snapshot, build_snapshot, pace_of

log = logging.getLogger("tempo.cli")


def cmd_init(args: argparse.Namespace, settings: Settings) -> int:
    """Create the data volume layout and bring the schema up to date."""
    data_dir = ensure_data_dirs(settings)
    upgrade_to_head()
    engine = engine_for(settings)
    try:
        revision = current_revision(engine)
    finally:
        engine.dispose()
    print(f"Datenverzeichnis: {data_dir}")
    print(f"Datenbank:        {settings.db_path}")
    print(f"Schema-Revision:  {revision}")
    return 0


def cmd_sync(args: argparse.Namespace, settings: Settings) -> int:
    """Fetch activities, their FIT files and wellness from the sources."""
    if not settings.has_intervals_credentials:
        print(
            "INTERVALS_API_KEY ist nicht gesetzt — siehe .env.example.",
            file=sys.stderr,
        )
        return 1

    engine = engine_for(settings)
    try:
        report = sync_intervals(engine, settings, full=args.full, force=args.force)
        print(f"intervals.icu: {report.status} — {report.summary()}")

        if settings.garmin_direct_enabled:
            garmin = sync_garmin(engine, settings)
            print(f"Garmin: {garmin.status} — {garmin.summary()}")
    except IngestError as exc:
        print(f"Sync fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    return 0 if report.ok else 1


def cmd_recompute(args: argparse.Namespace, settings: Settings) -> int:
    """Rebuild the derived tables from the stored raw data."""
    engine = engine_for(settings)
    try:
        report = recompute_all(engine)
        snapshot = build_snapshot(engine, weights=settings.readiness_weights)
    finally:
        engine.dispose()

    print(report.summary())
    for note in report.notes:
        print(f"  Hinweis: {note}")
    print(_render_snapshot(snapshot))
    return 0


def _render_metric(
    label: str, result: MetricResult[object], rendered: str | None = None
) -> str:
    """One line per metric: the value, or how far along it is."""
    if result.value is None:
        line = f"{label}: noch nicht verfügbar ({result.have}/{result.required}"
        if result.available_from is not None:
            line += f", frühestens {result.available_from:%d.%m.%Y}"
        line += ")"
        if result.last_data_point is not None:
            # In build-up *and* out of date: both apply per tile, and the
            # date is what tells the athlete which of the two it is.
            line += f" — letzter Wert {result.last_data_point:%d.%m.%Y}"
    elif result.stale and result.last_data_point is not None:
        # A stale value leads with its date; it is not the current state.
        line = (
            f"{label}: Stand {result.last_data_point:%d.%m.%Y} — "
            f"{rendered or result.value}"
        )
    else:
        line = (
            f"{label}: {rendered or result.value} (Konfidenz {result.confidence:.2f})"
        )
    return line


def _render_baseline(result: MetricResult[Baseline]) -> str | None:
    """A baseline as its band, in the units it was built in."""
    baseline = result.value
    if baseline is None:
        return None
    if baseline.log_transformed:
        low, high = math.exp(baseline.lower), math.exp(baseline.upper)
        middle = math.exp(baseline.mean)
    else:
        low, high, middle = baseline.lower, baseline.upper, baseline.mean
    return f"{middle:.1f} (Band {low:.1f} bis {high:.1f}, {baseline.days} Tage)"


def _render_critical_speed(cs_m_s: float, d_prime_m: float) -> str:
    pace = pace_of(cs_m_s)
    if pace is None:
        return f"{cs_m_s:.2f} m/s"
    return f"{int(pace) // 60}:{int(pace) % 60:02d} min/km, D' {d_prime_m:.0f} m"


def _render_snapshot(snapshot: Snapshot) -> str:
    """The headline metrics, as the interface would show them."""
    form = snapshot.form.value
    critical = snapshot.critical_speed.value
    rows = [
        _render_metric("Bereitschaft", snapshot.readiness),
        _render_metric(
            "Formkurve",
            snapshot.form,
            None
            if form is None
            else f"CTL {form.ctl:.1f}, ATL {form.atl:.1f}, Form {form.tsb:+.1f}",
        ),
        _render_metric(
            "ACWR",
            snapshot.acwr,
            None if snapshot.acwr.value is None else f"{snapshot.acwr.value:.2f}",
        ),
        _render_metric("HFV-Baseline", snapshot.hrv, _render_baseline(snapshot.hrv)),
        _render_metric(
            "Ruhe-HF-Baseline",
            snapshot.resting_hr,
            _render_baseline(snapshot.resting_hr),
        ),
        _render_metric(
            "Critical Speed",
            snapshot.critical_speed,
            None
            if critical is None
            else _render_critical_speed(critical.cs_m_s, critical.d_prime_m),
        ),
    ]
    if snapshot.hrv_source_field:
        rows.append(f"HFV-Quellfeld: {snapshot.hrv_source_field}")
    return "\n".join(rows)


def cmd_import_dir(args: argparse.Namespace, settings: Settings) -> int:
    """Import a directory of FIT files, e.g. a Garmin GDPR export."""
    path: Path = args.path
    if not path.is_dir():
        print(f"Kein Verzeichnis: {path}", file=sys.stderr)
        return 1

    engine = engine_for(settings)
    try:
        report = import_fit_directory(engine, settings, path)
    finally:
        engine.dispose()
    print(f"Import: {report.status} — {report.summary()}")
    return 0 if report.ok else 1


# The variable the hash belongs to, and the only line --write touches.
PASSWORD_HASH_VARIABLE = "TEMPO_PASSWORD_HASH"


def compose_escaped(value: str) -> str:
    """Escape a value so an env file survives Docker Compose.

    Compose reads ``$name`` in an env file as a variable and substitutes it
    away. The scrypt hash is six fields joined by ``$``, so pasted by hand
    it arrives truncated — which then looks exactly like a wrong password.
    Doubling the sign is Compose's documented escape, and Tempo understands
    both forms, so the escaped line also works under ``--env-file``.
    """
    return value.replace("$", "$$")


def write_env_value(path: Path, variable: str, value: str) -> bool:
    """Set one variable in an env file. Returns True if a line was replaced.

    The rest of the file is left exactly as it is — comments, order, blank
    lines and every other variable. An env file is hand written, and a tool
    that reformats one is a tool nobody runs twice.
    """
    line = f"{variable}={value}"
    if not path.exists():
        path.write_text(line + "\n", encoding="utf-8")
        return False

    existing = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    for index, current in enumerate(existing):
        if current.lstrip().startswith(f"{variable}="):
            existing[index] = line
            replaced = True
            break
    if not replaced:
        existing.append(line)
    path.write_text("\n".join(existing) + "\n", encoding="utf-8")
    return replaced


def cmd_hash_password(args: argparse.Namespace, settings: Settings) -> int:
    """Derive a password hash for TEMPO_PASSWORD_HASH.

    ``--write`` puts it straight into the env file, escaped. Copying the
    hash by hand is the step that keeps failing: the ``$`` separators get
    eaten by Compose, and a line break inserted by a terminal splits it in
    two — both leave a hash that cannot match any password.
    """
    password = getpass.getpass("Passwort: ")
    repeated = getpass.getpass("Passwort wiederholen: ")
    if password != repeated:
        print("Die Eingaben stimmen nicht überein.", file=sys.stderr)
        return 1
    if not password:
        print("Das Passwort darf nicht leer sein.", file=sys.stderr)
        return 1

    encoded = hash_password(password)
    if not getattr(args, "write", False):
        print("In .env eintragen (die $-Zeichen verdoppeln, siehe README):")
        print(f"{PASSWORD_HASH_VARIABLE}={compose_escaped(encoded)}")
        return 0

    target = Path(args.env_file)
    try:
        replaced = write_env_value(
            target, PASSWORD_HASH_VARIABLE, compose_escaped(encoded)
        )
    except OSError as exc:
        print(f"{target} konnte nicht geschrieben werden: {exc}", file=sys.stderr)
        return 1

    action = "ersetzt" if replaced else "ergänzt"
    print(f"{PASSWORD_HASH_VARIABLE} in {target} {action}.")
    print("Die $-Zeichen sind für Docker Compose verdoppelt — so gehört das.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tempo",
        description="Tempo — selbstgehostete Trainingsanalyse für Läufer.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Ausführliche Protokollausgabe",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Datenverzeichnis und Schema anlegen")
    init.set_defaults(func=cmd_init)

    sync = sub.add_parser("sync", help="Daten von intervals.icu holen")
    sync.add_argument(
        "--full",
        action="store_true",
        help="Vollsync statt inkrementell ab dem Wasserstand",
    )
    sync.add_argument(
        "--force",
        action="store_true",
        help="Auch dann synchronisieren, wenn die letzte Runde unter einer "
        "Stunde her ist",
    )
    sync.set_defaults(func=cmd_sync)

    recompute = sub.add_parser("recompute", help="Kennzahlen neu berechnen")
    recompute.add_argument(
        "--all",
        action="store_true",
        help="Gesamte Historie neu berechnen statt nur der offenen Tage",
    )
    recompute.set_defaults(func=cmd_recompute)

    import_dir = sub.add_parser(
        "import-dir", help="FIT-Dateien aus einem Verzeichnis einlesen"
    )
    import_dir.add_argument("path", type=Path, help="Pfad zum Verzeichnis")
    import_dir.set_defaults(func=cmd_import_dir)

    hash_cmd = sub.add_parser(
        "hash-password", help="Passwort-Hash für TEMPO_PASSWORD_HASH erzeugen"
    )
    hash_cmd.add_argument(
        "--write",
        action="store_true",
        help="den Hash direkt in die .env schreiben, Compose-sicher escaped",
    )
    hash_cmd.add_argument(
        "--env-file",
        default=".env",
        help="welche Datei --write beschreibt (Default: .env)",
    )
    hash_cmd.set_defaults(func=cmd_hash_password)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(logging.DEBUG if args.verbose else logging.INFO)
    settings = get_settings()
    result: int = args.func(args, settings)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
