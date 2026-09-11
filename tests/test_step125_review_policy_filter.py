"""
STEP 123 — 검토 화면에서 **정책으로 좁혀 본다**.

무엇을 만들었는가
=================
여성기업 목표는 유형별로 갈리므로(공사 3% · 용역·물품 5%), 담당자가 **여성기업
거래만 골라** 구매유형을 확정할 수 있어야 한다. 검토 목록에 정책 필터를 더했다.

⭐ **매칭을 다시 하지 않는다.** 실적 합산이 쓰는 그 판정
(:meth:`ProcurementAchievementCalculator.find_matching_purchase_ids`)을 그대로
지난다 — 사업자번호를 다시 비교하거나 인증 파일을 다시 읽지 않는다.

::

    월별 지출 → 기존 정책 매칭 → 검토 목록 → [정책 필터] → 담당자 확정

조사 결과는 **Case B** 였다(§14)
=================================
정책 매칭 판정은 계산기 안에만 있었고 검토 조회에서 부를 수 없었다. 그래서
계산기의 판정 루프를 :meth:`_matching_purchases` 하나로 모으고, 합산과 필터가
**같은 코드**를 지나게 했다. ⛔ 새 매칭 알고리즘을 만들지 않았다.

⛔ 이 STEP 이 바꾸지 않은 것
============================
자동판정 규칙(도서인쇄비·소모성물품구입비 → 물품, 임차료는 PENDING 유지) ·
여성기업 계산식(3% · 5% · 5%) · 전체 달성률 · 검색 규칙 · 검토 저장 API ·
이력 구조 · 월별 누적 · 인증 버전 선택.

필터는 **목록을 좁힐 뿐** 어떤 계산도 바꾸지 않는다.

.. note::
    합성 데이터만 쓴다. 실제 기업명·사업자등록번호·건수·금액은 넣지 않는다.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from procurement.__main__ import main
from procurement.app import create_app
from procurement.core.purchase_type import GOODS, SERVICE
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.review_repository import ReviewRepository
from procurement.uploads.format import header_row

#: 합성 사업자등록번호 — ⛔ 실제 고객 값이 아니다.
_WOMAN = "1000000009"  # 여성기업에만
_BOTH = "1000000014"  # 여성기업 + 창업기업 둘 다
_NONE = "1000000028"  # 어느 정책에도 없음


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "step123.db"
    init_db(path)
    seed_policies(path)
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _won(value: object) -> Decimal:
    return Decimal(str(value))


def _company_file(path: Path, business_numbers: list[str]) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    for business_no in business_numbers:
        sheet.append([business_no, "합성업체", "가나다", "2026-01-01", "2026-12-31"])
    book.save(path)
    return path


def _purchase_row(
    *, day: str, amount: int, business_no: str, budget: str = "일반수용비"
) -> list[object]:
    values: dict[str, object] = {
        "결의일자": day,
        "계약일자": day,
        "지급일": day,
        "기업명": "합성업체",
        "사업자등록번호": business_no,
        "계": amount,
        "신고기준일": day,
        "적요": "합성 거래",
        "예산과목": budget,
    }
    return [values[header] for header in header_row()]


def _purchase_file(path: Path, rows: list[list[object]]) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(list(header_row()))
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def _register(client: TestClient, tmp_path: Path, code: str, business_numbers: list[str]) -> None:
    path = _company_file(tmp_path / f"{code}.xlsx", business_numbers)
    assert (
        client.post(
            "/companies/upload", json={"file_path": str(path), "policy_code": code}
        ).status_code
        == 200
    )


def _reviews(client: TestClient, **params: object) -> dict[str, Any]:
    # 화면과 **같은 경로**로 부른다 — page 를 주면 검색·필터가 있는 조회다.
    params.setdefault("page", 1)
    response: httpx.Response = client.get("/reviews", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _ids(client: TestClient, **params: object) -> list[int]:
    payload = _reviews(client, page_size=200, **params)
    return sorted(item["source"]["purchase_id"] for item in payload["items"])


def _amount_sum(client: TestClient, **params: object) -> Decimal:
    payload = _reviews(client, page_size=200, **params)
    return sum((_won(item["source"]["amount"]) for item in payload["items"]), Decimal("0"))


@pytest.fixture
def seeded(client: TestClient, tmp_path: Path) -> TestClient:
    """여성기업 2곳 · 창업기업 1곳(여성기업과 겹침) · 무소속 1곳."""
    _register(client, tmp_path, "WOMAN", [_WOMAN, _BOTH])
    _register(client, tmp_path, "STARTUP", [_BOTH])
    spend = _purchase_file(
        tmp_path / "spend.xlsx",
        [
            _purchase_row(day="2026-03-01", amount=1_000, business_no=_WOMAN),
            _purchase_row(day="2026-03-02", amount=2_000, business_no=_BOTH),
            _purchase_row(day="2026-03-03", amount=4_000, business_no=_NONE),
            _purchase_row(day="2026-03-04", amount=8_000, business_no=_WOMAN, budget="도서인쇄비"),
        ],
    )
    assert (
        client.post(
            "/uploads/purchases",
            json={"file_path": str(spend), "year": 2026, "month": 3},
        ).status_code
        == 200
    )
    client.post("/purchases/rematch")
    return client


# ======================================================================
# Test 1 · 2  전체 / 여성기업
# ======================================================================
class TestThePolicyFilterNarrowsTheList:
    def test_1_no_policy_means_everything(self, seeded: TestClient) -> None:
        """정책을 고르지 않으면 전체다 — 예전과 같다."""
        assert _ids(seeded) == [1, 2, 3, 4]

    def test_2_the_woman_filter_keeps_only_woman_rows(self, seeded: TestClient) -> None:
        """⭐ 여성기업을 고르면 여성기업과 한 거래만 남는다."""
        assert _ids(seeded, policy="WOMAN") == [1, 2, 4]
        assert _amount_sum(seeded, policy="WOMAN") == 11_000

    def test_2b_the_total_count_follows_the_filter(self, seeded: TestClient) -> None:
        """건수도 좁혀진 조건 기준으로 나온다 — 화면이 「검토 대상 N건」을 그린다."""
        assert _reviews(seeded)["page"]["total"] == 4
        assert _reviews(seeded, policy="WOMAN")["page"]["total"] == 3

    def test_2c_an_unmatched_company_is_excluded(self, seeded: TestClient) -> None:
        """어느 정책에도 없는 거래는 정책 필터에서 빠진다."""
        assert 3 not in _ids(seeded, policy="WOMAN")
        assert 3 not in _ids(seeded, policy="STARTUP")


# ======================================================================
# Test 3  정책 독립성
# ======================================================================
class TestThePoliciesAreIndependent:
    def test_3a_one_purchase_shows_under_every_policy_it_matches(self, seeded: TestClient) -> None:
        """⭐ 한 거래가 여러 정책에 걸리면 **어느 쪽에서도** 보인다."""
        assert 2 in _ids(seeded, policy="WOMAN")
        assert 2 in _ids(seeded, policy="STARTUP")

    def test_3b_choosing_one_does_not_remove_it_from_the_other(self, seeded: TestClient) -> None:
        """어느 한 쪽에서 봤다고 다른 쪽에서 빠지지 않는다."""
        first = _ids(seeded, policy="WOMAN")
        assert _ids(seeded, policy="STARTUP") == [2]
        assert _ids(seeded, policy="WOMAN") == first


# ======================================================================
# Test 4  기업정보가 없는 정책 — 오류가 아니다
# ======================================================================
class TestAPolicyWithNoCompaniesIsEmptyNotBroken:
    def test_4a_it_returns_an_empty_list(self, seeded: TestClient) -> None:
        """기업정보를 받은 적 없는 정책을 골라도 오류가 나지 않는다."""
        payload = _reviews(seeded, policy="SELF_SUPPORT_VILLAGE")

        assert payload["items"] == []
        assert payload["page"]["total"] == 0

    def test_4b_an_unknown_policy_code_is_empty_too(self, seeded: TestClient) -> None:
        """⛔ 모르는 코드로 좁히면 **조용히 전체를 주지 않는다.**"""
        payload = _reviews(seeded, policy="NO_SUCH_POLICY")

        assert payload["items"] == []

    def test_4c_the_dashboard_status_is_untouched(self, seeded: TestClient) -> None:
        """⛔ 「조회불가」와 「미해당」의 뜻을 필터가 바꾸지 않는다(§17)."""
        payload = seeded.get("/dashboard/summary", params={"year": 2026}).json()
        row = next(
            item for item in payload["policies"] if item["policy_code"] == "SELF_SUPPORT_VILLAGE"
        )

        assert row["status"] == "COMPANY_DATA_NOT_REGISTERED"
        assert row["purchase_amount"] is None


# ======================================================================
# Test 5 · 6  기존 검색과 AND 로 걸린다
# ======================================================================
class TestItCombinesWithTheExistingSearch:
    def test_5_policy_and_business_number(self, seeded: TestClient) -> None:
        """정책 = 여성기업 · 검색 = 사업자등록번호 → 둘 다 만족하는 건만."""
        assert _ids(seeded, policy="WOMAN", search=_BOTH) == [2]

    def test_5b_a_business_number_outside_the_policy_finds_nothing(
        self, seeded: TestClient
    ) -> None:
        """⭐ 검색이 맞아도 정책이 아니면 나오지 않는다 — AND 다."""
        assert _ids(seeded, search=_NONE) == [3]
        assert _ids(seeded, policy="WOMAN", search=_NONE) == []

    def test_6_policy_and_amount(self, seeded: TestClient) -> None:
        """정책 = 여성기업 · 검색 = 금액."""
        assert _ids(seeded, policy="WOMAN", search="8000") == [4]

    def test_6b_the_search_rules_did_not_change(self, seeded: TestClient) -> None:
        """⛔ 검색 자체의 뜻을 바꾸지 않았다 — 정책 없이도 예전과 같다."""
        assert _ids(seeded, search="8000") == [4]


# ======================================================================
# Test 7  좁혀 본 건도 예전처럼 저장된다
# ======================================================================
class TestSavingFromTheFilteredListIsUnchanged:
    def test_7a_a_filtered_row_can_be_confirmed(self, db: Path, seeded: TestClient) -> None:
        """필터로 찾은 건을 기존 API 로 확정한다."""
        purchase_id = _ids(seeded, policy="WOMAN")[0]

        assert (
            seeded.put(
                f"/reviews/{purchase_id}",
                json={"final_purchase_type": SERVICE, "reviewed_by": "담당자"},
            ).status_code
            == 200
        )

        review = ReviewRepository(db).find_by_purchase_id(purchase_id)
        assert review is not None
        assert review.final_purchase_type == SERVICE

    def test_7b_the_history_still_records_it(self, seeded: TestClient) -> None:
        """⛔ 이력 구조를 바꾸지 않았다."""
        purchase_id = _ids(seeded, policy="WOMAN")[0]
        seeded.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": SERVICE, "reviewed_by": "담당자"},
        )

        history = seeded.get(f"/reviews/{purchase_id}/history").json()["items"]
        assert history[-1]["after_type"] == SERVICE

    def test_7c_the_confirmation_reaches_the_woman_calculation(self, seeded: TestClient) -> None:
        """필터로 좁혀 확정해도 계산 연결은 그대로다."""
        seeded.put("/reviews/1", json={"final_purchase_type": SERVICE, "reviewed_by": "담당자"})

        payload = seeded.get("/dashboard/summary", params={"year": 2026}).json()
        woman = next(row for row in payload["policies"] if row["policy_code"] == "WOMAN")
        service = next(entry for entry in woman["scoped_achievements"] if entry["scope"] == SERVICE)
        assert _won(service["purchase_amount"]) == 1_000


# ======================================================================
# Test 8  자동판정 규칙은 그대로다
# ======================================================================
class TestTheAutomaticRulesAreUnchanged:
    def test_8a_the_confirmed_account_is_still_goods(self, db: Path, seeded: TestClient) -> None:
        """도서인쇄비 건은 여전히 물품으로 자동 확정된다."""
        review = ReviewRepository(db).find_by_purchase_id(4)

        assert review is not None
        assert review.final_purchase_type == GOODS

    def test_8b_a_held_account_is_still_pending(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """⛔ 임차료는 이번에도 자동 확정하지 않는다 — PENDING 유지."""
        spend = _purchase_file(
            tmp_path / "rent.xlsx",
            [_purchase_row(day="2026-03-01", amount=1_000, business_no=_WOMAN, budget="임차료")],
        )
        assert (
            client.post(
                "/uploads/purchases",
                json={"file_path": str(spend), "year": 2026, "month": 3},
            ).status_code
            == 200
        )

        review = ReviewRepository(db).find_by_purchase_id(1)
        assert review is None or review.final_purchase_type is None

    def test_8c_the_rule_endpoint_still_works(self, seeded: TestClient) -> None:
        """``POST /reviews/apply-rules`` 동작이 그대로다."""
        payload = seeded.post("/reviews/apply-rules", params={"year": 2026}).json()

        assert payload["examined"] == 4
        assert payload["already_decided"] == 1  # 업로드 때 확정된 도서인쇄비
        assert payload["classified"] == 0


# ======================================================================
# Test 9  필터는 계산을 바꾸지 않는다
# ======================================================================
class TestTheFilterChangesNoCalculation:
    def test_9a_the_dashboard_is_identical_before_and_after_filtering(
        self, seeded: TestClient
    ) -> None:
        """⭐ 목록을 좁혀 봐도 달성률·총액이 그대로다."""
        before = seeded.get("/dashboard/summary", params={"year": 2026}).json()

        _reviews(seeded, policy="WOMAN")
        _reviews(seeded, policy="STARTUP")

        assert seeded.get("/dashboard/summary", params={"year": 2026}).json() == before

    def test_9b_the_woman_total_is_the_matching_amount(self, seeded: TestClient) -> None:
        """정책 필터의 합계와 여성기업 실적이 **같은 판정**에서 나온다."""
        payload = seeded.get("/dashboard/summary", params={"year": 2026}).json()
        woman = next(row for row in payload["policies"] if row["policy_code"] == "WOMAN")

        assert _won(woman["purchase_amount"]) == _amount_sum(seeded, policy="WOMAN") == 11_000

    def test_9c_the_filter_writes_nothing(self, db: Path, seeded: TestClient) -> None:
        """⛔ 조회가 검토 행을 만들지 않는다."""
        before = len(ReviewRepository(db).find_all())

        _reviews(seeded, policy="WOMAN")

        assert len(ReviewRepository(db).find_all()) == before


# ======================================================================
# §9  화면에 정책 칸이 있는가
# ======================================================================
class TestTheScreenHasThePolicyControl:
    @pytest.fixture
    def page(self, client: TestClient) -> str:
        response = client.get("/")
        assert response.status_code == 200
        body: str = response.text
        return body

    def test_10_the_review_screen_has_a_policy_select(self, page: str) -> None:
        assert 'id="review-policy"' in page
        assert "정책으로 검토 대상 좁히기" in page

    def test_11_it_sends_the_policy_parameter(self, page: str) -> None:
        assert 'parts.push("policy=" + encodeURIComponent(el("review-policy").value));' in page

    def test_12_the_options_come_from_the_existing_endpoint(self, page: str) -> None:
        """⛔ 정책 목록을 화면이 지어내지 않는다."""
        assert "fillReviewPolicies" in page
        assert "/dashboard/policy-display" in page

    def test_13_the_csv_export_follows_the_same_condition(self, page: str) -> None:
        """내보내기도 화면과 같은 조건을 쓴다 — 기존 `reviewParams` 그대로."""
        assert '"/reviews/export.csv?" + reviewParams(false)' in page
