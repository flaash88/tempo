"""Command line interface.

Operator-facing output is German, like the rest of the interface. Code and
comments stay English.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from pathlib import Path

from tempo import __version__
from tempo.api.auth import hash_password
from tempo.config import Settings, get_settings
from tempo.db.migrate import current_revision, upgrade_to_head
from tempo.db.session import engine_for, ensure_data_dirs
from tempo.logging import configure_logging

log = logging.getLogger("tempo.cli")

# Exit code for a command whose phase has not been implemented yet. Distinct
# from 1 so that scripts can tell "not built" apart from "failed".
EXIT_NOT_IMPLEMENTED = 2


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
    """Fetch activities, streams and wellness from the configured sources."""
    print(
        "Sync ist noch nicht implementiert — kommt in Phase 2 (Ingestion).",
        file=sys.stderr,
    )
    return EXIT_NOT_IMPLEMENTED


def cmd_recompute(args: argparse.Namespace, settings: Settings) -> int:
    """Recompute the derived metrics from the stored raw data."""
    print(
        "Neuberechnung ist noch nicht implementiert — kommt in Phase 3 "
        "(Metrik-Engine).",
        file=sys.stderr,
    )
    return EXIT_NOT_IMPLEMENTED


def cmd_import_dir(args: argparse.Namespace, settings: Settings) -> int:
    """Import a directory of FIT files, e.g. a Garmin GDPR export."""
    path: Path = args.path
    if not path.is_dir():
        print(f"Kein Verzeichnis: {path}", file=sys.stderr)
        return 1
    print(
        "Import ist noch nicht implementiert — kommt in Phase 2 (FIT-Parser).",
        file=sys.stderr,
    )
    return EXIT_NOT_IMPLEMENTED


def cmd_hash_password(args: argparse.Namespace, settings: Settings) -> int:
    """Derive a password hash for TEMPO_PASSWORD_HASH."""
    password = getpass.getpass("Passwort: ")
    repeated = getpass.getpass("Passwort wiederholen: ")
    if password != repeated:
        print("Die Eingaben stimmen nicht überein.", file=sys.stderr)
        return 1
    if not password:
        print("Das Passwort darf nicht leer sein.", file=sys.stderr)
        return 1
    print("In .env eintragen:")
    print(f"TEMPO_PASSWORD_HASH={hash_password(password)}")
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
