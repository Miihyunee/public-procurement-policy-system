"""
STEP 90 — 인증 데이터 연결 구조의 **기술적 사실**을 고정합니다.

고객 답변이 오면 바로 구현할 수 있도록 현재 구조를 확인했습니다. 그 확인
결과 중 **나중에 조용히 달라지면 안 되는 것**을 여기에 잠급니다.

무엇을 지키는가 (지시서 §13)
============================

1. 결의일자 기준 규칙이 유지된다.
2. 인증 유효기간 판정이 **결의일자**를 쓴다.
3. 창업기업 OR 규칙이 유지된다.
4. 목표율 미설정 상태에서 **임의 기본값이 적용되지 않는다**.
5. 기업이 없으면 인증 sync 가 **인증을 임의로 붙이지 않는다**.

.. warning::
    ⛔ 이 파일은 업무규칙을 정하지 않습니다. **지금 코드가 어떤 상태인지**를
    적을 뿐이며, 고객 답변으로 구조가 바뀌면 기대값을 갱신하고 사유를
    적습니다(예: ``valid_to`` 선택 항목화).

.. note::
    합성 데이터만 씁니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.calculators.rules import (
    ResolutionDateRule,
    ResolutionOrContractDateRule,
    RuleContext,
    build_default_registry,
)
from procurement.collectors.sync_service import SKIP_COMPANY_NOT_FOUND
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import (
    CertificationRepository,
    CertificationValidationError,
)
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository, validate_target_rate
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Purchase

#: 합성 사업자등록번호 — 실제 업체의 번호가 아닙니다.
_BUSINESS_NO = "1000000001"

#: 인증 유효기간.
_VALID_FROM = date(2026, 1, 1)
_VALID_TO = date(2026, 12, 31)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "structure.db"
    init_db(path)
    seed_policies(path)
    return path


def _policy_id(db: Path, code: str) -> int:
    policy = PolicyRepository(db).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _company(db: Path) -> int:
    saved = CompanyRepository(db).insert(
        Company(
            business_no=_BUSINESS_NO,
            company_name="합성기업 가",
            representative_name="홍길동",
        )
    )
    assert saved.company_id is not None
    return saved.company_id


def _purchase(db: Path, *, resolution: date, company_id: int | None) -> None:
    PurchaseRepository(db).insert(
        Purchase(
            business_no=_BUSINESS_NO,
            company_name="합성기업 가",
            resolution_date=resolution,
            issue_date=date(2027, 7, 7),  # ⛔ 판정에 쓰이면 안 되는 날짜
            amount=Decimal("1000"),
            company_id=company_id,
        )
    )


class TestTheCertificationShape:
    """§15 · §17 — 인증이 무엇을 담을 수 있는가."""

    def test_a_certification_can_have_no_end_date(self, db: Path) -> None:
        """**종료일 없는 인증을 저장할 수 있다** — 시작일 이후로 계속 유효.

        .. note::
            분류 ② 요구사항 변경 (STEP 108). 이 시험은 STEP 107 까지
            *"종료일이 필수다"* 였고, 그때 이미 *"고객 답변에 따라 이 제약을
            푸는 변경이 필요할 수 있다. 그때 이 시험이 깨지는 것이 정상이며,
            기대값을 바꾸고 사유를 적는다"* 고 적어 두었습니다.

            🟢 2026-09-04 고객 확정: *"사회적기업과 사회적협동조합은 종료일이
            없으며 계속 유효한 것으로 판단한다."* 그래서 기대값을 뒤집습니다.

        .. warning::
            ⛔ 종료일이 **있는** 인증의 규칙은 그대로입니다. 그리고 파일에서
            들어오는 빈 종료일은 여전히 두 정책에서만 허용됩니다
            (:mod:`procurement.core.open_ended_certification`).
        """
        company_id = _company(db)
        stored = CertificationRepository(db).insert(
            Certification(
                company_id=company_id,
                policy_id=_policy_id(db, "SOCIAL_COOPERATIVE"),
                valid_from=_VALID_FROM,
                valid_to=None,
            )
        )
        assert stored.certification_id is not None
        assert stored.valid_to is None

        # 저장한 그대로 다시 읽힌다 — ⛔ 어디서도 종료일이 만들어지지 않는다.
        reloaded = CertificationRepository(db).find_by_id(stored.certification_id)
        assert reloaded is not None
        assert reloaded.valid_from == _VALID_FROM
        assert reloaded.valid_to is None

    def test_a_certification_still_needs_a_start_date(self, db: Path) -> None:
        """시작일은 여전히 필수다 — 언제부터 유효한지 모르면 판정할 수 없다."""
        company_id = _company(db)
        with pytest.raises(CertificationValidationError):
            CertificationRepository(db).insert(
                Certification(
                    company_id=company_id,
                    policy_id=_policy_id(db, "SOCIAL_COOPERATIVE"),
                    valid_from=None,  # type: ignore[arg-type]
                    valid_to=None,
                )
            )

    def test_a_certification_points_at_a_policy(self, db: Path) -> None:
        """인증 종류를 따로 두지 않고 **정책을 가리킨다** — 정책이 있어야 붙는다."""
        fields = set(Certification.__dataclass_fields__)
        assert "policy_id" in fields
        assert "certification_type" not in fields
        assert "certification_kind" not in fields

    def test_the_stored_dates_come_back_unchanged(self, db: Path) -> None:
        company_id = _company(db)
        repository = CertificationRepository(db)
        repository.insert(
            Certification(
                company_id=company_id,
                policy_id=_policy_id(db, "SMALL_BUSINESS"),
                valid_from=_VALID_FROM,
                valid_to=_VALID_TO,
            )
        )
        stored = repository.find_by_company(company_id)[0]
        assert (stored.valid_from, stored.valid_to) == (_VALID_FROM, _VALID_TO)


class TestTheResolutionDateDecidesTheCertification:
    """§13 ①②③ — 확정된 판정 규칙이 그대로인가."""

    def test_a_purchase_inside_the_period_counts(self, db: Path) -> None:
        company_id = _company(db)
        CertificationRepository(db).insert(
            Certification(
                company_id=company_id,
                policy_id=_policy_id(db, "SMALL_BUSINESS"),
                valid_from=_VALID_FROM,
                valid_to=_VALID_TO,
            )
        )
        _purchase(db, resolution=date(2026, 6, 1), company_id=company_id)

        calculator = ProcurementAchievementCalculator(
            PurchaseRepository(db), CertificationRepository(db), PolicyRepository(db)
        )
        assert calculator.calculate_policy_purchase(_policy_id(db, "SMALL_BUSINESS")) == Decimal(
            "1000"
        )

    def test_the_issue_date_cannot_rescue_a_purchase(self, db: Path) -> None:
        """⭐ 신고기준일은 판정에 쓰이지 않는다 — 결의일자가 밖이면 불인정."""
        company_id = _company(db)
        CertificationRepository(db).insert(
            Certification(
                company_id=company_id,
                policy_id=_policy_id(db, "SMALL_BUSINESS"),
                valid_from=_VALID_FROM,
                valid_to=_VALID_TO,
            )
        )
        # 결의일자는 유효기간 밖, 신고기준일은 안.
        PurchaseRepository(db).insert(
            Purchase(
                business_no=_BUSINESS_NO,
                company_name="합성기업 가",
                resolution_date=date(2027, 3, 1),
                issue_date=_VALID_TO,
                amount=Decimal("1000"),
                company_id=company_id,
            )
        )
        calculator = ProcurementAchievementCalculator(
            PurchaseRepository(db), CertificationRepository(db), PolicyRepository(db)
        )
        assert calculator.calculate_policy_purchase(_policy_id(db, "SMALL_BUSINESS")) == Decimal(
            "0"
        )

    def test_the_general_policies_use_the_resolution_rule(self, db: Path) -> None:
        registry = build_default_registry()
        for code in ("SMALL_BUSINESS", "WOMAN", "DISABLED"):
            policy = PolicyRepository(db).find_by_policy_code(code)
            assert policy is not None
            assert isinstance(registry.get(policy.evaluation_basis), ResolutionDateRule)

    def test_the_startup_or_rule_is_intact(self, db: Path) -> None:
        """🟢 창업기업 = 결의일자 **또는** 계약일자 — 둘 다 지원한다."""
        policy = PolicyRepository(db).find_by_policy_code("STARTUP")
        assert policy is not None
        rule = build_default_registry().get(policy.evaluation_basis)
        assert isinstance(rule, ResolutionOrContractDateRule)

        ranges = [(_VALID_FROM, _VALID_TO)]
        by_resolution = Purchase(
            business_no=_BUSINESS_NO,
            company_name="합성기업 가",
            resolution_date=date(2026, 6, 1),
            contract_date=date(2027, 1, 1),
            amount=Decimal("1"),
        )
        by_contract = Purchase(
            business_no=_BUSINESS_NO,
            company_name="합성기업 가",
            resolution_date=date(2027, 1, 1),
            contract_date=date(2026, 6, 1),
            amount=Decimal("1"),
        )
        assert rule.matches(RuleContext(purchase=by_resolution, validity_ranges=ranges)) is True
        assert rule.matches(RuleContext(purchase=by_contract, validity_ranges=ranges)) is True


class TestNoTargetRateIsInvented:
    """§13 ④ — 목표율 미설정에 임의 기본값이 붙지 않는가."""

    def test_the_seeds_leave_the_target_rate_unset(self, db: Path) -> None:
        for policy in PolicyRepository(db).find_all():
            assert policy.target_rate is None, policy.policy_code

    def test_an_unset_target_rate_is_valid(self) -> None:
        """``None`` 은 **미설정** 이라는 정상 값이다 — 오류가 아니다."""
        validate_target_rate(None)

    def test_the_calculator_is_not_called_without_a_target_rate(self, db: Path) -> None:
        """⭐ 목표율이 없으면 달성률을 만들어 내지 않는다."""
        company_id = _company(db)
        _purchase(db, resolution=date(2026, 6, 1), company_id=company_id)
        calculator = ProcurementAchievementCalculator(
            PurchaseRepository(db), CertificationRepository(db), PolicyRepository(db)
        )
        # 목표율이 하나도 없으므로 계산 대상이 비어 있다.
        assert calculator.calculate_all({}) == []


class TestNoCertificationWithoutACompany:
    """§13 ⑤ — 기업이 없으면 인증을 임의로 붙이지 않는가."""

    def test_the_skip_reason_exists(self) -> None:
        assert SKIP_COMPANY_NOT_FOUND

    def test_a_certification_needs_a_company_id(self, db: Path) -> None:
        with pytest.raises(CertificationValidationError):
            CertificationRepository(db).insert(
                Certification(
                    company_id=None,  # type: ignore[arg-type]
                    policy_id=_policy_id(db, "SMALL_BUSINESS"),
                    valid_from=_VALID_FROM,
                    valid_to=_VALID_TO,
                )
            )

    def test_an_unmatched_purchase_counts_for_no_policy(self, db: Path) -> None:
        """⭐ 기업에 연결되지 않은 거래는 **분자에 들어가지 않는다.**

        지금 실데이터가 정확히 이 상태다(적재 2,161건 전부 미매칭).
        """
        _purchase(db, resolution=date(2026, 6, 1), company_id=None)
        calculator = ProcurementAchievementCalculator(
            PurchaseRepository(db), CertificationRepository(db), PolicyRepository(db)
        )
        assert calculator.calculate_total_purchase() == Decimal("1000")  # 분모에는 있다
        assert calculator.calculate_policy_purchase(_policy_id(db, "SMALL_BUSINESS")) == Decimal(
            "0"
        )


class TestTheCompanyShape:
    """§14.2 — 기업을 넣으려면 무엇이 필요한가."""

    def test_a_company_needs_a_representative_name(self, db: Path) -> None:
        """⚠️ 대표자명이 **필수다** — 「작업」 시트에는 그 칸이 없다(§14.2).

        .. note::
            현재 구조의 사실을 적어 둡니다. 원천이 정해져 기업 적재 경로가
            생길 때 이 값을 어디서 채울지가 함께 정해져야 합니다.
            ⛔ 우리가 빈 값이나 대체값을 넣지 않았습니다.
        """
        from procurement.database.company_repository import CompanyValidationError

        with pytest.raises(CompanyValidationError):
            CompanyRepository(db).insert(
                Company(
                    business_no=_BUSINESS_NO,
                    company_name="합성기업 가",
                    representative_name="",
                )
            )

    def test_the_company_has_no_size_field(self) -> None:
        """⛔ 업체 규모를 저장하는 자리가 없다 — 규모로 판정하지 않는다(§16)."""
        fields = set(Company.__dataclass_fields__)
        assert fields == {
            "business_no",
            "company_name",
            "representative_name",
            "company_id",
            "created_at",
            "updated_at",
        }
