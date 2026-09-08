"""Test suite.

Tests run against synthetic fixtures and temporary databases only — never
against the production database, and never against the network.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
