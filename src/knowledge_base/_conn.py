"""Thread-local SQLite connection with double-checked locking for schema init."""

from __future__ import annotations

import threading

from .db import get_connection, init_schema

_local = threading.local()
_schema_lock = threading.Lock()
_schema_ready = False


def _get_conn():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = get_connection()
        global _schema_ready
        if not _schema_ready:
            with _schema_lock:
                if not _schema_ready:
                    init_schema(conn)
                    _schema_ready = True
        _local.conn = conn
    return conn
