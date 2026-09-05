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
    - 기업정보가 아예 없는 사업자번호의 구매 (분모에만 들어간다)

.. note::
    **2026-08-31 (STEP 74) 에 기대값이 바뀐 곳이 있습니다.** 예전에는
    ``123-45-67890`` 처럼 표기가 다르면 매칭에 실패했고, 이 파일이 그
    **결함을 기록**하고 있었습니다. 이제 저장·조회가 같은 형태로 맞춰지므로
    같은 사업자로 연결됩니다. 검증이 느슨해진 것이 아니라 **동작이 고쳐진**
    것입니다 — ``DECISIONS.md`` §0.11.
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
from procurement.database.policy_target_repository import PolicyTargetRepository
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
        중소기업 인정   2,000,000  (하이픈 표기 · STEP 74 이후 연결됨)
        창업기업 인정   2,000,000  (전체의 20%)
        인증기간 밖     2,000,000
        인증 없음       1,000,000
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

    # ⚠️ STEP 93 — 목표비율의 정본은 **연도별** 값이다(DECISIONS §0.20). 위
    #    Policy.target_rate 는 하위호환으로 남아 있을 뿐 계산에 쓰이지 않으므로,
    #    이 시험이 조회하는 연도(2026)에 같은 값을 등록한다.
    #    ⛔ 기대값은 바뀌지 않았다 — 값을 **어디에 두는지**만 바뀌었다.
    targets = PolicyTargetRepository(path)
    targets.upsert(2026, small_business.policy_id, Decimal("50"))
    targets.upsert(2026, startup.policy_id, Decimal("20"))

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
    # ⑥ 하이픈 표기 — 표기가 달라도 가기업으로 연결된다(STEP 74 이전에는 실패했다)
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
        assert matcher.match_all() == 6  # 표기가 달라도 6건 모두 연결된다(STEP 74)

    def test_hyphenated_business_no_now_matches(self, seeded: Path) -> None:
        """하이픈이 포함된 사업자번호도 **같은 기업**으로 연결됩니다.

        ⭐ 2026-08-31 이전에는 여기서 매칭이 실패했고, 이 시험이 그 **결함을
        기록**하고 있었습니다. 저장·조회가 같은 형태로 맞춰지면서 해결되었습니다
        (``DECISIONS.md`` §0.11).

        ⛔ 표기만 맞춘 것입니다. 번호가 다르면 여전히 남남입니다 —
        :meth:`test_a_business_no_without_a_company_stays_unmatched`.
        """
        CompanyMatcher(CompanyRepository(seeded), PurchaseRepository(seeded)).match_all()

        assert PurchaseRepository(seeded).find_unmatched() == []

        hyphenated = next(
            purchase
            for purchase in PurchaseRepository(seeded).find_all()
            if purchase.business_no == BUSINESS_NO_A_HYPHENATED
        )
        company_a = CompanyRepository(seeded).find_by_business_no(BUSINESS_NO_A)
        assert company_a is not None
        assert hyphenated.company_id == company_a.company_id

    def test_a_business_no_without_a_company_stays_unmatched(self, seeded: Path) -> None:
        """⛔ 기업정보가 없는 번호는 **연결되지 않는다.** 표기를 맞춘 것뿐이다."""
        PurchaseRepository(seeded).insert(
            Purchase(
                business_no="9999999999",
                company_name="등록되지 않은 업체",
                contract_date=date(2026, 3, 1),
                payment_date=date(2026, 3, 15),
                amount=Decimal("500000"),
            )
        )
        CompanyMatcher(CompanyRepository(seeded), PurchaseRepository(seeded)).match_all()

        unmatched = PurchaseRepository(seeded).find_unmatched()
        assert [purchase.business_no for purchase in unmatched] == ["9999999999"]

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
        response = TestClient(create_app(matched, period_date_field="payment_date")).get(
            "/dashboard/summary?year=2026"
        )
        assert response.status_code == 200

    def test_total_purchase_amount_includes_unmatched(self, matched: Path) -> None:
        """전체 구매액은 매칭 실패·미인증 건을 모두 포함합니다.

        단 **2026년 조회이므로 지급일이 2027년인 구매 ②(1,000,000)는 제외**됩니다.
        (D-27 적용으로 연도 지정이 필수가 되면서 분모가 기간 기준으로 좁혀집니다.)
        """
        payload = (
            TestClient(create_app(matched, period_date_field="payment_date"))
            .get("/dashboard/summary?year=2026")
            .json()
        )
        assert payload["total_purchase_amount"] == "9000000"

    def test_payment_date_policy_calculation(self, matched: Path) -> None:
        """중소기업(지급일 기준) — 유효기간 내 5,000,000 이 인정됩니다.

        ⭐ 하이픈 표기 2,000,000(구매 ⑥)이 STEP 74 로 연결되면서 3,000,000 →
        5,000,000 이 되었습니다. **계산이 바뀐 것이 아니라 연결되지 않던 것이
        연결된** 결과입니다 — 분모 9,000,000 은 그대로입니다.
        """
        payload = (
            TestClient(create_app(matched, period_date_field="payment_date"))
            .get("/dashboard/summary?year=2026")
            .json()
        )
        item = {p["policy_code"]: p for p in payload["policies"]}["SMALL_BUSINESS"]

        assert item["purchase_amount"] == "5000000"  # 유효기간 밖 1,000,000 은 여전히 제외
        assert item["target_rate"] == "50"
        assert item["total_purchase_amount"] == "9000000"  # 분모는 움직이지 않는다
        # 구매비율 55.56% / 목표 50% → 111.11%
        assert item["achievement_rate"] == "111.11"
        assert item["shortage_rate"] == "0.00"
        assert item["status"] == "NORMAL"

    def test_contract_date_policy_calculation(self, matched: Path) -> None:
        """창업기업(계약일 기준) — 계약일이 유효기간 내면 인정됩니다.

        구매 ③은 지급일(2026-08-01)이 인증 만료 후이지만 계약일(2026-05-01)이
        유효기간 내이므로 인정됩니다. 정책별 판정 기준일이 실제로 다르게
        적용되는지 확인하는 케이스입니다.
        """
        payload = (
            TestClient(create_app(matched, period_date_field="payment_date"))
            .get("/dashboard/summary?year=2026")
            .json()
        )
        item = {p["policy_code"]: p for p in payload["policies"]}["STARTUP"]

        assert item["purchase_amount"] == "2000000"
        # 분모가 9,000,000(2026년)이므로 구매비율 22.22% / 목표 20% → 111.11%
        assert item["achievement_rate"] == "111.11"
        assert item["shortage_rate"] == "0.00"
        assert item["status"] == "NORMAL"

    def test_uncertified_company_purchase_is_excluded_from_policy(self, matched: Path) -> None:
        """인증이 없는 기업의 구매는 어떤 정책 실적에도 포함되지 않습니다.

        ⛔ **기업이 연결되었다고 실적이 되지 않습니다.** 다기업(구매 ⑤)은
        매칭되지만 인증이 없어 어느 정책에도 들어가지 않습니다.
        """
        payload = (
            TestClient(create_app(matched, period_date_field="payment_date"))
            .get("/dashboard/summary?year=2026")
            .json()
        )
        policy_total = sum(
            Decimal(p["purchase_amount"])
            for p in payload["policies"]
            if p["purchase_amount"] is not None
        )
        # 2026년 분모 9,000,000 중 정책 인정은 7,000,000(중소 5,000,000 + 창업 2,000,000).
        assert policy_total == Decimal("7000000")
        # 남는 2,000,000 = 인증 없는 기업 1,000,000 + 인증기간 밖 1,000,000
        assert Decimal(payload["total_purchase_amount"]) - policy_total == Decimal("2000000")


