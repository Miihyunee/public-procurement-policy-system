"""
End-to-End 데이터 연계 구조 검증.

실제 고객 구매데이터를 투입하기 전에, **현재 시스템의 데이터 연결 구조가
정상적으로 동작하는지** 확인합니다.

검증 대상 흐름::

    구매데이터 → 사업자번호 → 기업 → 인증 → 정책 판정 → 구매금액 → 달성률 → Dashboard

.. note::
    본 테스트는 "실제 데이터처럼 보이는 대량 샘플"을 만들지 않습니다.
    기업 3개·정책 2개 수준의 **구조 검증용 최소 Fixture** 만 사용하며,
    목적은 각 연결 고리가 실제로 이어지는지 확인하는 것입니다.

    확인되는 실패 케이스도 함께 검증합니다.

    - 인증 유효기간을 벗어난 구매 (판정 기준일 기준)
    - 인증이 없는 기업의 구매
    - **사업자번호 형식 불일치로 매칭에 실패하는 구매** (정규화 미구현)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import init_db
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.matchers import CompanyMatcher
from procurement.models import Certification, Company, Policy, Purchase

# ----------------------------------------------------------------------
# 최소 Fixture 정의
# ----------------------------------------------------------------------
#: 기업 A — 중소기업 인증 보유(2026년 내내 유효)
BUSINESS_NO_A = "1234567890"
#: 기업 B — 창업기업 인증 보유(2026년 상반기만 유효)
BUSINESS_NO_B = "2234567890"
#: 기업 C — 인증 없음
BUSINESS_NO_C = "3334567890"
#: 기업 A 의 사업자번호를 하이픈 형식으로 표기한 값(고객 데이터에서 흔한 형태)
BUSINESS_NO_A_HYPHENATED = "123-45-67890"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "e2e.db"
    init_db(path)
    return path


def _seed_minimal_dataset(path: Path) -> None:
    """구조 검증에 필요한 최소 데이터를 구성합니다.

    금액 구성(전체 10,000,000원)::

        중소기업 인정   3,000,000  (전체의 30%)
        창업기업 인정   2,000,000  (전체의 20%)
        인증기간 밖     2,000,000
        인증 없음       1,000,000
        매칭 실패       2,000,000
    """
    company_repo = CompanyRepository(path)
    policy_repo = PolicyRepository(path)
    cert_repo = CertificationRepository(path)
    purchase_repo = PurchaseRepository(path)

    company_a = company_repo.insert(
        Company(business_no=BUSINESS_NO_A, company_name="가기업", representative_name="김대표")
    )
    company_b = company_repo.insert(
        Company(business_no=BUSINESS_NO_B, company_name="나기업", representative_name="이대표")
    )
    company_repo.insert(
        Company(business_no=BUSINESS_NO_C, company_name="다기업", representative_name="박대표")
    )
    assert company_a.company_id is not None
    assert company_b.company_id is not None

    # 목표율을 명시적으로 설정한다(계산 경로 검증이 목적).
    small_business = policy_repo.insert(
        Policy(
            policy_code="SMALL_BUSINESS",
            policy_name="중소기업",
            evaluation_basis="PAYMENT_DATE",
            target_rate=Decimal("50"),
        )
    )
    startup = policy_repo.insert(
        Policy(
            policy_code="STARTUP",
            policy_name="창업기업",
            evaluation_basis="CONTRACT_DATE",
            target_rate=Decimal("20"),
        )
    )
    assert small_business.policy_id is not None
    assert startup.policy_id is not None

    cert_repo.insert(
        Certification(
            company_id=company_a.company_id,
            policy_id=small_business.policy_id,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
        )
    )
    cert_repo.insert(
        Certification(
            company_id=company_b.company_id,
            policy_id=startup.policy_id,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 6, 30),  # 상반기만 유효
        )
    )

    def add_purchase(business_no: str, contract: date, payment: date, amount: str) -> None:
        purchase_repo.insert(
            Purchase(
                business_no=business_no,
                company_name="공급업체",
                contract_date=contract,
                payment_date=payment,
                amount=Decimal(amount),
            )
        )

    # ① 중소기업 인정 — 지급일이 유효기간 내
    add_purchase(BUSINESS_NO_A, date(2026, 3, 1), date(2026, 3, 15), "3000000")
    # ② 제외 — 지급일이 유효기간을 벗어남(2027년)
    add_purchase(BUSINESS_NO_A, date(2026, 12, 1), date(2027, 1, 15), "1000000")
    # ③ 창업기업 인정 — 계약일이 유효기간 내(지급일은 기간 밖이지만 계약일 기준)
    add_purchase(BUSINESS_NO_B, date(2026, 5, 1), date(2026, 8, 1), "2000000")
    # ④ 제외 — 계약일이 유효기간을 벗어남
    add_purchase(BUSINESS_NO_B, date(2026, 8, 1), date(2026, 9, 1), "1000000")
    # ⑤ 인증 없는 기업 — 정책 실적 아님, 전체 구매액에는 포함
    add_purchase(BUSINESS_NO_C, date(2026, 3, 1), date(2026, 3, 15), "1000000")
    # ⑥ 하이픈 표기 — 정규화가 없어 기업 매칭에 실패한다
    add_purchase(BUSINESS_NO_A_HYPHENATED, date(2026, 3, 1), date(2026, 3, 15), "2000000")


@pytest.fixture
def seeded(db_path: Path) -> Path:
    _seed_minimal_dataset(db_path)
    return db_path


class TestMatchingStep:
    """구매데이터 → 사업자번호 → 기업 연결 단계를 검증합니다."""

    def test_matcher_links_purchases_to_companies(self, seeded: Path) -> None:
        """사업자번호가 일치하는 구매는 기업과 연결됩니다."""
        matcher = CompanyMatcher(CompanyRepository(seeded), PurchaseRepository(seeded))
        assert matcher.match_all() == 5  # 전체 6건 중 하이픈 1건 실패

    def test_hyphenated_business_no_fails_to_match(self, seeded: Path) -> None:
        """하이픈이 포함된 사업자번호는 매칭에 실패합니다.

        현재 시스템에는 **사업자번호 정규화 로직이 없습니다.** 고객 데이터가
        ``123-45-67890`` 형식으로 들어오면 저장된 ``1234567890`` 과 일치하지
        않아 정책 실적에서 누락됩니다.
        """
        CompanyMatcher(CompanyRepository(seeded), PurchaseRepository(seeded)).match_all()

        unmatched = PurchaseRepository(seeded).find_unmatched()
        assert [purchase.business_no for purchase in unmatched] == [BUSINESS_NO_A_HYPHENATED]

    def test_matcher_is_idempotent(self, seeded: Path) -> None:
        """이미 매칭된 건은 다시 처리하지 않습니다."""
        matcher = CompanyMatcher(CompanyRepository(seeded), PurchaseRepository(seeded))
        matcher.match_all()
        assert matcher.match_all() == 0


class TestEndToEndCalculation:
    """매칭 이후 정책 판정 → 집계 → 달성률까지 이어지는지 검증합니다."""

    @pytest.fixture
    def matched(self, seeded: Path) -> Path:
        CompanyMatcher(CompanyRepository(seeded), PurchaseRepository(seeded)).match_all()
        return seeded

    def test_dashboard_returns_200(self, matched: Path) -> None:
        response = TestClient(create_app(matched)).get("/dashboard/summary")
        assert response.status_code == 200

    def test_total_purchase_amount_includes_unmatched(self, matched: Path) -> None:
        """전체 구매액은 매칭 실패·미인증 건을 모두 포함합니다."""
        payload = TestClient(create_app(matched)).get("/dashboard/summary").json()
        assert payload["total_purchase_amount"] == "10000000"

    def test_payment_date_policy_calculation(self, matched: Path) -> None:
        """중소기업(지급일 기준) — 유효기간 내 3,000,000 만 인정됩니다."""
        payload = TestClient(create_app(matched)).get("/dashboard/summary").json()
        item = {p["policy_code"]: p for p in payload["policies"]}["SMALL_BUSINESS"]

        assert item["purchase_amount"] == "3000000"  # 기간 밖 1,000,000 제외
        assert item["target_rate"] == "50"
        assert item["achievement_rate"] == "60.00"  # 구매비율 30% / 목표 50%
        assert item["shortage_rate"] == "40.00"
        assert item["status"] == "SHORTAGE"

    def test_contract_date_policy_calculation(self, matched: Path) -> None:
        """창업기업(계약일 기준) — 계약일이 유효기간 내면 인정됩니다.

        구매 ③은 지급일(2026-08-01)이 인증 만료 후이지만 계약일(2026-05-01)이
        유효기간 내이므로 인정됩니다. 정책별 판정 기준일이 실제로 다르게
        적용되는지 확인하는 케이스입니다.
        """
        payload = TestClient(create_app(matched)).get("/dashboard/summary").json()
        item = {p["policy_code"]: p for p in payload["policies"]}["STARTUP"]

        assert item["purchase_amount"] == "2000000"
        assert item["achievement_rate"] == "100.00"  # 구매비율 20% / 목표 20%
        assert item["shortage_rate"] == "0.00"
        assert item["status"] == "NORMAL"

    def test_uncertified_company_purchase_is_excluded_from_policy(self, matched: Path) -> None:
        """인증이 없는 기업의 구매는 어떤 정책 실적에도 포함되지 않습니다."""
        payload = TestClient(create_app(matched)).get("/dashboard/summary").json()
        policy_total = sum(
            Decimal(p["purchase_amount"])
            for p in payload["policies"]
            if p["purchase_amount"] is not None
        )
        # 전체 10,000,000 중 정책 인정은 5,000,000 뿐이다.
        assert policy_total == Decimal("5000000")


class TestMatchingRateImpact:
    """매칭 실패가 달성률에 미치는 영향을 확인합니다."""

    def test_unmatched_purchase_lowers_achievement_rate(self, seeded: Path) -> None:
        """매칭 실패 건은 분모(전체 구매액)에만 들어가 달성률을 낮춥니다.

        하이픈 표기 2,000,000원이 매칭되었다면 중소기업 구매액은 5,000,000원
        (50%)이 되어 달성률 100% 였을 것입니다. 매칭 실패로 60% 로 계산됩니다.
        **매칭률을 함께 보고해야 하는 이유**를 보여주는 케이스입니다.
        """
        CompanyMatcher(CompanyRepository(seeded), PurchaseRepository(seeded)).match_all()
        payload = TestClient(create_app(seeded)).get("/dashboard/summary").json()
        item = {p["policy_code"]: p for p in payload["policies"]}["SMALL_BUSINESS"]

        assert item["achievement_rate"] == "60.00"

        matched_count = len(
            [p for p in PurchaseRepository(seeded).find_all() if p.company_id is not None]
        )
        assert matched_count == 5  # 매칭률 5/6

    def test_normalized_business_no_would_match(self, seeded: Path) -> None:
        """하이픈을 제거하면 매칭에 성공합니다(정규화 필요성 확인).

        Import 단계에서 정규화가 이루어지면 해결되는 문제임을 보여줍니다.
        """
        purchase_repo = PurchaseRepository(seeded)
        target = next(
            p for p in purchase_repo.find_all() if p.business_no == BUSINESS_NO_A_HYPHENATED
        )
        assert target.purchase_id is not None

        company = CompanyRepository(seeded).find_by_business_no(
            target.business_no.replace("-", "")
        )
        assert company is not None
        assert company.business_no == BUSINESS_NO_A
