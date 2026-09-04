"""
CertificationRepository 테스트.

Certification 테이블 생성, 등록/조회/집계, 필수값·유효기간 검증을 확인합니다.
DB 파일은 tmp_path 로 격리합니다.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from procurement.database.certification_repository import (
    CertificationRepository,
    CertificationValidationError,
)
from procurement.models import Certification


@pytest.fixture
def repo(tmp_path: Path) -> CertificationRepository:
    """테이블이 생성된 CertificationRepository 를 반환합니다."""
    r = CertificationRepository(tmp_path / "test.db")
    r.create_table()
    return r


def _sample(company_id: int = 1, policy_id: int = 1) -> Certification:
    return Certification(
        company_id=company_id,
        policy_id=policy_id,
        certificate_number="CERT-0001",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        issuing_agency="중소벤처기업부",
    )


class TestCreateTable:
    """테이블 생성 및 제약조건을 검증합니다."""

    def test_create_table_is_idempotent(self, tmp_path: Path) -> None:
        r = CertificationRepository(tmp_path / "test.db")
        r.create_table()
        r.create_table()  # 반복 실행해도 오류가 없어야 함
        assert r.count() == 0

    def test_table_exists_after_create(self, repo: CertificationRepository) -> None:
        rows = repo.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='certification'"
        )
        assert len(rows) == 1

    def test_primary_key_defined(self, repo: CertificationRepository) -> None:
        cols = {row["name"]: row for row in repo.execute("PRAGMA table_info(certification)")}
        assert cols["certification_id"]["pk"] == 1

    def test_not_null_columns(self, repo: CertificationRepository) -> None:
        cols = {row["name"]: row for row in repo.execute("PRAGMA table_info(certification)")}
        for name in (
            "company_id",
            "policy_id",
            "valid_from",
            "created_at",
            "updated_at",
        ):
            assert cols[name]["notnull"] == 1, f"{name} 은 NOT NULL 이어야 합니다."
        # 선택 항목은 NULL 허용
        assert cols["certificate_number"]["notnull"] == 0
        assert cols["issuing_agency"]["notnull"] == 0
        # 분류 ② 요구사항 변경 (STEP 108) — 🟢 2026-09-04 고객 확정:
        # "사회적기업과 사회적협동조합은 종료일이 없으며 계속 유효한 것으로
        # 판단한다." 그래서 종료일은 NOT NULL 이 아니게 되었습니다.
        assert cols["valid_to"]["notnull"] == 0

    def test_columns_match_design(self, repo: CertificationRepository) -> None:
        """DATABASE_DESIGN.md 정의 컬럼과 정확히 일치해야 합니다."""
        names = [row["name"] for row in repo.execute("PRAGMA table_info(certification)")]
        assert names == [
            "certification_id",
            "company_id",
            "policy_id",
            "certificate_number",
            "valid_from",
            "valid_to",
            "issuing_agency",
            "created_at",
            "updated_at",
        ]

    def test_no_foreign_keys(self, repo: CertificationRepository) -> None:
        """이번 Issue 범위에서 Foreign Key 제약은 추가하지 않습니다."""
        assert repo.execute("PRAGMA foreign_key_list(certification)") == []


class TestInsert:
    """등록(Insert) 동작을 검증합니다."""

    def test_insert_returns_certification_id(self, repo: CertificationRepository) -> None:
        saved = repo.insert(_sample())
        assert saved.certification_id is not None
        assert saved.certification_id >= 1

    def test_insert_sets_timestamps(self, repo: CertificationRepository) -> None:
        saved = repo.insert(_sample())
        assert isinstance(saved.created_at, datetime)
        assert isinstance(saved.updated_at, datetime)

    def test_insert_persists_row(self, repo: CertificationRepository) -> None:
        repo.insert(_sample())
        assert repo.count() == 1

    def test_insert_without_optional_fields(self, repo: CertificationRepository) -> None:
        """certificate_number / issuing_agency 는 선택 항목입니다."""
        repo.insert(
            Certification(
                company_id=1,
                policy_id=1,
                valid_from=date(2026, 1, 1),
                valid_to=date(2026, 6, 30),
            )
        )
        found = repo.find_by_company(1)
        assert len(found) == 1
        assert found[0].certificate_number is None
        assert found[0].issuing_agency is None

    def test_same_company_multiple_certifications(self, repo: CertificationRepository) -> None:
        """하나의 기업은 여러 개의 인증을 보유할 수 있습니다."""
        repo.insert(_sample(company_id=10, policy_id=1))
        repo.insert(_sample(company_id=10, policy_id=2))
        assert len(repo.find_by_company(10)) == 2


class TestFindById:
    """단건 조회를 검증합니다."""

    def test_find_by_id(self, repo: CertificationRepository) -> None:
        saved = repo.insert(_sample(company_id=7, policy_id=3))
        assert saved.certification_id is not None
        found = repo.find_by_id(saved.certification_id)
        assert found is not None
        assert found.company_id == 7
        assert found.policy_id == 3
        assert found.certificate_number == "CERT-0001"
        assert found.issuing_agency == "중소벤처기업부"

    def test_find_by_id_missing_returns_none(self, repo: CertificationRepository) -> None:
        assert repo.find_by_id(99999) is None

    def test_roundtrip_dates(self, repo: CertificationRepository) -> None:
        saved = repo.insert(_sample())
        assert saved.certification_id is not None
        found = repo.find_by_id(saved.certification_id)
        assert found is not None
        assert found.valid_from == date(2026, 1, 1)
        assert found.valid_to == date(2026, 12, 31)

    def test_roundtrip_timestamps(self, repo: CertificationRepository) -> None:
        saved = repo.insert(_sample())
        assert saved.certification_id is not None
        found = repo.find_by_id(saved.certification_id)
        assert found is not None
        assert found.created_at == saved.created_at
        assert found.updated_at == saved.updated_at


class TestFindByCompany:
    """기업별 조회를 검증합니다."""

    def test_returns_only_matching_company(self, repo: CertificationRepository) -> None:
        repo.insert(_sample(company_id=1, policy_id=1))
        repo.insert(_sample(company_id=1, policy_id=2))
        repo.insert(_sample(company_id=2, policy_id=1))
        result = repo.find_by_company(1)
        assert len(result) == 2
        assert all(c.company_id == 1 for c in result)

    def test_returns_empty_list_when_none(self, repo: CertificationRepository) -> None:
        assert repo.find_by_company(12345) == []


class TestFindByPolicy:
    """정책별 조회를 검증합니다."""

    def test_returns_only_matching_policy(self, repo: CertificationRepository) -> None:
        repo.insert(_sample(company_id=1, policy_id=5))
        repo.insert(_sample(company_id=2, policy_id=5))
        repo.insert(_sample(company_id=3, policy_id=6))
        result = repo.find_by_policy(5)
        assert len(result) == 2
        assert all(c.policy_id == 5 for c in result)

    def test_returns_empty_list_when_none(self, repo: CertificationRepository) -> None:
        assert repo.find_by_policy(12345) == []


class TestCount:
    """등록 인증 수 집계를 검증합니다."""

    def test_count_zero(self, repo: CertificationRepository) -> None:
        assert repo.count() == 0

    def test_count_multiple(self, repo: CertificationRepository) -> None:
        repo.insert(_sample(company_id=1))
        repo.insert(_sample(company_id=2))
        repo.insert(_sample(company_id=3))
        assert repo.count() == 3


class TestRequiredValidation:
    """필수값 검증(None 금지)을 확인합니다."""

    @pytest.mark.parametrize("field", ["company_id", "policy_id", "valid_from"])
    def test_none_required_field_raises(self, repo: CertificationRepository, field: str) -> None:
        cert = _sample()
        setattr(cert, field, None)
        with pytest.raises(CertificationValidationError):
            repo.insert(cert)

    def test_valid_to_is_not_required(self, repo: CertificationRepository) -> None:
        """종료일은 필수가 아니다 — 없으면 **계속 유효**한 인증이다.

        분류 ② 요구사항 변경 (STEP 108). 🟢 2026-09-04 고객 확정:
        *"사회적기업과 사회적협동조합은 종료일이 없으며 계속 유효한 것으로
        판단한다."* ⛔ 없는 종료일을 지어내어 채우지 않습니다.
        """
        cert = _sample()
        cert.valid_to = None
        stored = repo.insert(cert)
        assert stored.valid_to is None
        assert repo.count() == 1

    def test_validation_failure_persists_nothing(self, repo: CertificationRepository) -> None:
        cert = _sample()
        cert.company_id = None  # type: ignore[assignment]
        with pytest.raises(CertificationValidationError):
            repo.insert(cert)
        assert repo.count() == 0


class TestDateValidation:
    """유효기간 검증을 확인합니다."""

    def test_valid_to_before_valid_from_raises(self, repo: CertificationRepository) -> None:
        with pytest.raises(CertificationValidationError):
            repo.insert(
                Certification(
                    company_id=1,
                    policy_id=1,
                    valid_from=date(2026, 12, 31),
                    valid_to=date(2026, 1, 1),
                )
            )

    def test_same_day_is_allowed(self, repo: CertificationRepository) -> None:
        """valid_to == valid_from 은 '이전'이 아니므로 허용합니다."""
        saved = repo.insert(
            Certification(
                company_id=1,
                policy_id=1,
                valid_from=date(2026, 5, 1),
                valid_to=date(2026, 5, 1),
            )
        )
        assert saved.certification_id is not None

    def test_date_validation_failure_persists_nothing(self, repo: CertificationRepository) -> None:
        with pytest.raises(CertificationValidationError):
            repo.insert(
                Certification(
                    company_id=1,
                    policy_id=1,
                    valid_from=date(2026, 12, 31),
                    valid_to=date(2026, 1, 1),
                )
            )
        assert repo.count() == 0
