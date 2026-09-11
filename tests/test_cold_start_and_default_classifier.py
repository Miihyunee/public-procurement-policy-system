"""STEP 6 — Cold Start 와 운영 기본 분석기.

두 가지를 고정합니다.

1. **운영에는 아직 어떤 분석 방법도 연결되지 않았다** (지시 3-1)
   BM25 · RAG · FUSE 는 실험 코드로만 존재하고, 조립 지점(``app.py``)은
   분석기를 주입하지 않습니다.

2. **확정 사례가 하나도 없으면 후보도 없다** (지시 12)
   억지로 "용역" 을 내놓거나 "가장 가까운 유형" 을 자동 확정하지 않습니다.
   운영 초기에 DB-2 가 비어 있는 것은 정상 상태입니다.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement import app as app_module
from procurement.core.description_classifier import NoRuleClassifier
from procurement.database.bootstrap import init_db
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.experiments.bm25 import BM25Classifier
from procurement.experiments.corpus import ClassificationCorpus
from procurement.experiments.fuse import FUSEClassifier
from procurement.experiments.rag import RAGClassifier
from procurement.models.purchase import Purchase
from procurement.models.review import PENDING
from procurement.reviews.review_service import ReviewService

EMPTY = ClassificationCorpus.from_examples([])


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "coldstart.db")
    init_db(path)
    return path


def add_purchase(repository: PurchaseRepository, description: str) -> int:
    """합성 구매 1건."""
    purchase = repository.insert(
        Purchase(
            business_no="111-11-11111",
            company_name="가나건설",
            contract_date=date(2026, 3, 1),
            payment_date=date(2026, 3, 20),
            amount=Decimal("1000000"),
            resolution_date=date(2026, 3, 25),
            issue_date=date(2026, 3, 10),
            description=description,
            budget_account="외주용역비",
        )
    )
    assert purchase.purchase_id is not None
    return purchase.purchase_id


class TestProductionHasNoAnalyzerYet:
    """⛔ 지시 3-1 — 운영 기본 분석기는 여전히 '없음' 이다."""

    def test_build_review_service_injects_no_classifier(self) -> None:
        """조립 지점이 분석기를 넣지 않는다 (AST 검사).

        문자열 검색이 아니라 **호출 인자**를 뜯어본다. ``ReviewService(...)``
        생성 시 세 번째 인자나 ``classifier=`` 키워드가 있으면 실패한다.
        """
        tree = ast.parse(inspect.getsource(app_module.build_review_service))

        constructions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ReviewService"
        ]

        assert constructions, "build_review_service 가 ReviewService 를 만들지 않는다"
        for call in constructions:
            assert len(call.args) <= 2, "분석기를 위치 인자로 넣었습니다"
            assert not any(keyword.arg == "classifier" for keyword in call.keywords), (
                "분석기를 키워드 인자로 넣었습니다"
            )

    def test_service_without_a_classifier_does_not_analyze(self, db_path: str) -> None:
        """분석기가 없으면 분석을 돌려도 아무 후보가 생기지 않는다."""
        purchases = PurchaseRepository(db_path)
        reviews = ReviewRepository(db_path)
        service = ReviewService(purchases, reviews)
        purchase_id = add_purchase(purchases, "시설물 유지관리 노무비")

        target = service.analyze(purchase_id)

        assert target.review.candidates == []
        assert target.review.analyzer_name is None
        assert service.analyze_all() == 0

    def test_no_rule_classifier_is_still_the_documented_default(self) -> None:
        """기본 구현은 규칙 없는 분석기이며, 어떤 후보도 만들지 않는다."""
        result = NoRuleClassifier().classify("무엇이든 넣어 본다")

        assert result.candidates == []
        assert result.analyzer_name == "no-rule"


class TestColdStartProducesNoCandidate:
    """⛔ 지시 12 — 확정 사례가 없으면 후보도 없다."""

    @pytest.mark.parametrize(
        "description",
        [
            "처음 보는 적요",
            "옥외 안내판 정비",
            "LED 등기구 교체공사",
            "",
            "   ",
        ],
    )
    def test_bm25_on_an_empty_corpus(self, description: str) -> None:
        result = BM25Classifier(EMPTY).classify(description)

        assert result.candidates == []
        assert result.top is None

    @pytest.mark.parametrize("description", ["처음 보는 적요", "옥외 안내판 정비", ""])
    def test_rag_on_an_empty_corpus(self, description: str) -> None:
        result = RAGClassifier(EMPTY).classify(description)

        assert result.candidates == []
        assert result.top is None

    def test_fuse_on_an_empty_corpus(self) -> None:
        """하위 분석기가 전부 빈손이면 FUSE 도 빈손이다."""
        fuse = FUSEClassifier([BM25Classifier(EMPTY), RAGClassifier(EMPTY)])

        assert fuse.classify("처음 보는 적요").candidates == []

    def test_none_description_is_safe(self) -> None:
        """적요가 아예 없는 행도 오류 없이 후보 0개가 된다."""
        for classifier in (BM25Classifier(EMPTY), RAGClassifier(EMPTY)):
            assert classifier.classify(None).candidates == []

    def test_no_candidate_is_not_a_failure(self) -> None:
        """후보 0개는 **정상 결과**다 — 실패 상태로 표시하지 않는다."""
        result = BM25Classifier(EMPTY).classify("처음 보는 적요")

        assert result.status != "FAILED"
        assert result.note


class TestColdStartThroughTheReviewPipeline:
    """빈 DB-2 로 시작해도 검토 화면이 정상 동작한다."""

    def test_empty_database_yields_pending_items_with_no_candidates(self, db_path: str) -> None:
        purchases = PurchaseRepository(db_path)
        service = ReviewService(purchases, ReviewRepository(db_path))
        add_purchase(purchases, "처음 보는 적요 A")
        add_purchase(purchases, "처음 보는 적요 B")

        targets = service.list_targets()

        assert len(targets) == 2
        for target in targets:
            assert target.review.candidates == []
            assert target.review.review_status == PENDING
            # ⛔ 억지 후보도, 기본 유형도 없다
            assert target.review.final_purchase_type is None
            assert target.past_labels.total == 0

    def test_progress_counts_everything_as_pending(self, db_path: str) -> None:
        """운영 첫날: 전부 미확정, 분석 0건 — 이것이 정상이다."""
        purchases = PurchaseRepository(db_path)
        service = ReviewService(purchases, ReviewRepository(db_path))
        for index in range(3):
            add_purchase(purchases, f"처음 보는 적요 {index}")

        progress = service.progress()

        assert (progress.total, progress.confirmed, progress.pending) == (3, 0, 3)
        assert progress.not_analyzed == 3
        assert progress.ambiguous == 0

    def test_the_first_confirmation_seeds_the_history(self, db_path: str) -> None:
        """cold start 는 담당자가 한 건 확정하는 순간부터 풀리기 시작한다."""
        purchases = PurchaseRepository(db_path)
        service = ReviewService(purchases, ReviewRepository(db_path))
        first = add_purchase(purchases, "같은 적요")
        second = add_purchase(purchases, "같은 적요")

        assert service.get_target(second).past_labels.total == 0
        service.confirm(first, final_purchase_type="SERVICE", reviewed_by="김담당")

        assert service.get_target(second).past_labels.total == 1
