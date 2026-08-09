"""
PolicyRepository 테스트.

Policy 테이블 생성, 등록/조회/집계, 중복·필수값 예외를 검증합니다.
DB 파일은 tmp_path 로 격리합니다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.database.policy_repository import (
    TARGET_RATE_MAX,
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
        for name in (
            "policy_code",
            "policy_name",
            "is_active",
            "evaluation_basis",
            "created_at",
            "updated_at",
        ):
            assert cols[name]["notnull"] == 1, f"{name} 은 NOT NULL 이어야 합니다."
        # description, target_rate 은 선택 항목이므로 NULL 허용
        assert cols["description"]["notnull"] == 0
        assert cols["target_rate"]["notnull"] == 0

    def test_columns_match_design(self, repo: PolicyRepository) -> None:
        """DATABASE_DESIGN.md v1.1 정의 컬럼과 정확히 일치해야 합니다."""
        names = [row["name"] for row in repo.execute("PRAGMA table_info(policy)")]
        assert names == [
            "policy_id",
            "policy_code",
            "policy_name",
            "description",
            "is_active",
            "evaluation_basis",
            "target_rate",
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


class TestEvaluationBasis:
    """evaluation_basis 저장·검증·복원을 검증합니다."""

    def test_default_is_payment_date(self, repo: PolicyRepository) -> None:
        """미지정 시 기본값 PAYMENT_DATE 로 저장됩니다."""
        repo.insert(_sample("DEFAULT_BASIS"))
        found = repo.find_by_policy_code("DEFAULT_BASIS")
        assert found is not None
        assert found.evaluation_basis == "PAYMENT_DATE"

    def test_store_payment_date(self, repo: PolicyRepository) -> None:
        repo.insert(
            Policy(policy_code="WOMAN", policy_name="여성기업", evaluation_basis="PAYMENT_DATE")
        )
        found = repo.find_by_policy_code("WOMAN")
        assert found is not None
        assert found.evaluation_basis == "PAYMENT_DATE"

    def test_store_contract_date(self, repo: PolicyRepository) -> None:
        repo.insert(
            Policy(policy_code="STARTUP", policy_name="창업기업", evaluation_basis="CONTRACT_DATE")
        )
        found = repo.find_by_policy_code("STARTUP")
        assert found is not None
        assert found.evaluation_basis == "CONTRACT_DATE"

    def test_row_mapping_includes_evaluation_basis(self, repo: PolicyRepository) -> None:
        """Row Mapping 이 evaluation_basis 를 포함해 복원해야 합니다."""
        saved = repo.insert(
            Policy(
                policy_code="DISABLED", policy_name="장애인기업", evaluation_basis="CONTRACT_DATE"
            )
        )
        assert saved.policy_id is not None
        # insert 반환값과 find_by_id 조회값 모두 동일 값을 가져야 함
        assert saved.evaluation_basis == "CONTRACT_DATE"
        found = repo.find_by_id(saved.policy_id)
        assert found is not None
        assert found.evaluation_basis == "CONTRACT_DATE"

    def test_invalid_value_raises(self, repo: PolicyRepository) -> None:
        with pytest.raises(PolicyValidationError):
            repo.insert(
                Policy(policy_code="BAD", policy_name="잘못된정책", evaluation_basis="SOMETHING")
            )

    def test_empty_value_raises(self, repo: PolicyRepository) -> None:
        with pytest.raises(PolicyValidationError):
            repo.insert(Policy(policy_code="EMPTY_BASIS", policy_name="빈값", evaluation_basis=""))

    def test_vendor_existence_not_allowed_in_mvp(self, repo: PolicyRepository) -> None:
        """VENDOR_EXISTENCE 는 MVP 범위 밖이므로 허용되지 않습니다."""
        with pytest.raises(PolicyValidationError):
            repo.insert(
                Policy(
                    policy_code="SELF_SUPPORT",
                    policy_name="자활용사촌",
                    evaluation_basis="VENDOR_EXISTENCE",
                )
            )

    def test_invalid_value_persists_nothing(self, repo: PolicyRepository) -> None:
        with pytest.raises(PolicyValidationError):
            repo.insert(
                Policy(policy_code="BAD2", policy_name="잘못된정책", evaluation_basis="XXX")
            )
        assert repo.count() == 0

    def test_column_not_null_rejects_direct_null(self, repo: PolicyRepository) -> None:
        """스키마 레벨에서 evaluation_basis 는 NOT NULL 이어야 합니다."""
        cols = {row["name"]: row for row in repo.execute("PRAGMA table_info(policy)")}
        assert cols["evaluation_basis"]["notnull"] == 1


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


class TestTargetRate:
    """목표율(target_rate) 저장·조회·검증을 확인합니다 (NULL 허용)."""

    def test_default_target_rate_is_none(self, repo: PolicyRepository) -> None:
        """미지정 시 목표율은 NULL(None) 로 저장됩니다."""
        saved = repo.insert(_sample("NO_RATE"))
        assert saved.target_rate is None
        found = repo.find_by_policy_code("NO_RATE")
        assert found is not None
        assert found.target_rate is None

    def test_insert_with_target_rate(self, repo: PolicyRepository) -> None:
        """목표율을 지정하면 그대로 저장·조회됩니다."""
        repo.insert(Policy(policy_code="RATE50", policy_name="중소기업", target_rate=Decimal("50")))
        found = repo.find_by_policy_code("RATE50")
        assert found is not None
        assert found.target_rate == Decimal("50")

    def test_target_rate_decimal_precision(self, repo: PolicyRepository) -> None:
        """소수 목표율도 정밀도 손실 없이 저장·복원됩니다."""
        repo.insert(
            Policy(policy_code="RATE_DEC", policy_name="여성기업", target_rate=Decimal("12.34"))
        )
        found = repo.find_by_policy_code("RATE_DEC")
        assert found is not None
        assert found.target_rate == Decimal("12.34")

    def test_returned_policy_carries_target_rate(self, repo: PolicyRepository) -> None:
        """insert 반환값에도 목표율이 반영됩니다."""
        saved = repo.insert(
            Policy(policy_code="RATE_RET", policy_name="장애인기업", target_rate=Decimal("20"))
        )
        assert saved.target_rate == Decimal("20")

    def test_zero_target_rate_raises(self, repo: PolicyRepository) -> None:
        """목표율이 0 이면 검증 예외가 발생합니다."""
        with pytest.raises(PolicyValidationError):
            repo.insert(Policy(policy_code="RATE0", policy_name="정책", target_rate=Decimal("0")))

    def test_negative_target_rate_raises(self, repo: PolicyRepository) -> None:
        """목표율이 음수면 검증 예외가 발생합니다."""
        with pytest.raises(PolicyValidationError):
            repo.insert(
                Policy(policy_code="RATE_NEG", policy_name="정책", target_rate=Decimal("-10"))
            )

    def test_invalid_target_rate_persists_nothing(self, repo: PolicyRepository) -> None:
        with pytest.raises(PolicyValidationError):
            repo.insert(
                Policy(policy_code="RATE_BAD", policy_name="정책", target_rate=Decimal("0"))
            )
        assert repo.count() == 0


class TestFindActiveWithTargetRate:
    """활성 + 목표율 보유 정책 조회를 검증합니다 (#20-2 조회 기반)."""

    def test_empty_when_no_policies(self, repo: PolicyRepository) -> None:
        assert repo.find_active_with_target_rate() == []

    def test_includes_active_with_target(self, repo: PolicyRepository) -> None:
        repo.insert(Policy(policy_code="A", policy_name="중소기업", target_rate=Decimal("50")))
        found = repo.find_active_with_target_rate()
        assert [p.policy_code for p in found] == ["A"]
        assert found[0].target_rate == Decimal("50")

    def test_excludes_null_target_rate(self, repo: PolicyRepository) -> None:
        """목표율이 없는(NULL) 정책은 제외됩니다."""
        repo.insert(Policy(policy_code="NO_RATE", policy_name="목표없음"))
        repo.insert(
            Policy(policy_code="HAS_RATE", policy_name="목표있음", target_rate=Decimal("30"))
        )
        codes = [p.policy_code for p in repo.find_active_with_target_rate()]
        assert codes == ["HAS_RATE"]

    def test_excludes_inactive_policy(self, repo: PolicyRepository) -> None:
        """비활성 정책은 목표율이 있어도 제외됩니다."""
        repo.insert(
            Policy(
                policy_code="OFF",
                policy_name="폐지",
                is_active=False,
                target_rate=Decimal("40"),
            )
        )
        assert repo.find_active_with_target_rate() == []

    def test_ordered_by_policy_id(self, repo: PolicyRepository) -> None:
        repo.insert(Policy(policy_code="P1", policy_name="정책1", target_rate=Decimal("10")))
        repo.insert(Policy(policy_code="P2", policy_name="정책2", target_rate=Decimal("20")))
        repo.insert(Policy(policy_code="P3", policy_name="정책3", target_rate=Decimal("30")))
        found = repo.find_active_with_target_rate()
        assert [p.policy_code for p in found] == ["P1", "P2", "P3"]
        assert all(p.policy_id is not None for p in found)


class TestFindAll:
    """활성·비활성 전체 조회를 검증합니다 (목표율 관리 화면용)."""

    def test_empty_when_no_policies(self, repo: PolicyRepository) -> None:
        assert repo.find_all() == []

    def test_includes_inactive_policy(self, repo: PolicyRepository) -> None:
        """비활성 정책도 조회에는 포함됩니다(변경만 제한됩니다)."""
        repo.insert(Policy(policy_code="ON", policy_name="활성"))
        repo.insert(Policy(policy_code="OFF", policy_name="비활성", is_active=False))
        assert [p.policy_code for p in repo.find_all()] == ["ON", "OFF"]

    def test_includes_null_target_rate(self, repo: PolicyRepository) -> None:
        repo.insert(Policy(policy_code="NO_RATE", policy_name="목표없음"))
        found = repo.find_all()
        assert len(found) == 1
        assert found[0].target_rate is None

    def test_ordered_by_policy_id(self, repo: PolicyRepository) -> None:
        repo.insert(Policy(policy_code="P1", policy_name="정책1"))
        repo.insert(Policy(policy_code="P2", policy_name="정책2"))
        assert [p.policy_code for p in repo.find_all()] == ["P1", "P2"]


class TestUpdateTargetRate:
    """목표율 변경(설정·해제)을 검증합니다."""

    def test_sets_target_rate_from_null(self, repo: PolicyRepository) -> None:
        """미설정(NULL) 상태에서 값을 설정할 수 있습니다."""
        repo.insert(_sample("SMALL_BUSINESS"))

        updated = repo.update_target_rate("SMALL_BUSINESS", Decimal("50"))

        assert updated is not None
        assert updated.target_rate == Decimal("50")
        found = repo.find_by_policy_code("SMALL_BUSINESS")
        assert found is not None
        assert found.target_rate == Decimal("50")

    def test_updates_updated_at(self, repo: PolicyRepository) -> None:
        saved = repo.insert(_sample("RATE_UPD"))
        assert saved.updated_at is not None

        updated = repo.update_target_rate("RATE_UPD", Decimal("10"))

        assert updated is not None
        assert updated.updated_at is not None
        assert updated.updated_at >= saved.updated_at

    def test_keeps_created_at(self, repo: PolicyRepository) -> None:
        """변경해도 생성일시는 바뀌지 않습니다."""
        saved = repo.insert(_sample("RATE_CREATED"))

        updated = repo.update_target_rate("RATE_CREATED", Decimal("10"))

        assert updated is not None
        assert updated.created_at == saved.created_at

    def test_unknown_policy_code_returns_none(self, repo: PolicyRepository) -> None:
        assert repo.update_target_rate("NOT_EXIST", Decimal("10")) is None

    def test_zero_raises(self, repo: PolicyRepository) -> None:
        repo.insert(_sample("RATE_ZERO"))
        with pytest.raises(PolicyValidationError, match="0 보다 커야"):
            repo.update_target_rate("RATE_ZERO", Decimal("0"))

    def test_negative_raises(self, repo: PolicyRepository) -> None:
        repo.insert(_sample("RATE_NEG_UPD"))
        with pytest.raises(PolicyValidationError, match="0 보다 커야"):
            repo.update_target_rate("RATE_NEG_UPD", Decimal("-1"))

    def test_over_max_raises(self, repo: PolicyRepository) -> None:
        """상한(100)을 넘으면 거부합니다 — 구조적 상한이며 법정 상한이 아닙니다."""
        repo.insert(_sample("RATE_OVER"))
        with pytest.raises(PolicyValidationError, match="이하여야"):
            repo.update_target_rate("RATE_OVER", Decimal("100.01"))

    def test_max_boundary_allowed(self, repo: PolicyRepository) -> None:
        repo.insert(_sample("RATE_MAX"))
        updated = repo.update_target_rate("RATE_MAX", TARGET_RATE_MAX)
        assert updated is not None
        assert updated.target_rate == TARGET_RATE_MAX

    def test_decimal_precision_preserved(self, repo: PolicyRepository) -> None:
        repo.insert(_sample("RATE_PREC"))
        updated = repo.update_target_rate("RATE_PREC", Decimal("12.34"))
        assert updated is not None
        assert updated.target_rate == Decimal("12.34")

    def test_reset_to_none(self, repo: PolicyRepository) -> None:
        """명시적 None 으로 목표율을 해제할 수 있습니다."""
        repo.insert(Policy(policy_code="RATE_RESET", policy_name="정책", target_rate=Decimal("30")))

        updated = repo.update_target_rate("RATE_RESET", None)

        assert updated is not None
        assert updated.target_rate is None

    def test_does_not_affect_other_policies(self, repo: PolicyRepository) -> None:
        repo.insert(Policy(policy_code="KEEP", policy_name="유지", target_rate=Decimal("20")))
        repo.insert(_sample("CHANGE"))

        repo.update_target_rate("CHANGE", Decimal("40"))

        other = repo.find_by_policy_code("KEEP")
        assert other is not None
        assert other.target_rate == Decimal("20")

    def test_all_seed_policy_codes(self, repo: PolicyRepository) -> None:
        """정본 정책 코드 5종 모두 변경 가능합니다."""
        codes = ("SMALL_BUSINESS", "WOMAN", "DISABLED", "STARTUP", "GREEN")
        for code in codes:
            repo.insert(_sample(code))

        for index, code in enumerate(codes, start=1):
            updated = repo.update_target_rate(code, Decimal(index))
            assert updated is not None
            assert updated.target_rate == Decimal(index)


class TestInsertTargetRateUpperBound:
    """등록 경로에도 동일한 상한 규칙이 적용되는지 확인합니다."""

    def test_insert_over_max_raises(self, repo: PolicyRepository) -> None:
        with pytest.raises(PolicyValidationError, match="이하여야"):
            repo.insert(
                Policy(policy_code="INS_OVER", policy_name="정책", target_rate=Decimal("101"))
            )

    def test_insert_max_boundary_allowed(self, repo: PolicyRepository) -> None:
        saved = repo.insert(
            Policy(policy_code="INS_MAX", policy_name="정책", target_rate=TARGET_RATE_MAX)
        )
        assert saved.target_rate == TARGET_RATE_MAX
