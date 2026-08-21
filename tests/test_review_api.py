"""
tests.test_review_api

**검토 API** 검증 — ``/reviews``.

여기서 잡으려는 것은 세 가지입니다.

1. 응답이 **원본 · 분석 · 확정을 분리**해서 준다
2. ⛔ **자동 확정하지 않는다** — 확정값이 미리 채워져 있지 않다
3. ⛔ **원본이 바뀌지 않는다**

설계 근거: ``docs/REVIEW_INTERFACE_DESIGN.md`` §4
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.database.bootstrap import bootstrap
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models.classification import ClassificationResult, TypeCandidate
from procurement.models.purchase import Purchase

FIXED = date(2026, 3, 15)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """정책 seed 와 DB-2 테이블까지 준비된 DB."""
    path = tmp_path / "review-api.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path, period_date_field="payment_date"))


def _purchase(db_path: Path, description: str = "시설물 유지관리") -> int:
    """원본 한 건을 저장하고 ID 를 돌려줍니다."""
    saved = PurchaseRepository(db_path).insert(
        Purchase(
            business_no="2208162517",
            company_name="한빛산업개발",
            contract_date=FIXED,
            payment_date=FIXED,
            resolution_date=FIXED,
            issue_date=date(2026, 3, 10),
            description=description,
            budget_account="외주용역비",
            amount=Decimal("54648000"),
        )
    )
    assert saved.purchase_id is not None
    return saved.purchase_id


def _analyze(db_path: Path, purchase_id: int, *pairs: tuple[str, str]) -> None:
    """분석 결과를 심어 둡니다(분석 방법 미선택이므로 테스트가 직접 넣습니다)."""
    ReviewRepository(db_path).save_analysis(
        purchase_id,
        ClassificationResult(
            candidates=[
                TypeCandidate(purchase_type=code, score=Decimal(score), evidence=f"{code} 근거")
                for code, score in pairs
            ],
            analyzer_name="test-analyzer",
            analyzer_version="1",
        ),
    )


class TestListReviews:
    """``GET /reviews``."""

    def test_empty_when_no_purchase(self, client: TestClient) -> None:
        body = client.get("/reviews").json()

        assert body["items"] == []
        assert body["progress"]["total"] == 0

    def test_purchase_appears_without_a_review_row(self, client: TestClient, db_path: Path) -> None:
        """검토 행이 없어도 목록에 나온다. ⛔ 조회만으로 DB-2 에 쓰지 않는다."""
        _purchase(db_path)

        body = client.get("/reviews").json()

        assert len(body["items"]) == 1
        assert body["items"][0]["review"]["status"] == "PENDING"
        assert ReviewRepository(db_path).count() == 0, "조회가 행을 만들면 안 된다"

    def test_progress_counts(self, client: TestClient, db_path: Path) -> None:
        first = _purchase(db_path)
        _purchase(db_path, "LED 교체공사")
        client.put(f"/reviews/{first}", json={"final_purchase_type": CONSTRUCTION})

        body = client.get("/reviews").json()

        assert body["progress"]["total"] == 2
        assert body["progress"]["confirmed"] == 1
        assert body["progress"]["pending"] == 1

    @pytest.mark.parametrize("review_filter", ["ALL", "PENDING", "CONFIRMED", "AMBIGUOUS"])
    def test_filters_are_accepted(self, client: TestClient, review_filter: str) -> None:
        assert client.get(f"/reviews?review_filter={review_filter}").status_code == 200

    def test_unknown_filter_is_422(self, client: TestClient) -> None:
        assert client.get("/reviews?review_filter=WHATEVER").status_code == 422

    def test_ambiguous_filter_selects_split_candidates(
        self, client: TestClient, db_path: Path
    ) -> None:
        clear = _purchase(db_path, "LED 교체공사")
        split = _purchase(db_path, "시설물 유지관리")
        _analyze(db_path, clear, (CONSTRUCTION, "0.97"))
        _analyze(db_path, split, (SERVICE, "0.72"), (CONSTRUCTION, "0.68"))

        body = client.get("/reviews?review_filter=AMBIGUOUS").json()

        assert [item["source"]["purchase_id"] for item in body["items"]] == [split]


class TestResponseSeparatesSourceAnalysisReview:
    """⛔ 원본 · 분석 · 확정을 **분리**해서 준다."""

    def test_three_blocks(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _purchase(db_path)
        _analyze(db_path, purchase_id, (SERVICE, "0.72"), (CONSTRUCTION, "0.68"))

        body = client.get(f"/reviews/{purchase_id}").json()

        assert set(body) == {"source", "analysis", "review"}

    def test_source_is_the_original(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _purchase(db_path)

        source = client.get(f"/reviews/{purchase_id}").json()["source"]

        assert source["description"] == "시설물 유지관리"
        assert source["company_name"] == "한빛산업개발"
        assert source["business_no"] == "2208162517"
        assert source["amount"] == "54648000"
        assert source["resolution_date"] == "2026-03-15"
        assert source["issue_date"] == "2026-03-10"
        assert source["budget_account"] == "외주용역비"

    def test_analysis_carries_candidates_and_evidence(
        self, client: TestClient, db_path: Path
    ) -> None:
        purchase_id = _purchase(db_path)
        _analyze(db_path, purchase_id, (SERVICE, "0.72"), (CONSTRUCTION, "0.68"))

        analysis = client.get(f"/reviews/{purchase_id}").json()["analysis"]

        assert analysis["is_ambiguous"] is True
        assert [c["purchase_type"] for c in analysis["candidates"]] == [SERVICE, CONSTRUCTION]
        assert analysis["candidates"][0]["label"] == "용역"
        assert analysis["candidates"][0]["score"] == "0.72"
        assert analysis["candidates"][0]["evidence"]
        assert analysis["analyzer_name"] == "test-analyzer"

    def test_source_never_carries_a_type(self, client: TestClient, db_path: Path) -> None:
        """⛔ 원본 블록에 구매유형이 섞이지 않는다."""
        purchase_id = _purchase(db_path)
        client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": CONSTRUCTION})

        source = client.get(f"/reviews/{purchase_id}").json()["source"]

        assert "purchase_type" not in source
        assert "final_purchase_type" not in source
        assert source["description"] == "시설물 유지관리", "적요가 유형으로 덮이면 안 된다"


class TestNoAutoConfirmation:
    """⛔ **자동 확정하지 않는다.**"""

    def test_high_score_does_not_preselect(self, client: TestClient, db_path: Path) -> None:
        """0.97 이어도 확정값은 비어 있다.

        미리 채우면 담당자가 그대로 눌러 사실상 자동 확정이 됩니다.
        """
        purchase_id = _purchase(db_path, "LED 교체공사")
        _analyze(db_path, purchase_id, (CONSTRUCTION, "0.97"), (SERVICE, "0.21"))

        review = client.get(f"/reviews/{purchase_id}").json()["review"]

        assert review["final_purchase_type"] is None
        assert review["status"] == "PENDING"

    def test_analysis_alone_never_confirms(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _purchase(db_path)
        _analyze(db_path, purchase_id, (SERVICE, "0.99"))

        body = client.get("/reviews").json()

        assert body["progress"]["confirmed"] == 0


class TestOptions:
    """``GET /reviews/options`` — 선택지는 백엔드가 소유한다."""

    def test_four_options(self, client: TestClient) -> None:
        options = client.get("/reviews/options").json()

        assert [option["label"] for option in options] == ["공사", "용역", "물품", "판단 보류"]

    def test_undecided_is_null_not_a_type(self, client: TestClient) -> None:
        """'판단 보류' 는 유형이 아니라 값 없음이다."""
        options = client.get("/reviews/options").json()

        assert options[-1]["value"] is None


class TestConfirm:
    """``PUT /reviews/{purchase_id}``."""

    @pytest.mark.parametrize("purchase_type", [CONSTRUCTION, SERVICE, GOODS])
    def test_each_type(self, client: TestClient, db_path: Path, purchase_type: str) -> None:
        purchase_id = _purchase(db_path)

        body = client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": purchase_type, "reviewed_by": "김담당"},
        ).json()

        assert body["review"]["final_purchase_type"] == purchase_type
        assert body["review"]["status"] == "CONFIRMED"
        assert body["review"]["reviewed_by"] == "김담당"
        assert body["review"]["reviewed_at"]

    def test_undecided(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _purchase(db_path)

        body = client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": None}).json()

        assert body["review"]["final_purchase_type"] is None
        assert body["review"]["final_purchase_type_label"] is None
        assert body["review"]["status"] == "CONFIRMED"

    def test_unknown_type_is_422(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _purchase(db_path)

        response = client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": "ETC"})

        assert response.status_code == 422

    def test_missing_key_is_422(self, client: TestClient, db_path: Path) -> None:
        """키가 없으면 '바꾸지 않음' 과 '판단 보류' 를 구분할 수 없다."""
        purchase_id = _purchase(db_path)

        assert client.put(f"/reviews/{purchase_id}", json={}).status_code == 422

    def test_unknown_purchase_is_404(self, client: TestClient) -> None:
        response = client.put("/reviews/9999", json={"final_purchase_type": SERVICE})

        assert response.status_code == 404

    def test_note_is_kept(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _purchase(db_path)

        body = client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": CONSTRUCTION, "review_note": "현장 시공 포함"},
        ).json()

        assert body["review"]["review_note"] == "현장 시공 포함"


class TestReopenAndHistory:
    """재검토와 이력."""

    def test_reopen(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _purchase(db_path)
        client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": SERVICE})

        body = client.post(f"/reviews/{purchase_id}/reopen", json={}).json()

        assert body["review"]["status"] == "REOPENED"
        assert body["review"]["final_purchase_type"] == SERVICE, "이전 선택은 남는다"

    def test_history_records_every_change(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _purchase(db_path)
        _analyze(db_path, purchase_id, (SERVICE, "0.72"))
        client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": SERVICE})
        client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": CONSTRUCTION})

        body = client.get(f"/reviews/{purchase_id}/history").json()

        assert [item["action"] for item in body["items"]] == [
            "ANALYZED",
            "CONFIRMED",
            "CONFIRMED",
        ]
        assert body["items"][-1]["before_type"] == SERVICE
        assert body["items"][-1]["after_type"] == CONSTRUCTION


class TestOriginalIsUnchanged:
    """⛔ **원본(DB-1)이 바뀌지 않는다.**"""

    def test_purchase_row_is_identical(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _purchase(db_path)
        before = PurchaseRepository(db_path).find_by_id(purchase_id)

        _analyze(db_path, purchase_id, (SERVICE, "0.72"))
        client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": CONSTRUCTION, "review_note": "메모"},
        )
        client.post(f"/reviews/{purchase_id}/reopen", json={})

        assert PurchaseRepository(db_path).find_by_id(purchase_id) == before

    def test_purchase_count_is_unchanged(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _purchase(db_path)
        client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": GOODS})

        assert PurchaseRepository(db_path).count() == 1
