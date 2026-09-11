"""
STEP 115 — 여성기업 공사·용역·물품 구매유형 **연결**과 실데이터 판정.

무엇을 지키는가
===============
여성기업 목표는 하나가 아니라 셋이다 — 공사 3% · 용역 5% · 물품 5%. 이 STEP 은
그 셋을 **담당자 확정값**(``purchase_review.final_purchase_type``)에 연결한
경로가 끝까지 살아 있는지, 그리고 **확정값이 없을 때 무엇을 하지 않는지**를
고정한다.

⛔ **자동 판정하지 않는다.** 적요·예산과목·거래처명으로 유형을 유추하지 않는다.
확정 전 상태는 ``PENDING``/``NULL`` 로 남으며, 어느 유형의 분모에도 분자에도
들어가지 않는다.

⛔ **분모가 없으면 0% 가 아니라 «계산 보류» 다.** "확정된 공사 거래가 한 건도
없다" 와 "공사 실적이 0원이다" 는 다른 말이며, 0% 로 적으면 담당자가 실적을
채워야 할 일과 검토를 끝내야 할 일을 구분하지 못한다.

실제 데이터에서는 어떠했는가 (§2 계측 · 2026-09-05)
===================================================

======================================  ==============================
계산 대상 구매                          2,079건 · 10,349,192,149원
``purchase_review`` 행 수               **0**
``final_purchase_type`` 이 확정된 건    **0**
여성기업 매칭(유효)                     145건 · 1,525,413,644원
======================================  ==============================

즉 공사·용역·물품 어느 쪽에도 **분모가 없다**. 그래서 이번 STEP 의 실데이터
판정은 «구매유형 확정 데이터 부족으로 HOLD» 이며, 달성률을 만들어 내지 않는다.
아래 :class:`TestNothingConfirmedMeansHold` 가 그 상태를 구조로 고정한다.

.. note::
    유형별 분모·분자 계산 자체와 §11 예시표는 STEP 103
    (``test_step103_woman_scoped_calculation.py``)이 이미 고정하고 있다. 이
    파일은 **거기서 다루지 않은 이음매** — 확정 API, 이력 보존, 월별 누적·활성
    버전과의 상호작용, 그리고 미확정 상태의 취급 — 을 본다.

.. note::
    합성 데이터만 쓴다. 실제 기업명·사업자등록번호는 넣지 않는다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from procurement.__main__ import main
from procurement.app import create_app
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models import Certification, Company, Purchase

#: 합성 사업자등록번호 — ⛔ 실제 고객 값이 아니다.
_WOMAN_BNO = "1000000009"
_OTHER_BNO = "1000000014"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "step115.db"
    bootstrap(path)
    ReviewRepository(path).create_table()
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path))


def _policy_id(db_path: Path, code: str) -> int:
    policy = PolicyRepository(db_path).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _certify(
    db_path: Path,
    code: str,
    business_no: str = _WOMAN_BNO,
    *,
    valid_from: date = date(2026, 1, 1),
    valid_to: date | None = date(2026, 12, 31),
) -> None:
    companies = CompanyRepository(db_path)
    company = companies.find_by_business_no(business_no)
    if company is None:
        company = companies.insert(Company(business_no=business_no, company_name="합성상사"))
    assert company.company_id is not None
    CertificationRepository(db_path).insert(
        Certification(
            company_id=company.company_id,
            policy_id=_policy_id(db_path, code),
            valid_from=valid_from,
            valid_to=valid_to,
        )
    )


def _spend(
    db_path: Path,
    business_no: str = _WOMAN_BNO,
    amount: str = "1000000",
    *,
    resolution_date: date = date(2026, 3, 5),
) -> int:
    """구매 한 건을 넣는다. ⛔ 유형은 붙이지 않는다 — 확정은 담당자가 한다."""
    purchase = PurchaseRepository(db_path).insert(
        Purchase(
            business_no=business_no,
            company_name="합성상사",
            amount=Decimal(amount),
            payment_date=resolution_date,
            contract_date=resolution_date,
            resolution_date=resolution_date,
        )
    )
    assert purchase.purchase_id is not None
    return purchase.purchase_id


def _confirm(client: TestClient, purchase_id: int, purchase_type: str | None) -> httpx.Response:
    response: httpx.Response = client.put(
        f"/reviews/{purchase_id}",
        json={"final_purchase_type": purchase_type, "reviewed_by": "담당자"},
    )
    return response


def _won(value: object) -> Decimal:
    """금액은 API 가 **문자열**로 준다 — 자릿수를 잃지 않으려고 그렇게 둔 값이다."""
    return Decimal(str(value))


def _woman(client: TestClient, year: int = 2026) -> dict[str, Any]:
    client.post("/purchases/rematch")
    payload = client.get("/dashboard/summary", params={"year": year}).json()
    row = next(item for item in payload["policies"] if item["policy_code"] == "WOMAN")
    return dict(row)


def _scoped(client: TestClient, year: int = 2026) -> dict[str, dict[str, Any]]:
    return {entry["scope"]: entry for entry in _woman(client, year)["scoped_achievements"]}


# ======================================================================
# §6 · §8-7  확정값이 없으면 «계산 보류» 다 — 0% 가 아니다
# ======================================================================
class TestNothingConfirmedMeansHold:
    """실데이터가 놓인 자리를 그대로 재현한다 — 검토 행이 하나도 없는 상태."""

    @pytest.fixture
    def unreviewed(self, db_path: Path, client: TestClient) -> TestClient:
        _certify(db_path, "WOMAN")
        _spend(db_path, _WOMAN_BNO, "1500000")
        _spend(db_path, _OTHER_BNO, "8500000")
        return client

    def test_1_every_type_is_on_hold_not_zero_percent(self, unreviewed: TestClient) -> None:
        """⭐ 세 유형 모두 «계산 보류» — ⛔ 0% 라고 쓰지 않는다."""
        scoped = _scoped(unreviewed)

        for scope in (CONSTRUCTION, SERVICE, GOODS):
            assert scoped[scope]["achievement_rate"] is None
            assert scoped[scope]["status"] == "CALCULATION_ON_HOLD"

    def test_2_the_denominator_is_absent_not_pretended(self, unreviewed: TestClient) -> None:
        """분모가 0 이다 — ⛔ 전체 구매금액으로 바꿔치기하지 않는다."""
        scoped = _scoped(unreviewed)

        for scope in (CONSTRUCTION, SERVICE, GOODS):
            assert _won(scoped[scope]["total_purchase_amount"]) == 0
            assert _won(scoped[scope]["purchase_amount"]) == 0

    def test_3_the_policy_row_still_reports_the_matched_amount(
        self, unreviewed: TestClient
    ) -> None:
        """유형을 몰라도 여성기업 실적 총액은 보인다 — 매칭은 유형과 무관하다."""
        row = _woman(unreviewed)

        assert _won(row["purchase_amount"]) == 1_500_000
        assert row["status"] == "SCOPED_BY_PURCHASE_TYPE"
        assert row["achievement_rate"] is None

    def test_4_unconfirmed_purchases_are_not_deleted(
        self, db_path: Path, unreviewed: TestClient
    ) -> None:
        """⛔ 미확정이라고 지우지 않는다 — 원본 두 건이 그대로 있다."""
        assert len(PurchaseRepository(db_path).find_all()) == 2


# ======================================================================
# §3 · §8-1  담당자 확정 흐름이 계산까지 이어지는가
# ======================================================================
class TestConfirmingReachesTheCalculation:
    """PENDING → 확정 → 여성기업 유형별 결과가 실제로 바뀌는가."""

    @pytest.fixture
    def spends(self, db_path: Path) -> dict[str, int]:
        _certify(db_path, "WOMAN")
        return {
            "woman": _spend(db_path, _WOMAN_BNO, "1000000"),
            "other": _spend(db_path, _OTHER_BNO, "9000000"),
        }

    def test_5_a_review_row_with_a_null_type_joins_no_scope(
        self, db_path: Path, client: TestClient, spends: dict[str, int]
    ) -> None:
        """검토 행이 **생겼어도** 유형이 ``None`` 이면 어느 분모에도 없다.

        ⛔ «검토를 시작했다» 와 «유형을 정했다» 는 다른 말이다.
        """
        assert _confirm(client, spends["woman"], None).status_code == 200
        assert _confirm(client, spends["other"], None).status_code == 200

        review = ReviewRepository(db_path).find_by_purchase_id(spends["woman"])
        assert review is not None and review.final_purchase_type is None

        scoped = _scoped(client)
        for scope in (CONSTRUCTION, SERVICE, GOODS):
            assert _won(scoped[scope]["total_purchase_amount"]) == 0
            assert scoped[scope]["status"] == "CALCULATION_ON_HOLD"

    @pytest.mark.parametrize("scope", [CONSTRUCTION, SERVICE, GOODS])
    def test_6_a_confirmed_type_reaches_the_woman_calculation(
        self, client: TestClient, spends: dict[str, int], scope: str
    ) -> None:
        """확정한 유형만 그 유형의 분모·분자에 들어간다 (100만 / 1,000만 = 10%)."""
        assert _confirm(client, spends["woman"], scope).status_code == 200
        assert _confirm(client, spends["other"], scope).status_code == 200

        scoped = _scoped(client)
        assert _won(scoped[scope]["purchase_amount"]) == 1_000_000
        assert _won(scoped[scope]["total_purchase_amount"]) == 10_000_000
        assert _won(scoped[scope]["achievement_rate"]) > 0

        for other in {CONSTRUCTION, SERVICE, GOODS} - {scope}:
            assert scoped[other]["status"] == "CALCULATION_ON_HOLD"

    def test_7_reopening_keeps_the_value_and_keeps_counting_it(
        self, db_path: Path, client: TestClient, spends: dict[str, int]
    ) -> None:
        """확정을 되돌려도 **지금은** 그 유형의 분모에 남아 있다.

        .. warning::
            ⚠️ **규칙이 아니라 현재 동작이다 — 고객 확정 대기(확인 요청서 ⑩).**

            «확정 취소»(:http:post:`/reviews/{purchase_id}/reopen`)는 상태만
            ``REOPENED`` 로 바꾸고 ``final_purchase_type`` 은 **일부러 남긴다**
            — 담당자가 무엇을 골랐었는지 보이게 하려는 설계다. 반면 계산은
            ``final_purchase_type`` 만 보므로, 되돌린 건이 여전히 공사·용역·물품
            분모에 든다.

            둘 중 어느 쪽이 옳은지는 **고객이 정할 일**이다.

            * «되돌렸으면 계산에서도 빠져야 한다» 면 계산 조건에
              ``review_status`` 를 더한다.
            * «되돌린 것은 화면 표시일 뿐, 값은 여전히 유효하다» 면 지금이 맞다.

            ⛔ 답이 오기 전에 어느 쪽으로도 바꾸지 않는다 — 실적 금액이 움직이는
            변경이다. 답이 오면 이 시험의 기대값을 그때 뒤집는다.
        """
        _confirm(client, spends["woman"], GOODS)
        assert _won(_scoped(client)[GOODS]["total_purchase_amount"]) == 1_000_000

        assert client.post(f"/reviews/{spends['woman']}/reopen", json={}).status_code == 200

        review = ReviewRepository(db_path).find_by_purchase_id(spends["woman"])
        assert review is not None
        assert review.review_status == "REOPENED"
        assert review.final_purchase_type == GOODS  # ⚠️ 남긴다 — 위 경고 참조

        assert _won(_scoped(client)[GOODS]["total_purchase_amount"]) == 1_000_000
        assert _won(_woman(client)["purchase_amount"]) == 1_000_000

    def test_8_the_history_is_kept(self, client: TestClient, spends: dict[str, int]) -> None:
        """⛔ 이력을 덮어쓰지 않는다 — 바꾼 횟수만큼 남는다."""
        _confirm(client, spends["woman"], CONSTRUCTION)
        _confirm(client, spends["woman"], SERVICE)

        history = client.get(f"/reviews/{spends['woman']}/history").json()["items"]

        assert len(history) >= 2
        assert history[-1]["after_type"] == SERVICE
        assert any(entry["after_type"] == CONSTRUCTION for entry in history)


# ======================================================================
# §1-4 · §8  목표 3% · 5% · 5% 가 서로 독립인가, 다른 정책을 건드리지 않는가
# ======================================================================
class TestTheThreeTargetsStandApart:
    def test_9_the_woman_targets_are_three_five_five(self, db_path: Path) -> None:
        """공사 3% · 용역 5% · 물품 5% — 한 정책에 세 줄이다."""
        rates = PolicyTargetRepository(db_path).scoped_rates_by_policy_id(2026)

        assert rates[_policy_id(db_path, "WOMAN")] == {
            CONSTRUCTION: Decimal("3"),
            SERVICE: Decimal("5"),
            GOODS: Decimal("5"),
        }

    def test_10_other_policies_keep_a_single_total_target(self, db_path: Path) -> None:
        """⛔ 유형별 목표가 다른 정책으로 번지지 않는다."""
        scoped = PolicyTargetRepository(db_path).scoped_rates_by_policy_id(2026)

        for code in ("SMALL_BUSINESS", "STARTUP", "SOCIAL_ENTERPRISE", "DISABLED"):
            assert _policy_id(db_path, code) not in scoped

    def test_11_confirming_a_type_does_not_move_the_total_policies(
        self, db_path: Path, client: TestClient
    ) -> None:
        """유형 확정은 ``TOTAL`` 분모 정책의 실적·달성률을 바꾸지 않는다."""
        _certify(db_path, "WOMAN")
        _certify(db_path, "STARTUP", _OTHER_BNO)
        _spend(db_path, _WOMAN_BNO, "1000000")
        startup_spend = _spend(db_path, _OTHER_BNO, "9000000")

        client.post("/purchases/rematch")
        before = client.get("/dashboard/summary", params={"year": 2026}).json()

        _confirm(client, startup_spend, SERVICE)
        after = client.get("/dashboard/summary", params={"year": 2026}).json()

        assert after["total_purchase_amount"] == before["total_purchase_amount"]
        for name in ("STARTUP", "SMALL_BUSINESS", "SOCIAL_ENTERPRISE"):
            was = next(row for row in before["policies"] if row["policy_code"] == name)
            now = next(row for row in after["policies"] if row["policy_code"] == name)
            assert (now["purchase_amount"], now["achievement_rate"]) == (
                was["purchase_amount"],
                was["achievement_rate"],
            )


# ======================================================================
# §7  앞선 STEP 이 세운 것들과 함께 서는가
# ======================================================================
class TestItStandsWithTheEarlierSteps:
    def test_12_the_year_axis_is_still_the_resolution_date(
        self, db_path: Path, client: TestClient
    ) -> None:
        """연도 귀속은 여전히 결의일자다 — 유형을 확정해도 축이 바뀌지 않는다."""
        _certify(db_path, "WOMAN", valid_from=date(2025, 1, 1), valid_to=date(2026, 12, 31))
        last_year = _spend(db_path, _WOMAN_BNO, "1000000", resolution_date=date(2025, 12, 20))
        this_year = _spend(db_path, _WOMAN_BNO, "2000000", resolution_date=date(2026, 1, 20))
        _confirm(client, last_year, GOODS)
        _confirm(client, this_year, GOODS)

        assert _won(_scoped(client, 2026)[GOODS]["total_purchase_amount"]) == 2_000_000

    def test_13_only_the_active_certification_version_counts(
        self, db_path: Path, client: TestClient
    ) -> None:
        """STEP 114 의 활성 버전 구조가 유형별 계산에도 그대로 적용된다."""
        _certify(db_path, "WOMAN")
        purchase_id = _spend(db_path, _WOMAN_BNO, "1000000")
        _confirm(client, purchase_id, GOODS)
        assert _won(_scoped(client)[GOODS]["purchase_amount"]) == 1_000_000

        certifications = CertificationRepository(db_path)
        active = certifications.find_active_by_policy(_policy_id(db_path, "WOMAN"))

        assert [row.certification_id for row in active] == [
            row.certification_id
            for row in certifications.find_by_policy(_policy_id(db_path, "WOMAN"))
        ]
