"""Alembic stores revision IDs in VARCHAR(32) by default."""
from __future__ import annotations

import re
from pathlib import Path


VERSIONS = Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions"


def test_all_alembic_revision_ids_fit_default_version_column():
    for path in sorted(VERSIONS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        match = re.search(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", source, re.MULTILINE)
        if not match:
            continue
        revision = match.group(1)
        assert len(revision) <= 32, (
            f"{path.name} revision ID is {len(revision)} chars; "
            "Alembic's default alembic_version.version_num is VARCHAR(32)"
        )
