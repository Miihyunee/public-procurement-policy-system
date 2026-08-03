"""
PolicyRepository 테스트.

Policy 테이블 생성, 등록/조회/집계, 중복·필수값 예외를 검증합니다.
DB 파일은 tmp_path 로 격리합니다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from procurement.database.policy_repository import (
    DuplicatePolicyCodeError,
    PolicyRepository,
    PolicyValidationError,
)
from procurement.models import Policy


@pytest.fixture
def repo(tmp_path: Path) -> PolicyRepository:
    """테이블이 생성된 PolicyRepository 를 반환합니다."""
    r = PolicyRepository(tmp_path / "test.db")
    r.create_table()
    return r


def _sample(policy_code: str = "SMALL_BIZ") -> Policy:
    return Policy(
        policy_code=policy_code,
        policy_name="중소기업",
        description="중소기업 우선구매 정책",
    )


class TestCreateTable:
    """테이블 생성 및 제약조건을 검증합니다."""

    def test_create_table_is_idempotent(self, tmp_path: Path) -> None:
        r = PolicyRepository(tmp_path / "test.db")
        r.create_table()
        r.create_table()  # 반복 호출해도 예외가 없어야 함
        assert r.count() == 0

    def test_table_exists_after_create(self, repo: PolicyRepository) -> None:
        rows = repo.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='policy'")
        assert len(rows) == 1

    def test_primary_key_defined(self, repo: PolicyRepository) -> None:
        cols = {row["name"]: row for row in repo.execute("PRAGMA table_info(policy)")}
        assert cols["policy_id"]["pk"] == 1

    def test_unique_index_defined(self, repo: PolicyRepository) -> None:
        indexes = repo.execute("PRAGMA index_list(policy)")
        assert any(idx["unique"] == 1 for idx in indexes)

    def test_not_null_columns(self, repo: PolicyRepository) -> None:
        cols = {row["name"]: row for row in repo.execute("PRAGMA table_info(policy)")}
        for name in ("policy_code", "policy_name", "is_active", "created_at", "updated_at"):
            assert cols[name]["notnull"] == 1, f"{name} 은 NOT NULL 이어야 합니다."
        # description 은 선택 항목이므로 NULL 허용
        assert cols["description"]["notnull"] == 0

    def test_columns_match_design(self, repo: PolicyRepository) -> None:
        """DATABASE_DESIGN.md 정의 컬럼과 정확히 일치해야 합니다."""
        names = [row["name"] for row in repo.execute("PRAGMA table_info(policy)")]
        assert names == [
            "policy_id",
            "policy_code",
            "policy_name",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]


class TestInsert:
    """등록(Insert) 동작을 검증합니다."""

    def test_insert_returns_policy_id(self, repo: PolicyRepository) -> None:
        saved = repo.insert(_sample())
        assert saved.policy_id is not None
        assert saved.policy_id >= 1

    def test_insert_sets_timestamps(self, repo: PolicyRepository) -> None:
        saved = repo.insert(_sample())
        assert isinstance(saved.created_at, datetime)
        assert isinstance(saved.updated_at, datetime)

    def test_insert_persists_row(self, repo: PolicyRepository) -> None:
        repo.insert(_sample())
        assert repo.count() == 1

    def test_insert_without_description(self, repo: PolicyRepository) -> None:
        """description 은 선택 항목이므로 없어도 저장되어야 합니다."""
        saved = repo.insert(Policy(policy_code="GREEN", policy_name="녹색제품"))
        found = repo.find_by_policy_code("GREEN")
        assert saved.description is None
        assert found is not None
        assert found.description is None

    def test_insert_is_active_default_true(self, repo: PolicyRepository) -> None:
        repo.insert(_sample("WOMAN"))
        found = repo.find_by_policy_code("WOMAN")
        assert found is not None
        assert found.is_active is True

    def test_insert_is_active_false(self, repo: PolicyRepository) -> None:
        repo.insert(Policy(policy_code="OLD", policy_name="폐지정책", is_active=False))
        found = repo.find_by_policy_code("OLD")
        assert found is not None
        assert found.is_active is False


class TestExists:
    """존재 여부 확인을 검증합니다."""

    def test_exists_true(self, repo: PolicyRepository) -> None:
        repo.insert(_sample("DISABLED_BIZ"))
        assert repo.exists("DISABLED_BIZ") is True

    def test_exists_false(self, repo: PolicyRepository) -> None:
        assert repo.exists("NOT_EXIST") is False


class TestFind:
    """조회(Find) 동작을 검증합니다."""

    def test_find_by_policy_code(self, repo: PolicyRepository) -> None:
        repo.insert(_sample("STARTUP"))
        found = repo.find_by_policy_code("STARTUP")
        assert found is not None
        assert found.policy_name == "중소기업"
        assert found.description == "중소기업 우선구매 정책"

    def test_find_by_policy_code_missing_returns_none(self, repo: PolicyRepository) -> None:
        assert repo.find_by_policy_code("NONE") is None

    def test_find_by_id(self, repo: PolicyRepository) -> None:
        saved = repo.insert(_sample("GREEN_PRODUCT"))
        assert saved.policy_id is not None
        found = repo.find_by_id(saved.policy_id)
        assert found is not None
        assert found.policy_code == "GREEN_PRODUCT"

    def test_find_by_id_missing_returns_none(self, repo: PolicyRepository) -> None:
        assert repo.find_by_id(99999) is None

    def test_roundtrip_timestamps(self, repo: PolicyRepository) -> None:
        saved = repo.insert(_sample("ROUNDTRIP"))
        found = repo.find_by_policy_code("ROUNDTRIP")
        assert found is not None
        assert found.created_at == saved.created_at
        assert found.updated_at == saved.updated_at


class TestCount:
    """등록 정책 수 집계를 검증합니다."""

    def test_count_zero(self, repo: PolicyRepository) -> None:
        assert repo.count() == 0

    def test_count_multiple(self, repo: PolicyRepository) -> None:
        repo.insert(_sample("P1"))
        repo.insert(_sample("P2"))
        repo.insert(_sample("P3"))
        assert repo.count() == 3


class TestDuplicatePolicyCode:
    """정책 코드 UNIQUE 제약을 검증합니다."""

    def test_duplicate_raises(self, repo: PolicyRepository) -> None:
        repo.insert(_sample("DUP"))
        with pytest.raises(DuplicatePolicyCodeError):
            repo.insert(_sample("DUP"))

    def test_duplicate_does_not_increase_count(self, repo: PolicyRepository) -> None:
        repo.insert(_sample("DUP2"))
        with pytest.raises(DuplicatePolicyCodeError):
            repo.insert(_sample("DUP2"))
        assert repo.count() == 1


class TestRequiredValidation:
    """필수값 검증을 확인합니다."""

    def test_missing_policy_code(self, repo: PolicyRepository) -> None:
        with pytest.raises(PolicyValidationError):
            repo.insert(Policy(policy_code="", policy_name="정책명"))

    def test_blank_policy_code(self, repo: PolicyRepository) -> None:
        with pytest.raises(PolicyValidationError):
            repo.insert(Policy(policy_code="   ", policy_name="정책명"))

    def test_missing_policy_name(self, repo: PolicyRepository) -> None:
        with pytest.raises(PolicyValidationError):
            repo.insert(Policy(policy_code="CODE1", policy_name=""))

    def test_blank_policy_name(self, repo: PolicyRepository) -> None:
        with pytest.raises(PolicyValidationError):
            repo.insert(Policy(policy_code="CODE2", policy_name="   "))

    def test_validation_failure_persists_nothing(self, repo: PolicyRepository) -> None:
        with pytest.raises(PolicyValidationError):
            repo.insert(Policy(policy_code="", policy_name="정책명"))
        assert repo.count() == 0
