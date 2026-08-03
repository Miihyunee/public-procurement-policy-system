"""
CompanyRepository 테스트.

Company 테이블 생성, 등록/조회/집계, 중복·필수값 예외를 검증합니다.
DB 파일은 tmp_path 로 격리합니다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from procurement.database.company_repository import (
    CompanyRepository,
    CompanyValidationError,
    DuplicateBusinessNoError,
)
from procurement.models import Company


@pytest.fixture
def repo(tmp_path: Path) -> CompanyRepository:
    """테이블이 생성된 CompanyRepository 를 반환합니다."""
    r = CompanyRepository(tmp_path / "test.db")
    r.create_table()
    return r


def _sample(business_no: str = "1234567890") -> Company:
    return Company(
        business_no=business_no,
        company_name="테스트기업",
        representative_name="홍길동",
    )


class TestCreateTable:
    """테이블 생성 및 제약조건을 검증합니다."""

    def test_create_table_is_idempotent(self, tmp_path: Path) -> None:
        r = CompanyRepository(tmp_path / "test.db")
        r.create_table()
        r.create_table()  # 반복 호출해도 예외가 없어야 함
        assert r.count() == 0

    def test_table_exists_after_create(self, repo: CompanyRepository) -> None:
        rows = repo.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='company'")
        assert len(rows) == 1

    def test_primary_key_and_unique_defined(self, repo: CompanyRepository) -> None:
        cols = {row["name"]: row for row in repo.execute("PRAGMA table_info(company)")}
        assert cols["company_id"]["pk"] == 1
        # UNIQUE 인덱스가 business_no 에 존재하는지 확인
        indexes = repo.execute("PRAGMA index_list(company)")
        assert any(idx["unique"] == 1 for idx in indexes)


class TestInsert:
    """등록(Insert) 동작을 검증합니다."""

    def test_insert_returns_company_id(self, repo: CompanyRepository) -> None:
        saved = repo.insert(_sample())
        assert saved.company_id is not None
        assert saved.company_id >= 1

    def test_insert_sets_timestamps(self, repo: CompanyRepository) -> None:
        saved = repo.insert(_sample())
        assert isinstance(saved.created_at, datetime)
        assert isinstance(saved.updated_at, datetime)

    def test_insert_persists_row(self, repo: CompanyRepository) -> None:
        repo.insert(_sample())
        assert repo.count() == 1


class TestExists:
    """존재 여부 확인을 검증합니다."""

    def test_exists_true(self, repo: CompanyRepository) -> None:
        repo.insert(_sample("1112223334"))
        assert repo.exists("1112223334") is True

    def test_exists_false(self, repo: CompanyRepository) -> None:
        assert repo.exists("0000000000") is False


class TestFind:
    """조회(Find) 동작을 검증합니다."""

    def test_find_by_business_no(self, repo: CompanyRepository) -> None:
        repo.insert(_sample("5556667778"))
        found = repo.find_by_business_no("5556667778")
        assert found is not None
        assert found.company_name == "테스트기업"
        assert found.representative_name == "홍길동"

    def test_find_by_business_no_missing_returns_none(self, repo: CompanyRepository) -> None:
        assert repo.find_by_business_no("9998887776") is None

    def test_find_by_id(self, repo: CompanyRepository) -> None:
        saved = repo.insert(_sample("6667778889"))
        assert saved.company_id is not None
        found = repo.find_by_id(saved.company_id)
        assert found is not None
        assert found.business_no == "6667778889"

    def test_find_by_id_missing_returns_none(self, repo: CompanyRepository) -> None:
        assert repo.find_by_id(99999) is None

    def test_roundtrip_timestamps(self, repo: CompanyRepository) -> None:
        saved = repo.insert(_sample("7778889990"))
        found = repo.find_by_business_no("7778889990")
        assert found is not None
        assert found.created_at == saved.created_at
        assert found.updated_at == saved.updated_at


class TestCount:
    """등록 기업 수 집계를 검증합니다."""

    def test_count_zero(self, repo: CompanyRepository) -> None:
        assert repo.count() == 0

    def test_count_multiple(self, repo: CompanyRepository) -> None:
        repo.insert(_sample("1000000001"))
        repo.insert(_sample("1000000002"))
        repo.insert(_sample("1000000003"))
        assert repo.count() == 3


class TestDuplicateBusinessNo:
    """사업자등록번호 UNIQUE 제약을 검증합니다."""

    def test_duplicate_raises(self, repo: CompanyRepository) -> None:
        repo.insert(_sample("2223334445"))
        with pytest.raises(DuplicateBusinessNoError):
            repo.insert(_sample("2223334445"))

    def test_duplicate_does_not_increase_count(self, repo: CompanyRepository) -> None:
        repo.insert(_sample("3334445556"))
        with pytest.raises(DuplicateBusinessNoError):
            repo.insert(_sample("3334445556"))
        assert repo.count() == 1


class TestRequiredValidation:
    """필수값 검증을 확인합니다."""

    def test_missing_business_no(self, repo: CompanyRepository) -> None:
        with pytest.raises(CompanyValidationError):
            repo.insert(Company(business_no="", company_name="A", representative_name="B"))

    def test_missing_company_name(self, repo: CompanyRepository) -> None:
        with pytest.raises(CompanyValidationError):
            repo.insert(
                Company(
                    business_no="1234509876",
                    company_name="   ",
                    representative_name="B",
                )
            )

    def test_missing_representative_name(self, repo: CompanyRepository) -> None:
        with pytest.raises(CompanyValidationError):
            repo.insert(
                Company(
                    business_no="1234509877",
                    company_name="A",
                    representative_name="",
                )
            )

    def test_validation_failure_persists_nothing(self, repo: CompanyRepository) -> None:
        with pytest.raises(CompanyValidationError):
            repo.insert(Company(business_no="", company_name="A", representative_name="B"))
        assert repo.count() == 0