class TestMatchingRateImpact:
    """매칭 실패가 달성률에 미치는 영향을 확인합니다."""

    def test_unmatched_purchase_lowers_achievement_rate(self, seeded: Path) -> None:
        """매칭 실패 건은 분모(전체 구매액)에만 들어가 달성률을 낮춥니다.

        ⚠️ 예전에는 **하이픈 표기 건**이 이 역할을 했습니다. 그 건은 STEP 74 로
        연결되었으므로, 이제 **기업정보가 아예 없는 사업자번호**로 같은 성질을
        확인합니다 — 지켜야 할 것은 표기 문제가 아니라 *"연결되지 않은 구매도
        분모에는 들어간다"* 이기 때문입니다.

        **매칭률을 함께 보고해야 하는 이유**를 보여주는 케이스입니다.
        """
        PurchaseRepository(seeded).insert(
            Purchase(
                business_no="9999999999",
                company_name="등록되지 않은 업체",
                contract_date=date(2026, 3, 1),
                payment_date=date(2026, 3, 15),
                amount=Decimal("3000000"),
            )
        )
        CompanyMatcher(CompanyRepository(seeded), PurchaseRepository(seeded)).match_all()
        payload = (
            TestClient(create_app(seeded, period_date_field="payment_date"))
            .get("/dashboard/summary?year=2026")
            .json()
        )
        item = {p["policy_code"]: p for p in payload["policies"]}["SMALL_BUSINESS"]

        # 분모만 9,000,000 → 12,000,000 으로 늘고 분자는 5,000,000 그대로다.
        assert item["total_purchase_amount"] == "12000000"
        assert item["purchase_amount"] == "5000000"
        # 구매비율 41.67% / 목표 50% → 83.33% (연결되었다면 더 높았을 값)
        assert item["achievement_rate"] == "83.33"

        matched_count = len(
            [p for p in PurchaseRepository(seeded).find_all() if p.company_id is not None]
        )
        assert matched_count == 6  # 매칭률 6/7 — 남은 1건은 기업정보가 없다

    def test_normalized_business_no_would_match(self, seeded: Path) -> None:
        """하이픈을 제거한 번호로도 같은 기업을 찾습니다.

        ⚠️ 예전에는 *"정규화가 있었다면 이렇게 됐을 것"* 을 보여주는 시험이었고,
        지금은 **실제 동작**입니다(STEP 74). 손으로 하이픈을 지운 값이든 원래
        표기든 같은 기업이 나와야 합니다.
        """
        purchase_repo = PurchaseRepository(seeded)
        target = next(
            p for p in purchase_repo.find_all() if p.business_no == BUSINESS_NO_A_HYPHENATED
        )
        assert target.purchase_id is not None

        repository = CompanyRepository(seeded)
        company = repository.find_by_business_no(target.business_no.replace("-", ""))
        assert company is not None
        assert company.business_no == BUSINESS_NO_A
        # 원래 표기로 물어도 같은 기업이다.
        assert repository.find_by_business_no(target.business_no) == company
