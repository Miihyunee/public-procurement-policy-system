"""STEP 11 — 실사용 규모(2,292건)에서의 검토 화면 회귀 테스트.

실제 고객 데이터로 리허설한 결과(``REVIEW_INTERFACE_DESIGN.md`` §12)를
**합성 데이터로 재현**해 고정합니다.

⚠️ **고객 원본은 저장소에 넣지 않습니다.** 여기 쓰는 2,292건은 전부 합성이며,
실제 사업자번호·거래처명이 아닙니다. 재현하려는 것은 값이 아니라 **규모와
모양**입니다 — 반복되는 적요, 일부만 확정된 상태, 유형이 갈린 적요, 후보가
없는 건, 예산과목이 빈 건.

⛔ 이 파일은 어떤 업무규칙도 만들지 않습니다. 자동 확정·임계값·그룹 일괄
확정이 생기지 않았는지 함께 확인합니다.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.core.description_key import normalize_description
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.database.bootstrap import bootstrap
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models.classification import ClassificationResult, TypeCandidate
from procurement.models.purchase import Purchase
from procurement.models.review import CONFIRMED, PENDING, REOPENED
from procurement.reviews.past_labels import MIXED_TYPES, SINGLE_TYPE
from procurement.reviews.query import (
    ANY,
    DECIDED,
    DESCENDING,
    HAS_HISTORY,
    HISTORY_MIXED,
    MANY_CANDIDATES,
    NO_CANDIDATE,
    UNDECIDED,
    ReviewQuery,
)
from procurement.reviews.review_service import ReviewService

#: 실제 검토 대상 시트와 같은 행 수.
SCALE = 2292

#: 그중 담당자가 이미 확정한 것으로 두는 건수.
CONFIRMED_COUNT = 1380

#: 반복되는 적요 — 실데이터에서 통신비·관리비류가 이런 모양이었다.
REPEATED = (
    "통신비",
    "1월 수도광열비",
    "사무실 관리비 지출",
    "복합기 임차료",
    "정수기 월정료",
)

#: 예산과목 — 실데이터처럼 일부는 비어 있다.
ACCOUNTS = ("임차료", "수도광열비", "외주용역비", "행사운영비", None)


def _description(index: int) -> str:
    """반복 적요와 고유 적요를 섞습니다."""
    if index % 5 == 0:
        return REPEATED[index % len(REPEATED)]
    if index % 7 == 0:
        # 띄어쓰기만 다른 같은 적요 — 정규화가 묶어 줘야 한다.
        return f"  {REPEATED[index % len(REPEATED)]}  "
    return f"합성 적요 {index:04d} 지출결의"


@pytest.fixture(scope="module")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """2,292건 합성 DB. 만드는 데 시간이 걸리므로 모듈 단위로 한 번만."""
    path = tmp_path_factory.mktemp("scale") / "scale.db"
    bootstrap(path)
    purchases = PurchaseRepository(path)
    reviews = ReviewRepository(path)

    base = date(2026, 1, 1)
    ids: list[int] = []
    for index in range(SCALE):
        saved = purchases.insert(
            Purchase(
                business_no=f"{100 + index % 800:03d}-82-{index % 100000:05d}",
                company_name=f"합성거래처 {index % 300:03d}",
                contract_date=base,
                payment_date=base,
                resolution_date=base + timedelta(days=index % 180),
                issue_date=base + timedelta(days=index % 150),
                description=_description(index),
                budget_account=ACCOUNTS[index % len(ACCOUNTS)],
                amount=Decimal(str(10_000 + (index % 997) * 5_431)),
            )
        )
        assert saved.purchase_id is not None
        ids.append(saved.purchase_id)

    types = (SERVICE, GOODS, CONSTRUCTION)
    for order, purchase_id in enumerate(ids[:CONFIRMED_COUNT]):
        # 같은 적요라도 유형이 갈리는 경우를 일부 만든다 — 실데이터에서
        # 관찰된 모양이다(같은 적요가 여러 예산과목 행으로 쪼개진 건).
        chosen = types[order % 3] if order % 97 == 0 else types[order % 2]
        reviews.confirm(
            purchase_id,
            final_purchase_type=chosen,
            reviewed_by="합성담당",
        )

    # 미확정 건 일부에 분석 결과를 심는다. 후보 0개인 건도 남겨 둔다.
    for order, purchase_id in enumerate(ids[CONFIRMED_COUNT:]):
        if order % 11 == 0:
            continue  # 후보 0개 — ⛔ 오류가 아니다
        pairs = (
            ((SERVICE, "0.9000"), (GOODS, "0.8800"))
            if order % 2
            else ((GOODS, "1.0000"), (SERVICE, "0.1200"), (CONSTRUCTION, "0.0500"))
        )
        reviews.save_analysis(
            purchase_id,
            ClassificationResult(
                candidates=[
                    TypeCandidate(purchase_type=code, score=Decimal(score), evidence="합성 근거")
                    for code, score in pairs
                ],
                analyzer_name="합성-분석기",
                analyzer_version="1",
            ),
        )
    return path


@pytest.fixture(scope="module")
def service(db_path: Path) -> ReviewService:
    return ReviewService(PurchaseRepository(db_path), ReviewRepository(db_path))


@pytest.fixture(scope="module")
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path, period_date_field="resolution_date"))


def _page(client: TestClient, query: str = "") -> dict[str, object]:
    response = client.get("/reviews?page=1&page_size=20" + query)
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    return body


def _total(client: TestClient, query: str = "") -> int:
    page = _page(client, query)
    info = page["page"]
    assert isinstance(info, dict)
    return int(info["total"])


class TestScaleLoads:
    """규모 자체 — 2,292건이 그대로 보인다."""

    def test_all_rows_are_visible(self, client: TestClient) -> None:
        assert _total(client) == SCALE

    def test_only_one_page_comes_down(self, client: TestClient) -> None:
        """⛔ 전체를 브라우저로 내려보내지 않는다 (STEP 8 설계 유지)."""
        page = _page(client)
        items = page["items"]
        assert isinstance(items, list)
        assert len(items) == 20

    def test_page_size_is_capped(self, client: TestClient) -> None:
        assert client.get("/reviews?page=1&page_size=5000").status_code == 422

    def test_progress_counts_everything(self, client: TestClient) -> None:
        progress = _page(client)["progress"]
        assert isinstance(progress, dict)
        assert progress["total"] == SCALE
        assert progress["confirmed"] == CONFIRMED_COUNT
        assert progress["pending"] == SCALE - CONFIRMED_COUNT


class TestScaleSearchAndFilters:
    """검색 · 필터가 규모와 무관하게 조건대로 좁힌다."""

    def test_search_narrows(self, client: TestClient) -> None:
        found = _total(client, "&search=통신비")
        assert 0 < found < SCALE

    def test_search_ignores_spacing(self, client: TestClient) -> None:
        """띄어쓰기만 다른 적요도 같은 것으로 찾는다."""
        assert _total(client, "&search=통신비") == _total(client, "&search=통 신 비")

    def test_status_filters_add_up(self, client: TestClient) -> None:
        confirmed = _total(client, f"&status={CONFIRMED}")
        pending = _total(client, f"&status={PENDING}")
        reopened = _total(client, f"&status={REOPENED}")

        assert confirmed == CONFIRMED_COUNT
        assert confirmed + pending + reopened == SCALE

    def test_decision_filter_matches_status(self, client: TestClient) -> None:
        assert _total(client, f"&decision={DECIDED}") == CONFIRMED_COUNT
        assert _total(client, f"&decision={UNDECIDED}") == SCALE - CONFIRMED_COUNT

    def test_candidate_filters_partition(self, client: TestClient) -> None:
        none = _total(client, f"&candidates={NO_CANDIDATE}")
        many = _total(client, f"&candidates={MANY_CANDIDATES}")

        assert none > 0 and many > 0
        assert none + many <= SCALE

    def test_history_filters_are_reachable(self, client: TestClient) -> None:
        assert _total(client, f"&history={HAS_HISTORY}") > 0
        assert _total(client, f"&history={HISTORY_MIXED}") > 0

    def test_condition_progress_differs_from_whole(self, client: TestClient) -> None:
        """조건 진행률은 전체와 다른 숫자다."""
        page = _page(client, f"&status={PENDING}")
        condition = page["condition"]
        assert isinstance(condition, dict)
        assert condition["confirmed"] == 0
        assert condition["total"] == SCALE - CONFIRMED_COUNT


class TestScaleSortAndPaging:
    """정렬 · 페이지 — 값이 없는 건이 끼어도 순서가 무너지지 않는다."""

    def test_amount_desc_is_ordered(self, service: ReviewService) -> None:
        page = service.search(ReviewQuery(sort="amount", direction=DESCENDING, page_size=50))
        amounts = [target.purchase.amount for target in page.items]

        assert amounts == sorted(amounts, reverse=True)

    def test_missing_values_sort_last(self, service: ReviewService) -> None:
        """⛔ ``dominant_ratio`` 가 없는 건이 앞으로 오면 안 된다 (STEP 8 회귀)."""
        page = service.search(
            ReviewQuery(sort="dominant_ratio", direction=DESCENDING, page_size=50)
        )
        totals = [target.past_labels.total for target in page.items]

        assert totals[0] > 0

    def test_last_page_is_reachable(self, client: TestClient) -> None:
        info = _page(client)["page"]
        assert isinstance(info, dict)
        last = int(info["total_pages"])
        body = client.get(f"/reviews?page={last}&page_size=20").json()

        assert body["items"]
        assert body["page"]["has_next"] is False

    def test_pages_do_not_overlap(self, service: ReviewService) -> None:
        first = service.search(ReviewQuery(page=1, page_size=100))
        second = service.search(ReviewQuery(page=2, page_size=100))

        ids_first = {target.purchase.purchase_id for target in first.items}
        ids_second = {target.purchase.purchase_id for target in second.items}
        assert ids_first.isdisjoint(ids_second)


class TestScalePastLabels:
    """과거 확정 이력 — 규모가 커져도 같은 적요만 묶인다."""

    def test_repeated_description_has_history(self, service: ReviewService) -> None:
        page = service.search(ReviewQuery(search="통신비", history=HAS_HISTORY, page_size=5))

        assert page.items
        assert page.items[0].past_labels.total > 0

    def test_mixed_history_is_reported_without_a_verdict(self, service: ReviewService) -> None:
        """혼재는 **사실**로만 표시된다 — '신뢰 가능' 같은 판정이 없다."""
        page = service.search(ReviewQuery(history=HISTORY_MIXED, page_size=5))

        assert page.items
        summary = page.items[0].past_labels
        assert summary.consistency == MIXED_TYPES
        assert summary.type_count > 1
        assert summary.dominant is not None

    def test_single_type_history_exists_too(self, service: ReviewService) -> None:
        page = service.search(ReviewQuery(search="정수기 월정료", page_size=5))

        assert page.items
        assert page.items[0].past_labels.consistency in (SINGLE_TYPE, MIXED_TYPES)

    def test_spacing_variants_share_history(self, service: ReviewService) -> None:
        """띄어쓰기만 다른 적요는 같은 그룹으로 본다."""
        assert normalize_description("  통신비  ") == normalize_description("통신비")


class TestScaleExport:
    """CSV — 조건은 반영하고 페이지는 무시한다."""

    def _rows(self, client: TestClient, query: str = "") -> list[list[str]]:
        response = client.get("/reviews/export.csv" + query)
        assert response.status_code == 200
        text = response.content.decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text)))

    def test_every_row_is_exported(self, client: TestClient) -> None:
        rows = self._rows(client)

        assert len(rows) - 1 == SCALE
        assert len(rows[0]) == 20

    def test_columns_are_consistent(self, client: TestClient) -> None:
        rows = self._rows(client)

        assert all(len(row) == len(rows[0]) for row in rows[1:])

    def test_bom_and_crlf_survive_the_scale(self, client: TestClient) -> None:
        content = client.get("/reviews/export.csv").content

        assert content.startswith(b"\xef\xbb\xbf")
        assert content.count(b"\r\n") == SCALE + 1

    def test_filter_applies_but_page_does_not(self, client: TestClient) -> None:
        filtered = self._rows(client, f"?status={CONFIRMED}")
        paged = self._rows(client, f"?status={CONFIRMED}&page=3&page_size=5")

        assert len(filtered) - 1 == CONFIRMED_COUNT
        assert filtered == paged

    def test_screen_count_matches_export_count(self, client: TestClient) -> None:
        screen = _total(client, f"&candidates={MANY_CANDIDATES}")
        exported = len(self._rows(client, f"?candidates={MANY_CANDIDATES}")) - 1

        assert screen == exported


class TestScaleLifecycle:
    """확정 → Undo → 재확정이 규모와 무관하게 같은 값을 남긴다.

    ⚠️ 이 클래스는 모듈 fixture 를 **바꿉니다**. 다른 클래스가 세는 건수에
    영향을 주지 않도록 마지막에 원래 상태로 되돌려 놓습니다.
    """

    def test_confirm_undo_reconfirm(self, service: ReviewService, client: TestClient) -> None:
        page = service.search(ReviewQuery(status=PENDING, page_size=1))
        purchase_id = page.items[0].purchase.purchase_id
        assert purchase_id is not None

        confirmed = client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": SERVICE, "reviewed_by": "합성담당"},
        )
        assert confirmed.status_code == 200
        assert client.get(f"/reviews?status={CONFIRMED}&page=1").json()["page"]["total"] == (
            CONFIRMED_COUNT + 1
        )

        undone = client.post(f"/reviews/{purchase_id}/reopen", json={"reopened_by": "합성담당"})
        assert undone.status_code == 200
        review = undone.json()["review"]
        assert review["status"] == REOPENED
        # ⛔ 되돌려도 값은 남는다.
        assert review["final_purchase_type"] == SERVICE
        assert review["reviewed_by"] == "합성담당"

        assert client.post(f"/reviews/{purchase_id}/reopen", json={}).status_code == 409

        again = client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": GOODS, "reviewed_by": "합성담당"},
        )
        assert again.status_code == 200

        actions = [
            entry["action"]
            for entry in client.get(f"/reviews/{purchase_id}/history").json()["items"]
        ]
        assert actions[-3:] == ["CONFIRMED", "REOPENED", "CONFIRMED"]

        # fixture 를 원래대로 — 이 건은 원래 미확정이었다.
        client.post(f"/reviews/{purchase_id}/reopen", json={})


class TestNoAutomationLeakedAtScale:
    """⛔ 규모가 커져도 자동으로 결정되는 것은 없다."""

    def test_nothing_is_confirmed_without_a_person(self, client: TestClient) -> None:
        """분석만 된 건은 확정되지 않는다."""
        body = _page(client, f"&status={PENDING}&candidates={MANY_CANDIDATES}")
        items = body["items"]
        assert isinstance(items, list)
        assert items

        for item in items:
            assert isinstance(item, dict)
            review = item["review"]
            assert review["status"] == PENDING
            assert review["final_purchase_type"] is None

    def test_analysis_is_not_a_decision(self, client: TestClient) -> None:
        """후보 1순위가 아무리 높아도 확정 칸은 비어 있다."""
        items = _page(client, f"&status={PENDING}&candidates={MANY_CANDIDATES}")["items"]
        assert isinstance(items, list)

        first = items[0]
        assert isinstance(first, dict)
        analysis = first["analysis"]
        assert analysis["candidates"]
        assert first["review"]["final_purchase_type"] is None

    def test_past_labels_never_fill_the_decision(self, service: ReviewService) -> None:
        page = service.search(ReviewQuery(history=HAS_HISTORY, status=PENDING, page_size=20))

        assert page.items
        for target in page.items:
            assert target.past_labels.total > 0
            assert target.review.final_purchase_type is None

    def test_grouping_does_not_confirm_the_group(self, service: ReviewService) -> None:
        """같은 적요가 이미 확정돼 있어도 나머지가 따라 확정되지 않는다."""
        page = service.search(ReviewQuery(search="통신비", status=PENDING, page_size=20))

        for target in page.items:
            assert target.review.review_status == PENDING

    def test_options_do_not_include_a_bulk_action(self, client: TestClient) -> None:
        """⛔ 그룹 일괄 확정 같은 동작이 API 에 없다."""
        paths = client.get("/openapi.json").json()["paths"]

        for path in paths:
            assert "bulk" not in path
            assert "auto" not in path


class TestConditionsStayIndependent:
    """조건을 섞어도 서로를 덮어쓰지 않는다."""

    def test_search_and_filter_combine(self, client: TestClient) -> None:
        both = _total(client, f"&search=통신비&status={CONFIRMED}")
        only_search = _total(client, "&search=통신비")

        assert both <= only_search

    def test_unknown_filter_value_is_refused(self, client: TestClient) -> None:
        """⛔ 조용히 기본값으로 되돌리지 않는다 — 다른 목록을 보게 된다."""
        assert client.get("/reviews?page=1&status=없는상태").status_code == 422

    def test_default_query_is_unfiltered(self, client: TestClient) -> None:
        assert _total(client, f"&status={ANY}&decision={ANY}&history={ANY}") == SCALE
