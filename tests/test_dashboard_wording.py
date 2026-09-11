"""
STEP 68 — 화면이 **지금 계산하는 것**을 정확히 설명하는지 잠급니다.

계산은 바꾸지 않았습니다. 화면 문구가 계산의 실제 의미와 어긋나지 않는지,
그리고 **고객이 아직 답하지 않은 것**을 확정된 것처럼 적고 있지 않은지만
검사합니다.

지키는 것 세 가지
=================

1. **연도를 나누는 날짜**와 **인증 유효기간 판정 기준일**을 한 문장으로
   뭉뚱그리지 않는다. 후자는 W-1-2 🔴 미확정이며 정책마다 다르다.
2. **분모가 화면에 보인다.** 적재 합계(KPI)와 달성률 분모는 다른 숫자이고,
   분모가 없으면 담당자가 KPI 숫자로 비율을 가늠하게 된다.
3. **참고정보 · 담당자 판정 · 계산 대상**이 섞여 읽히는 문구가 없다.

.. note::
    ⛔ 이 파일은 문구만 봅니다. 계산 결과를 검증하는 시험은
    ``tests/test_achievement_boundaries.py`` (STEP 67)에 있습니다.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.import_batch import STATUS_ACTIVE, ImportBatch
from procurement.models.purchase import Purchase

_PLAIN = "1000000002"


@pytest.fixture
def page() -> str:
    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "procurement"
        / "web"
        / "static"
        / "index.html"
    )
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    """주석을 걷어냅니다 — **화면에 나가는 말**만 남깁니다.

    주석에는 "이렇게 쓰지 않는다" 는 금지 문구 자체가 적혀 있어, 그대로
    검사하면 규칙을 지킨 코드가 규칙 위반으로 보입니다.
    """
    without_html = re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL)
    without_block = re.sub(r"/\*.*?\*/", "", without_html, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.MULTILINE)


@pytest.fixture
def visible(page: str) -> str:
    """주석을 걷어낸 화면 — 사용자가 실제로 보게 되는 말."""
    return _strip_comments(page)


def _script(page: str, start_marker: str, end_marker: str) -> str:
    """함수 하나의 본문만 잘라 냅니다(주석 제거 — 화면에 나가는 말만 봅니다)."""
    start = page.index(start_marker)
    body = page[start : page.index(end_marker, start)]
    return _strip_comments(body)


# ----------------------------------------------------------------------
# 1. 연도 기준일 ≠ 인증 유효기간 판정 기준일
# ----------------------------------------------------------------------
class TestPeriodBasisWording:
    """⭐ 두 개념을 한 문장으로 합치지 않는다."""

    def _note(self, page: str) -> str:
        return _script(page, "function renderStatusTable", 'text(el("status-note")')

    def test_says_the_year_is_split_by_that_date(self, page: str) -> None:
        assert "연도는 " in self._note(page)
        assert "로 나눕니다" in self._note(page)

    def test_says_certification_basis_differs_by_policy(self, page: str) -> None:
        """⭐ 인증 판정 기준일이 정책마다 다르다는 사실을 함께 적는다.

        W-1-2 가 🔴 미확정이므로, 한 날짜로 다 정해진 것처럼 읽히면 안 된다.
        """
        assert "인증 유효기간 판정 기준일은 정책마다 다릅니다" in self._note(page)

    def test_does_not_claim_one_basis_for_certification(self, visible: str) -> None:
        """⛔ "인증은 결의일자 기준" 류의 확정 표현이 없다."""
        for banned in (
            "결의일자 기준 인증",
            "인증 유효기간은 결의일자",
            "모든 정책은 결의일자",
            "인증기업은 결의일자 기준",
        ):
            assert banned not in visible, banned

    def test_raw_column_name_is_not_shown(self, page: str) -> None:
        """화면에 ``resolution_date`` 같은 컬럼명을 그대로 내보내지 않는다."""
        note = self._note(page)
        assert "dateFieldLabel(status.period_date_field)" in note
        assert "+ status.period_date_field +" not in note

    @pytest.mark.parametrize(
        ("field", "label"),
        [
            ("resolution_date", "결의일자"),
            ("payment_date", "지급일"),
            ("contract_date", "계약일자"),
        ],
    )
    def test_date_field_labels_exist(self, page: str, field: str, label: str) -> None:
        block = _script(page, "var DATE_FIELD_LABEL", "function dateFieldLabel")
        assert f'{field}: "{label}"' in block


# ----------------------------------------------------------------------
# 2. 분모가 화면에 보인다
# ----------------------------------------------------------------------
class TestDenominatorIsVisible:
    """⭐ 달성률 분모를 정책 카드에 함께 적는다."""

    def test_policy_card_shows_the_denominator(self, page: str) -> None:
        card = _script(page, "function policyCard", "card.appendChild(dl)")
        assert "계산 대상 전체 구매액" in card
        assert "item.total_purchase_amount" in card

    def test_kpi_tile_says_it_is_the_loaded_total(self, page: str) -> None:
        """KPI 타일은 **적재 합계**임을 라벨로 밝힌다 — 분모가 아니다."""
        kpi = _script(page, "function renderKpi", "ICON.count")
        assert "적재된 구매금액 합계" in kpi
        assert "연도 무관" in kpi

    def test_kpi_tile_is_not_called_the_total_purchase(self, visible: str) -> None:
        """⛔ 예전 라벨("총 구매금액")이 화면에 남아 있지 않다.

        분모와 같은 이름으로 읽히던 자리다.
        """
        assert "총 구매금액" not in visible


class TestTwoNumbersReallyDiffer:
    """문구가 필요한 이유를 **실제 값으로** 보여 준다.

    두 숫자가 같다면 문구를 나눌 이유가 없다. 갈린다는 사실을 여기서 잠근다.
    """

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        path = tmp_path / "wording.db"
        init_db(path)
        seed_policies(path)
        PolicyRepository(path).update_target_rate("SMALL_BUSINESS", Decimal("30"))

        batches = ImportBatchRepository(path)

        def batch() -> int:
            saved = batches.insert(
                ImportBatch(
                    file_name="synthetic.xlsx",
                    period_start=date(2026, 1, 1),
                    period_end=date(2026, 12, 31),
                    row_count=1,
                    status=STATUS_ACTIVE,
                )
            )
            assert saved.batch_id is not None
            return saved.batch_id

        purchases = PurchaseRepository(path)

        def add(amount: str, batch_id: int, day: date = date(2026, 3, 1)) -> None:
            purchases.insert(
                Purchase(
                    business_no=_PLAIN,
                    company_name="합성기업",
                    contract_date=day,
                    payment_date=day,
                    resolution_date=day,
                    amount=Decimal(amount),
                    batch_id=batch_id,
                )
            )

        old, new = batch(), batch()
        add("900", old)  # 대체될 배치
        add("100", new)  # 계산 대상
        add("500", new, date(2025, 3, 1))  # 조회 연도 밖
        batches.supersede(old, superseded_by=new)

        return TestClient(create_app(path, period_date_field="resolution_date"))

    def test_loaded_total_includes_everything(self, client: TestClient) -> None:
        status: Any = client.get("/dashboard/data-status?year=2026").json()
        assert Decimal(status["purchase_total_amount"]) == Decimal("1500")

    def test_denominator_is_scoped(self, client: TestClient) -> None:
        summary: Any = client.get("/dashboard/summary?year=2026").json()
        assert Decimal(summary["total_purchase_amount"]) == Decimal("100")

    def test_the_two_numbers_are_not_the_same(self, client: TestClient) -> None:
        """⭐ 갈린다 — 그래서 화면이 둘을 구분해 불러야 한다."""
        status: Any = client.get("/dashboard/data-status?year=2026").json()
        summary: Any = client.get("/dashboard/summary?year=2026").json()
        assert Decimal(status["purchase_total_amount"]) != Decimal(summary["total_purchase_amount"])

    def test_denominator_is_carried_in_each_policy(self, client: TestClient) -> None:
        """정책마다 분모가 응답에 실려 화면이 그대로 적을 수 있다."""
        summary: Any = client.get("/dashboard/summary?year=2026").json()
        for policy in summary["policies"]:
            assert Decimal(policy["total_purchase_amount"]) == Decimal("100")


# ----------------------------------------------------------------------
# 3. 참고정보 · 담당자 판정 · 계산 대상이 섞이지 않는다
# ----------------------------------------------------------------------
class TestBoundariesAreNotBlurred:
    """⛔ 검토·구매유형·거래처 이력이 달성률에 반영된다고 적지 않는다."""

    @pytest.mark.parametrize(
        "banned",
        [
            "검토 완료 실적",
            "확정된 구매유형 기준 달성률",
            "검토된 건만 실적",
            "판정 결과를 반영한 달성률",
            "구매유형 기준 실적",
        ],
    )
    def test_no_review_to_achievement_wording(self, visible: str, banned: str) -> None:
        assert banned not in visible

    @pytest.mark.parametrize(
        "banned",
        ["공사업체", "용역업체", "물품업체", "이력 기반 판정", "거래처 이력으로 달성률"],
    )
    def test_no_company_history_verdict_wording(self, visible: str, banned: str) -> None:
        assert banned not in visible

    def test_history_block_still_says_it_is_not_a_verdict(self, page: str) -> None:
        block = _script(page, "function companyHistory", "return box;")
        assert "자동 판정 아님" in block
        assert "현재 구매유형을 정하지 않습니다" in block

    @pytest.mark.parametrize(
        "banned", ["제외된 실적", "무효 데이터", "삭제된 데이터", "실적 불인정"]
    )
    def test_unloaded_rows_are_not_called_excluded(self, visible: str, banned: str) -> None:
        """⛔ 미적재 행을 실적 제외로 확정해 부르지 않는다(Q5-8 🔴 미확정)."""
        assert banned not in visible
