"""
tests.test_review_repository

**DB-2 (검토 · 분류)** 저장소 검증.

여기서 잡으려는 것은 "저장이 되는가" 만이 아니라, 다음 두 가지가 **구조적으로
불가능한가** 입니다.

1. 자동 분석이 담당자 확정값을 덮는 것
2. 검토가 원본(DB-1)을 건드리는 것

설계 근거: ``docs/DATABASE_PIPELINE_DESIGN.md`` §3
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models.classification import (
    ANALYZED,
    NOT_ANALYZED,
    ClassificationResult,
    TypeCandidate,
)
from procurement.models.purchase import Purchase
from procurement.models.review import (
    ACTION_ANALYZED,
    ACTION_CONFIRMED,
    ACTION_REOPENED,
    CONFIRMED,
    PENDING,
    REOPENED,
    ReviewValidationError,
)

FIXED = date(2026, 3, 15)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """DB-1 · DB-2 테이블이 만들어진 빈 DB."""
    path = tmp_path / "review.db"
    PurchaseRepository(path).create_table()
    ReviewRepository(path).create_table()
    return path


@pytest.fixture
def repo(db_path: Path) -> ReviewRepository:
    return ReviewRepository(db_path)


def _purchase(db_path: Path, description: str = "시설물 유지관리") -> Purchase:
    """원본(DB-1) 한 건을 저장합니다."""
    return PurchaseRepository(db_path).insert(
        Purchase(
            business_no="2208162517",
            company_name="한빛산업개발",
            contract_date=FIXED,
            payment_date=FIXED,
            resolution_date=FIXED,
            issue_date=FIXED,
            description=description,
            budget_account="외주용역비",
            amount=Decimal("54648000"),
        )
    )


def _result(*pairs: tuple[str, str], note: str = "") -> ClassificationResult:
    """후보 목록을 만든 분석 결과."""
    return ClassificationResult(
        candidates=[
            TypeCandidate(purchase_type=code, score=Decimal(score), evidence=f"{code} 근거")
            for code, score in pairs
        ],
        analyzer_name="test-analyzer",
        analyzer_version="1",
        status=ANALYZED,
        note=note,
    )


class TestSchema:
    """테이블 생성."""

    def test_create_table_is_idempotent(self, db_path: Path) -> None:
        ReviewRepository(db_path).create_table()
        ReviewRepository(db_path).create_table()
        assert ReviewRepository(db_path).count() == 0

    def test_ensure_creates_a_pending_row(self, repo: ReviewRepository, db_path: Path) -> None:
        """새 검토 행은 미분석 · 미확정으로 시작한다. ⛔ 기본 유형을 채우지 않는다."""
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None

        review = repo.ensure(purchase.purchase_id)

        assert review.analysis_status == NOT_ANALYZED
        assert review.review_status == PENDING
        assert review.final_purchase_type is None
        assert review.candidates == []

    def test_ensure_is_idempotent(self, repo: ReviewRepository, db_path: Path) -> None:
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None

        first = repo.ensure(purchase.purchase_id)
        second = repo.ensure(purchase.purchase_id)

        assert first.review_id == second.review_id
        assert repo.count() == 1


class TestAnalysisIsStored:
    """자동 분석 결과 저장."""

    def test_candidates_round_trip(self, repo: ReviewRepository, db_path: Path) -> None:
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None

        review = repo.save_analysis(
            purchase.purchase_id, _result((SERVICE, "0.72"), (CONSTRUCTION, "0.68"))
        )

        assert [candidate.purchase_type for candidate in review.candidates] == [
            SERVICE,
            CONSTRUCTION,
        ]
        assert review.candidates[0].score == Decimal("0.72")
        assert review.candidates[0].evidence == f"{SERVICE} 근거"
        assert review.analyzer_name == "test-analyzer"
        assert review.analyzer_version == "1"
        assert review.analyzed_at is not None

    def test_ambiguous_is_flagged(self, repo: ReviewRepository, db_path: Path) -> None:
        """후보가 갈리면 표시된다 — ⛔ 정렬용이며 자동 확정에 쓰지 않는다."""
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None

        review = repo.save_analysis(
            purchase.purchase_id, _result((SERVICE, "0.72"), (CONSTRUCTION, "0.68"))
        )

        assert review.is_ambiguous is True
        assert repo.find_ambiguous() == [review]

    def test_single_candidate_is_not_ambiguous(self, repo: ReviewRepository, db_path: Path) -> None:
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None

        review = repo.save_analysis(purchase.purchase_id, _result((CONSTRUCTION, "0.97")))

        assert review.is_ambiguous is False

    def test_no_candidate_is_allowed(self, repo: ReviewRepository, db_path: Path) -> None:
        """판단할 수 없으면 후보를 만들지 않는다 — 정상 상태다."""
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None

        review = repo.save_analysis(purchase.purchase_id, _result(note="방법 미선택"))

        assert review.candidates == []
        assert review.analysis_note == "방법 미선택"
        assert review.review_status == PENDING


class TestAnalysisNeverOverwritesConfirmation:
    """⛔ **자동 분석이 담당자 확정값을 덮지 않는다** — 가장 중요한 성질."""

    def test_reanalysis_keeps_the_confirmed_type(
        self, repo: ReviewRepository, db_path: Path
    ) -> None:
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None
        repo.save_analysis(purchase.purchase_id, _result((SERVICE, "0.72")))
        repo.confirm(purchase.purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")

        # 분석기를 다시 돌린다 — 이번에는 물품을 1순위로 추천한다.
        review = repo.save_analysis(purchase.purchase_id, _result((GOODS, "0.99")))

        assert review.final_purchase_type == CONSTRUCTION, "담당자 확정값이 유지되어야 한다"
        assert review.review_status == CONFIRMED
        assert review.reviewed_by == "김담당"
        # 분석 결과 자체는 갱신된다.
        assert review.candidates[0].purchase_type == GOODS

    def test_confirmation_keeps_the_analysis(self, repo: ReviewRepository, db_path: Path) -> None:
        """반대로 확정도 분석 결과를 지우지 않는다(무엇을 추천했는지 남는다)."""
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None
        repo.save_analysis(purchase.purchase_id, _result((SERVICE, "0.72")))

        review = repo.confirm(purchase.purchase_id, final_purchase_type=CONSTRUCTION)

        assert review.candidates[0].purchase_type == SERVICE
        assert review.analyzer_name == "test-analyzer"

    def test_analysis_sql_does_not_touch_confirmation_columns(self) -> None:
        """⛔ 분석 SQL 의 SET 목록에 확정 컬럼이 **없다**."""
        import inspect

        from procurement.database import review_repository

        source = inspect.getsource(review_repository.ReviewRepository.save_analysis)
        for column in ("final_purchase_type", "review_status", "reviewed_by", "reviewed_at"):
            assert f"{column} = ?" not in source, column

    def test_confirm_sql_does_not_touch_analysis_columns(self) -> None:
        """⛔ 확정 SQL 의 SET 목록에 분석 컬럼이 **없다**."""
        import inspect

        from procurement.database import review_repository

        source = inspect.getsource(review_repository.ReviewRepository.confirm)
        for column in ("candidates_json", "top_type", "top_score", "analyzer_name"):
            assert f"{column} = ?" not in source, column


class TestConfirmation:
    """담당자 확정."""

    @pytest.mark.parametrize("purchase_type", [CONSTRUCTION, SERVICE, GOODS])
    def test_each_allowed_type(
        self, repo: ReviewRepository, db_path: Path, purchase_type: str
    ) -> None:
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None

        review = repo.confirm(purchase.purchase_id, final_purchase_type=purchase_type)

        assert review.final_purchase_type == purchase_type
        assert review.review_status == CONFIRMED
        assert review.reviewed_at is not None

    def test_undecided_is_allowed(self, repo: ReviewRepository, db_path: Path) -> None:
        """**판단 보류** — 담당자가 모를 때 아무 값이나 고르게 만들지 않는다."""
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None

        review = repo.confirm(purchase.purchase_id, final_purchase_type=None)

        assert review.final_purchase_type is None
        assert review.final_purchase_type_label is None
        assert review.review_status == CONFIRMED

    def test_unknown_type_is_rejected(self, repo: ReviewRepository, db_path: Path) -> None:
        """⛔ 새 분류 체계를 만들지 않는다."""
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None

        with pytest.raises(ReviewValidationError, match="허용되지 않는 구매유형"):
            repo.confirm(purchase.purchase_id, final_purchase_type="ETC")

    def test_reconfirm_replaces_the_value(self, repo: ReviewRepository, db_path: Path) -> None:
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None
        repo.confirm(purchase.purchase_id, final_purchase_type=SERVICE)

        review = repo.confirm(purchase.purchase_id, final_purchase_type=GOODS)

        assert review.final_purchase_type == GOODS

    def test_reopen_keeps_the_previous_value(self, repo: ReviewRepository, db_path: Path) -> None:
        """⛔ 되돌려도 이전 선택을 지우지 않는다(무엇을 골랐었는지 보여야 한다)."""
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None
        repo.confirm(purchase.purchase_id, final_purchase_type=SERVICE)

        review = repo.reopen(purchase.purchase_id, reopened_by="박담당")

        assert review.review_status == REOPENED
        assert review.final_purchase_type == SERVICE


class TestHistory:
    """변경 이력 (append-only)."""

    def test_analysis_and_confirmation_are_recorded(
        self, repo: ReviewRepository, db_path: Path
    ) -> None:
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None
        repo.save_analysis(purchase.purchase_id, _result((SERVICE, "0.72")))
        repo.confirm(purchase.purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")

        history = repo.find_history(purchase.purchase_id)

        assert [entry.action for entry in history] == [ACTION_ANALYZED, ACTION_CONFIRMED]
        assert history[1].after_type == CONSTRUCTION
        assert history[1].changed_by == "김담당"

    def test_previous_value_is_kept(self, repo: ReviewRepository, db_path: Path) -> None:
        """수정하면 **이전 값**이 이력에 남는다."""
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None
        repo.confirm(purchase.purchase_id, final_purchase_type=SERVICE)
        repo.confirm(purchase.purchase_id, final_purchase_type=CONSTRUCTION)

        history = repo.find_history(purchase.purchase_id)

        assert history[-1].before_type == SERVICE
        assert history[-1].after_type == CONSTRUCTION

    def test_reopen_is_recorded(self, repo: ReviewRepository, db_path: Path) -> None:
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None
        repo.confirm(purchase.purchase_id, final_purchase_type=SERVICE)
        repo.reopen(purchase.purchase_id, note="현장 확인 필요")

        history = repo.find_history(purchase.purchase_id)

        assert history[-1].action == ACTION_REOPENED
        assert history[-1].note == "현장 확인 필요"

    def test_candidates_are_snapshotted(self, repo: ReviewRepository, db_path: Path) -> None:
        """그 시점의 후보가 이력에 함께 남는다."""
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None
        repo.save_analysis(purchase.purchase_id, _result((SERVICE, "0.72"), (GOODS, "0.31")))

        entry = repo.find_history(purchase.purchase_id)[0]

        assert [candidate.purchase_type for candidate in entry.candidates] == [SERVICE, GOODS]


class TestOriginalIsNeverModified:
    """⛔ **원본(DB-1)을 수정하지 않는다.**"""

    def test_purchase_row_is_unchanged(self, repo: ReviewRepository, db_path: Path) -> None:
        purchase = _purchase(db_path)
        assert purchase.purchase_id is not None
        before = PurchaseRepository(db_path).find_by_id(purchase.purchase_id)

        repo.save_analysis(purchase.purchase_id, _result((SERVICE, "0.72")))
        repo.confirm(purchase.purchase_id, final_purchase_type=CONSTRUCTION)
        repo.reopen(purchase.purchase_id)

        after = PurchaseRepository(db_path).find_by_id(purchase.purchase_id)
        assert after == before

    def test_repository_never_writes_to_purchase_table(self) -> None:
        """⛔ 소스에 ``purchase`` 테이블 쓰기 SQL 이 없다."""
        import inspect

        from procurement.database import review_repository

        source = inspect.getsource(review_repository).lower()
        for forbidden in ("update purchase ", "insert into purchase ", "delete from purchase"):
            assert forbidden not in source, forbidden


class TestProgress:
    """진행 상황 집계."""

    def test_counts(self, repo: ReviewRepository, db_path: Path) -> None:
        first = _purchase(db_path)
        second = _purchase(db_path, "LED 교체공사")
        assert first.purchase_id is not None and second.purchase_id is not None
        repo.save_analysis(first.purchase_id, _result((SERVICE, "0.72"), (CONSTRUCTION, "0.68")))
        repo.confirm(first.purchase_id, final_purchase_type=CONSTRUCTION)
        repo.ensure(second.purchase_id)

        progress = repo.progress()

        assert progress.total == 2
        assert progress.confirmed == 1
        assert progress.pending == 1
        assert progress.ambiguous == 1
