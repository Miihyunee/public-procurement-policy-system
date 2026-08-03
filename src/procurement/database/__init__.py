"""
procurement.database

데이터베이스 접근 계층 패키지.

연결 관리와 Repository 기반 클래스를 제공합니다::

    from procurement.database import get_connection, BaseRepository

    with get_connection() as conn:
        conn.execute("SELECT 1")
"""

from procurement.database.base import BaseRepository
from procurement.database.connection import create_connection, get_connection

__all__ = ["BaseRepository", "create_connection", "get_connection"]
