"""The Alembic migrations apply cleanly and match models.py."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    # A subprocess keeps alembic's logging setup out of the pytest process.
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": db_url},
        capture_output=True,
        text=True,
    )


def test_migrations_round_trip_and_match_models(tmp_path):
    url = f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}"

    for args in (["upgrade", "head"], ["check"], ["downgrade", "base"], ["upgrade", "head"]):
        result = alembic(url, *args)
        assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stderr}"
