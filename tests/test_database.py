"""
Database 기반 계층 테스트.

연결 관리(connection)와 BaseRepository 의 공통 동작을 검증합니다.
실제 DB 파일은 tmp_path 를 사용하여 격리합니다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from procurement.database import BaseRepository, create_connection, get_connection


class TestCreateConnection:
    """create_connection 동작을 검증합니다."""

    def test_returns_connection(self, tmp_path: Path) -> None:
        conn = create_connection(tmp_path / "test.db")
        try:
            assert isinstance(conn, sqlite3.Connection)
        finally:
            conn.close()

    def test_row_factory_is_row(self, tmp_path: Path) -> None:
        conn = create_connection(tmp_path / "test.db")
        try:
            assert conn.row_factory is sqlite3.Row
        finally:
            conn.close()

    def test_foreign_keys_enabled(self, tmp_path: Path) -> None:
        conn = create_connection(tmp_path / "test.db")
        try:
            (value,) = conn.execute("PRAGMA foreign_keys").fetchone()
            assert value == 1
        finally:
            conn.close()

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nested" / "dir" / "test.db"
        conn = create_connection(db_path)
        try:
            assert db_path.parent.exists()
        finally:
            conn.close()

    def test_creates_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        conn = create_connection(db_path)
        try:
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.commit()
        finally:
            conn.close()
        assert db_path.exists()


class TestGetConnection:
    """get_connection 컨텍스트 매니저 동작을 검증합니다."""

    def test_yields_connection(self, tmp_path: Path) -> None:
        with get_connection(tmp_path / "test.db") as conn:
            assert isinstance(conn, sqlite3.Connection)

    def test_commit_on_success(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.execute("INSERT INTO t (id) VALUES (1)")

        # 별도 연결에서 커밋 여부 확인
        with get_connection(db_path) as conn:
            rows = conn.execute("SELECT id FROM t").fetchall()
        assert [row["id"] for row in rows] == [1]

    def test_rollback_on_exception(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with get_connection(db_path) as conn:
            conn.execute("CREATE TABLE t (id INTEGER)")

        with pytest.raises(ValueError):
            with get_connection(db_path) as conn:
                conn.execute("INSERT INTO t (id) VALUES (1)")
                raise ValueError("boom")

        with get_connection(db_path) as conn:
            rows = conn.execute("SELECT id FROM t").fetchall()
        assert rows == []

    def test_connection_closed_after_block(self, tmp_path: Path) -> None:
        with get_connection(tmp_path / "test.db") as conn:
            pass
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


class _DummyRepository(BaseRepository):
    """테스트용 구체 Repository."""

    table_name = "dummy"


class TestBaseRepository:
    """BaseRepository 공통 헬퍼 동작을 검증합니다."""

    def _setup_table(self, db_path: Path) -> None:
        with get_connection(db_path) as conn:
            conn.execute("CREATE TABLE dummy (id INTEGER PRIMARY KEY, name TEXT)")

    def test_table_name_default_is_empty(self) -> None:
        assert BaseRepository.table_name == ""

    def test_subclass_table_name(self) -> None:
        assert _DummyRepository.table_name == "dummy"

    def test_connection_context(self, tmp_path: Path) -> None:
        repo = _DummyRepository(tmp_path / "test.db")
        with repo.connection() as conn:
            assert isinstance(conn, sqlite3.Connection)

    def test_execute_write_and_read(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        self._setup_table(db_path)
        repo = _DummyRepository(db_path)

        affected = repo.execute_write("INSERT INTO dummy (name) VALUES (?)", ("alpha",))
        assert affected == 1

        rows = repo.execute("SELECT name FROM dummy")
        assert [row["name"] for row in rows] == ["alpha"]

    def test_execute_write_with_dict_params(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        self._setup_table(db_path)
        repo = _DummyRepository(db_path)

        repo.execute_write("INSERT INTO dummy (name) VALUES (:name)", {"name": "beta"})
        rows = repo.execute("SELECT name FROM dummy WHERE name = :name", {"name": "beta"})
        assert len(rows) == 1

    def test_execute_returns_empty_list(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        self._setup_table(db_path)
        repo = _DummyRepository(db_path)
        assert repo.execute("SELECT * FROM dummy") == []
