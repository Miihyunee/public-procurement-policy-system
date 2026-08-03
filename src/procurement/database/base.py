"""
procurement.database.base

모든 Repository 의 공통 기반 클래스를 정의합니다.

Repository 는 특정 테이블에 대한 데이터 접근 로직을 캡슐화하며,
:class:`BaseRepository` 는 연결 획득 및 공통 실행 헬퍼를 제공합니다.

구체 Repository (예: ``CompanyRepository``) 는 본 클래스를 상속하여
``table_name`` 을 지정하고 도메인별 메서드를 구현합니다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from procurement.database.connection import get_connection


class BaseRepository:
    """Repository 공통 기반 클래스.

    Attributes:
        table_name: Repository 가 다루는 테이블명. 하위 클래스에서 지정합니다.
    """

    #: 하위 클래스가 재정의해야 하는 대상 테이블명.
    table_name: str = ""

    def __init__(self, db_path: str | Path | None = None) -> None:
        """Repository 를 초기화합니다.

        Args:
            db_path: 사용할 DB 파일 경로. ``None`` 이면 설정값을 사용합니다.
        """
        self._db_path = db_path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """트랜잭션 범위의 연결을 제공하는 컨텍스트 매니저.

        Yields:
            활성화된 :class:`sqlite3.Connection` 객체.
        """
        with get_connection(self._db_path) as conn:
            yield conn

    def execute(
        self, query: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[sqlite3.Row]:
        """SELECT 계열 쿼리를 실행하고 전체 결과를 반환합니다.

        Args:
            query: 실행할 SQL 문자열.
            params: 바인딩 파라미터.

        Returns:
            조회된 행(:class:`sqlite3.Row`) 목록.
        """
        with self.connection() as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchall()

    def execute_write(self, query: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> int:
        """INSERT/UPDATE/DELETE 계열 쿼리를 실행합니다.

        Args:
            query: 실행할 SQL 문자열.
            params: 바인딩 파라미터.

        Returns:
            영향을 받은 행의 수(``cursor.rowcount``).
        """
        with self.connection() as conn:
            cursor = conn.execute(query, params)
            return cursor.rowcount
