"""
procurement.database.connection

SQLite 데이터베이스 연결을 관리합니다.

DB 파일 경로는 :data:`procurement.core.config.settings` 의 ``db_file`` 을
기준으로 결정되며, 컨텍스트 매니저를 통해 연결의 생성/커밋/롤백/종료를
안전하게 처리합니다.

사용 예:
    from procurement.database import get_connection

    with get_connection() as conn:
        rows = conn.execute("SELECT 1").fetchall()
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from procurement.core.config import settings


def _resolve_db_path(db_path: str | Path | None) -> Path:
    """사용할 DB 파일 경로를 결정합니다.

    Args:
        db_path: 명시적으로 지정한 DB 파일 경로. ``None`` 이면 설정값을 사용합니다.

    Returns:
        최종 DB 파일 경로.
    """
    return Path(db_path) if db_path is not None else settings.db_file


def create_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """SQLite 연결 객체를 생성합니다.

    상위 디렉터리가 없으면 생성하며, ``row_factory`` 를 :class:`sqlite3.Row`
    로 설정하고 외래 키 제약을 활성화합니다.

    Args:
        db_path: DB 파일 경로. ``None`` 이면 ``settings.db_file`` 을 사용합니다.

    Returns:
        구성이 완료된 :class:`sqlite3.Connection` 객체.
    """
    target = _resolve_db_path(db_path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_connection(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """트랜잭션 범위를 갖는 SQLite 연결 컨텍스트 매니저.

    블록이 정상 종료되면 커밋하고, 예외가 발생하면 롤백합니다.
    어느 경우든 연결은 반드시 닫힙니다.

    Args:
        db_path: DB 파일 경로. ``None`` 이면 ``settings.db_file`` 을 사용합니다.

    Yields:
        활성화된 :class:`sqlite3.Connection` 객체.
    """
    conn = create_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
