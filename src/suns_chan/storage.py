"""Database abstraction. App code uses Database + repositories, not raw SQL everywhere."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable


class Database:
    """Thin wrapper over sqlite3 with explicit lifecycle. Future backends
    (e.g. Postgres) implement this same small surface."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row

    @property
    def path(self) -> Path:
        return self._path

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        cur = self._conn.execute(sql, tuple(params))
        self._conn.commit()
        return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, tuple(params)).fetchall()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
