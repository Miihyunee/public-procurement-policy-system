"""
procurement.database.review_repository

**DB-2 (검토 · 분류)** 영속화.

두 테이블을 다룹니다.

===========================  ==================================================
``purchase_review``          검토 **현재 상태** (구매 1건 : 검토 1건)
``purchase_review_history``  변경 **이력** (append-only)
===========================  ==================================================

.. warning::
    ⛔ **원본(DB-1)을 건드리지 않습니다.**

    이 Repository 는 ``purchase`` 테이블에 **쓰지 않습니다.** 조회 시 원본을
    함께 보여줘야 할 때만 ``JOIN`` 으로 읽습니다.

.. warning::
    ⛔ **자동 분석이 담당자 확정값을 덮지 않습니다.**

    쓰기 메서드가 둘로 나뉘어 있고, 각자 자기 영역의 컬럼만 UPDATE 합니다.

    ==========================  ==========================================
    :meth:`~ReviewRepository.save_analysis`  분석 컬럼만
    :meth:`~ReviewRepository.confirm`        확정 컬럼만
    ==========================  ==========================================

설계 근거: ``docs/DATABASE_PIPELINE_DESIGN.md`` §3
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from procurement.database.base import BaseRepository
from procurement.models.classification import (
    ANALYZED,
    ClassificationResult,
    TypeCandidate,
)
from procurement.models.review import (
    ACTION_ANALYZED,
    ACTION_CONFIRMED,
    ACTION_REOPENED,
    CONFIRMED,
    PENDING,
    REOPENED,
    REVIEW_STATUSES,
    PurchaseReview,
    ReviewHistoryEntry,
    ReviewProgress,
    ReviewValidationError,
    validate_final_purchase_type,
)

#: 검토 현재 상태. ``purchase_id`` 는 UNIQUE — 구매 1건에 검토 1건.
#:
#: ⚠️ 분석 컬럼과 확정 컬럼을 **물리적으로 나눠** 두었습니다. 한쪽을 쓰는 SQL 이
#: 다른 쪽 컬럼을 건드리지 않는다는 것이 SQL 문만 봐도 드러나야 하기 때문입니다.
CREATE_REVIEW_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS purchase_review (
    review_id INTEGER PRIMARY KEY,
    purchase_id INTEGER NOT NULL UNIQUE,

    analysis_status TEXT NOT NULL,
    analyzer_name TEXT,
    analyzer_version TEXT,
    analyzed_at DATETIME,
    candidates_json TEXT,
    top_type TEXT,
    top_score NUMERIC,
    is_ambiguous INTEGER NOT NULL DEFAULT 0,
    analysis_note TEXT,

    review_status TEXT NOT NULL,
    final_purchase_type TEXT,
    reviewed_by TEXT,
    reviewed_at DATETIME,
    review_note TEXT,

    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

#: 변경 이력. **append-only** — UPDATE·DELETE 하지 않습니다.
CREATE_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS purchase_review_history (
    history_id INTEGER PRIMARY KEY,
    purchase_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    changed_at DATETIME NOT NULL,
    changed_by TEXT,
    before_type TEXT,
    after_type TEXT,
    note TEXT,
    candidates_json TEXT
)
"""

#: 검토 목록·이력 조회에 쓰는 인덱스.
CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_review_history_purchase "
    "ON purchase_review_history (purchase_id)",
    "CREATE INDEX IF NOT EXISTS idx_review_status ON purchase_review (review_status)",
)


def _to_db(value: datetime) -> str:
    """datetime 을 SQLite 저장용 ISO 문자열로 변환합니다."""
    return value.isoformat(sep=" ")


def _from_db(value: str | None) -> datetime | None:
    """SQLite 에서 읽은 ISO 문자열을 datetime 으로 변환합니다."""
    return datetime.fromisoformat(value) if value else None


def _candidates_to_json(candidates: Sequence[TypeCandidate]) -> str:
    """후보 목록을 JSON 문자열로 변환합니다.

    후보 개수를 컬럼으로 고정하지 않는 이유는, 분석 방법(BM25 · RAG · FUSE)에
    따라 후보 수가 달라지기 때문입니다. 컬럼을 고정하면 방법을 바꿀 때 스키마가
    흔들립니다(``DATABASE_PIPELINE_DESIGN.md`` §3.2).
    """
    return json.dumps(
        [
            {
                "purchase_type": candidate.purchase_type,
                "score": str(candidate.score),
                "evidence": candidate.evidence,
            }
            for candidate in candidates
        ],
        ensure_ascii=False,
    )


def _candidates_from_json(raw: str | None) -> list[TypeCandidate]:
    """JSON 문자열을 후보 목록으로 되돌립니다."""
    if not raw:
        return []
    parsed: list[dict[str, Any]] = json.loads(raw)
    return [
        TypeCandidate(
            purchase_type=str(item["purchase_type"]),
            score=Decimal(str(item["score"])),
            evidence=str(item.get("evidence", "")),
        )
        for item in parsed
    ]


class ReviewRepository(BaseRepository):
    """DB-2 데이터 접근 계층."""

    table_name = "purchase_review"

    def create_table(self) -> None:
        """검토·이력 테이블과 인덱스를 만듭니다(없을 때만).

        ``CREATE TABLE IF NOT EXISTS`` 이므로 반복 호출해도 안전하며, 기존
        데이터를 건드리지 않습니다.
        """
        with self.connection() as conn:
            conn.execute(CREATE_REVIEW_TABLE_SQL)
            conn.execute(CREATE_HISTORY_TABLE_SQL)
            for statement in CREATE_INDEX_SQL:
                conn.execute(statement)

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------
    def find_by_purchase_id(self, purchase_id: int) -> PurchaseReview | None:
        """구매 ID 로 검토 상태를 조회합니다.

        Args:
            purchase_id: DB-1 구매 ID.

        Returns:
            :class:`PurchaseReview`. 아직 검토 행이 없으면 ``None``.
        """
        rows = self.execute("SELECT * FROM purchase_review WHERE purchase_id = ?", (purchase_id,))
        return self._row_to_review(rows[0]) if rows else None

    def find_all(self) -> list[PurchaseReview]:
        """전체 검토 상태를 구매 ID 순으로 조회합니다."""
        rows = self.execute("SELECT * FROM purchase_review ORDER BY purchase_id")
        return [self._row_to_review(row) for row in rows]

    def confirmed_fingerprint(self) -> tuple[int, str | None]:
        """확정 상태의 **값싼 지문** — ``(확정 건수, 가장 늦은 확정 시각)``.

        같은 지문이면 확정 내용이 그대로라고 보고, 과거 이력 색인을 다시
        만들지 않습니다(:class:`~procurement.reviews.review_service.ReviewService`).

        전체 행을 읽지 않고 집계 한 번으로 끝나므로, 색인을 매번 새로 만드는
        것보다 훨씬 쌉니다.

        .. note::
            **왜 건수와 시각을 함께 보는가.**

            - ``CONFIRMED`` → ``REOPENED`` : 건수가 줄어든다
            - 같은 건을 다른 유형으로 재확정 : 건수는 같지만 확정 시각이 바뀐다

            둘 중 하나만 보면 각각을 놓칩니다.

        .. note::
            **왜 ``updated_at`` 이 아니라 ``reviewed_at`` 인가.**
            ``updated_at`` 은 **재분석에도** 바뀝니다. 분석은 확정 이력을
            건드리지 않으므로, 그걸로 판단하면 아무 이유 없이 색인을 다시
            만들게 됩니다. ``reviewed_at`` 은 담당자가 확정할 때만 바뀝니다.

        .. note::
            **왜 서비스 밖에 두는가.** 서비스가 아니라 **DB 상태**를 근거로
            판단해야, 테스트나 다른 코드가 Repository 를 직접 고쳐도 낡은
            색인이 남지 않습니다.

        Returns:
            ``(건수, ISO 문자열 또는 None)``. 확정이 하나도 없으면 ``(0, None)``.
        """
        rows = self.execute(
            "SELECT COUNT(*) AS n, MAX(reviewed_at) AS latest "
            "FROM purchase_review WHERE review_status = ?",
            (CONFIRMED,),
        )
        if not rows:
            return (0, None)
        row = rows[0]
        latest = row["latest"]
        return (int(row["n"]), None if latest is None else str(latest))

    def find_by_review_status(self, review_status: str) -> list[PurchaseReview]:
        """검토 상태로 걸러 조회합니다.

        Args:
            review_status: :data:`~procurement.models.review.PENDING` 등.

        Raises:
            ReviewValidationError: 허용되지 않는 상태값인 경우.
        """
        self._validate_review_status(review_status)
        rows = self.execute(
            "SELECT * FROM purchase_review WHERE review_status = ? ORDER BY purchase_id",
            (review_status,),
        )
        return [self._row_to_review(row) for row in rows]

    def find_ambiguous(self) -> list[PurchaseReview]:
        """후보가 갈려 담당자가 **먼저 볼** 건을 조회합니다.

        ⛔ 자동 확정·자동 제외에 쓰지 않습니다. 정렬·필터 용도입니다.
        """
        rows = self.execute(
            "SELECT * FROM purchase_review WHERE is_ambiguous = 1 ORDER BY purchase_id"
        )
        return [self._row_to_review(row) for row in rows]

    def progress(self) -> ReviewProgress:
        """검토 진행 상황을 집계합니다."""
        rows = self.execute(
            "SELECT "
            "COUNT(*) AS total, "
            "SUM(CASE WHEN review_status = ? THEN 1 ELSE 0 END) AS confirmed, "
            "SUM(CASE WHEN review_status <> ? THEN 1 ELSE 0 END) AS pending, "
            "SUM(CASE WHEN is_ambiguous = 1 THEN 1 ELSE 0 END) AS ambiguous, "
            "SUM(CASE WHEN analysis_status = ? THEN 0 ELSE 1 END) AS not_analyzed "
            "FROM purchase_review",
            (CONFIRMED, CONFIRMED, ANALYZED),
        )
        row = rows[0]
        return ReviewProgress(
            total=int(row["total"] or 0),
            confirmed=int(row["confirmed"] or 0),
            pending=int(row["pending"] or 0),
            ambiguous=int(row["ambiguous"] or 0),
            not_analyzed=int(row["not_analyzed"] or 0),
        )

    def find_history(self, purchase_id: int) -> list[ReviewHistoryEntry]:
        """구매 한 건의 변경 이력을 시간순으로 조회합니다."""
        rows = self.execute(
            "SELECT * FROM purchase_review_history WHERE purchase_id = ? "
            "ORDER BY changed_at, history_id",
            (purchase_id,),
        )
        return [self._row_to_history(row) for row in rows]

    def count(self) -> int:
        """검토 행 수를 반환합니다."""
        rows = self.execute("SELECT COUNT(*) AS cnt FROM purchase_review")
        return int(rows[0]["cnt"])

    # ------------------------------------------------------------------
    # 쓰기 — 검토 행 준비
    # ------------------------------------------------------------------
    def ensure(self, purchase_id: int) -> PurchaseReview:
        """검토 행이 없으면 만들고, 있으면 그대로 돌려줍니다(멱등).

        새로 만든 행은 ``NOT_ANALYZED`` · ``PENDING`` 이며, 확정값은 비어
        있습니다. ⛔ 기본 유형을 채워 넣지 않습니다.

        Args:
            purchase_id: DB-1 구매 ID.

        Returns:
            :class:`PurchaseReview`.
        """
        existing = self.find_by_purchase_id(purchase_id)
        if existing is not None:
            return existing

        now = datetime.now()
        review = PurchaseReview(purchase_id=purchase_id)
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO purchase_review "
                "(purchase_id, analysis_status, is_ambiguous, review_status, "
                "created_at, updated_at) VALUES (?, ?, 0, ?, ?, ?)",
                (
                    purchase_id,
                    review.analysis_status,
                    review.review_status,
                    _to_db(now),
                    _to_db(now),
                ),
            )
        found = self.find_by_purchase_id(purchase_id)
        assert found is not None  # 방금 넣었으므로 존재한다
        return found

    # ------------------------------------------------------------------
    # 쓰기 — 자동 분석 (⛔ 확정 컬럼을 건드리지 않는다)
    # ------------------------------------------------------------------
    def save_analysis(self, purchase_id: int, result: ClassificationResult) -> PurchaseReview:
        """분석 결과를 저장합니다.

        .. warning::
            ⛔ **확정 컬럼을 UPDATE 하지 않습니다.** SQL 의 SET 목록에
            ``final_purchase_type`` · ``review_status`` · ``reviewed_by`` ·
            ``reviewed_at`` 가 **없습니다.** 재분석을 몇 번 돌려도 담당자가
            확정한 값은 그대로 남습니다.

        Args:
            purchase_id: DB-1 구매 ID.
            result: 분석기가 만든 결과.

        Returns:
            갱신된 :class:`PurchaseReview`.
        """
        self.ensure(purchase_id)
        now = datetime.now()
        top = result.top

        with self.connection() as conn:
            conn.execute(
                "UPDATE purchase_review SET "
                "analysis_status = ?, analyzer_name = ?, analyzer_version = ?, "
                "analyzed_at = ?, candidates_json = ?, top_type = ?, top_score = ?, "
                "is_ambiguous = ?, analysis_note = ?, updated_at = ? "
                "WHERE purchase_id = ?",
                (
                    result.status,
                    result.analyzer_name,
                    result.analyzer_version,
                    _to_db(now),
                    _candidates_to_json(result.candidates),
                    top.purchase_type if top else None,
                    str(top.score) if top else None,
                    1 if result.is_ambiguous else 0,
                    result.note or None,
                    _to_db(now),
                    purchase_id,
                ),
            )
            self._append_history(
                conn,
                ReviewHistoryEntry(
                    purchase_id=purchase_id,
                    action=ACTION_ANALYZED,
                    changed_at=now,
                    changed_by=result.analyzer_name,
                    note=result.note or None,
                    candidates=list(result.candidates),
                ),
            )

        updated = self.find_by_purchase_id(purchase_id)
        assert updated is not None
        return updated

    # ------------------------------------------------------------------
    # 쓰기 — 담당자 확정 (⛔ 분석 컬럼을 건드리지 않는다)
    # ------------------------------------------------------------------
    def confirm(
        self,
        purchase_id: int,
        *,
        final_purchase_type: str | None,
        reviewed_by: str | None = None,
        review_note: str | None = None,
    ) -> PurchaseReview:
        """담당자의 최종 선택을 확정합니다.

        .. warning::
            ⛔ **분석 컬럼을 UPDATE 하지 않습니다.** 담당자 확정이 분석 결과를
            지우지 않으므로, 나중에 "분석은 무엇을 추천했고 담당자는 무엇을
            골랐는가" 를 비교할 수 있습니다.

        Args:
            purchase_id: DB-1 구매 ID.
            final_purchase_type: ``CONSTRUCTION`` · ``SERVICE`` · ``GOODS``
                또는 ``None``(**판단 보류**).
            reviewed_by: 확정자.
            review_note: 담당자 메모.

        Returns:
            갱신된 :class:`PurchaseReview`.

        Raises:
            ReviewValidationError: 허용되지 않는 유형값인 경우.
        """
        validate_final_purchase_type(final_purchase_type)

        before = self.ensure(purchase_id)
        now = datetime.now()

        with self.connection() as conn:
            conn.execute(
                "UPDATE purchase_review SET "
                "review_status = ?, final_purchase_type = ?, reviewed_by = ?, "
                "reviewed_at = ?, review_note = ?, updated_at = ? "
                "WHERE purchase_id = ?",
                (
                    CONFIRMED,
                    final_purchase_type,
                    reviewed_by,
                    _to_db(now),
                    review_note,
                    _to_db(now),
                    purchase_id,
                ),
            )
            self._append_history(
                conn,
                ReviewHistoryEntry(
                    purchase_id=purchase_id,
                    action=ACTION_CONFIRMED,
                    changed_at=now,
                    changed_by=reviewed_by,
                    before_type=before.final_purchase_type,
                    after_type=final_purchase_type,
                    note=review_note,
                    candidates=list(before.candidates),
                ),
            )

        updated = self.find_by_purchase_id(purchase_id)
        assert updated is not None
        return updated

    def reopen(
        self, purchase_id: int, *, reopened_by: str | None = None, note: str | None = None
    ) -> PurchaseReview:
        """확정을 되돌려 다시 검토 상태로 만듭니다.

        ⛔ 이전 확정값을 지우지 않습니다. 값은 그대로 두고 상태만
        :data:`~procurement.models.review.REOPENED` 로 바꿔, 담당자가 무엇을
        골랐었는지 화면에서 계속 볼 수 있게 합니다.

        Args:
            purchase_id: DB-1 구매 ID.
            reopened_by: 되돌린 사람.
            note: 사유.

        Returns:
            갱신된 :class:`PurchaseReview`.
        """
        before = self.ensure(purchase_id)
        now = datetime.now()

        with self.connection() as conn:
            conn.execute(
                "UPDATE purchase_review SET review_status = ?, updated_at = ? "
                "WHERE purchase_id = ?",
                (REOPENED, _to_db(now), purchase_id),
            )
            self._append_history(
                conn,
                ReviewHistoryEntry(
                    purchase_id=purchase_id,
                    action=ACTION_REOPENED,
                    changed_at=now,
                    changed_by=reopened_by,
                    before_type=before.final_purchase_type,
                    after_type=before.final_purchase_type,
                    note=note,
                    candidates=list(before.candidates),
                ),
            )

        updated = self.find_by_purchase_id(purchase_id)
        assert updated is not None
        return updated

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_review_status(review_status: str) -> None:
        """검토 상태값을 검증합니다."""
        if review_status not in REVIEW_STATUSES:
            allowed = " · ".join(sorted(REVIEW_STATUSES))
            raise ReviewValidationError(
                f"허용되지 않는 검토 상태입니다: {review_status!r} (허용: {allowed})"
            )

    @staticmethod
    def _append_history(conn: sqlite3.Connection, entry: ReviewHistoryEntry) -> None:
        """이력을 추가합니다(append-only)."""
        conn.execute(
            "INSERT INTO purchase_review_history "
            "(purchase_id, action, changed_at, changed_by, before_type, after_type, "
            "note, candidates_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.purchase_id,
                entry.action,
                _to_db(entry.changed_at),
                entry.changed_by,
                entry.before_type,
                entry.after_type,
                entry.note,
                _candidates_to_json(entry.candidates),
            ),
        )

    @staticmethod
    def _row_to_review(row: sqlite3.Row) -> PurchaseReview:
        """SQLite Row 를 :class:`PurchaseReview` 로 변환합니다."""
        return PurchaseReview(
            review_id=row["review_id"],
            purchase_id=row["purchase_id"],
            analysis_status=row["analysis_status"],
            analyzer_name=row["analyzer_name"],
            analyzer_version=row["analyzer_version"],
            analyzed_at=_from_db(row["analyzed_at"]),
            candidates=_candidates_from_json(row["candidates_json"]),
            analysis_note=row["analysis_note"],
            review_status=row["review_status"],
            final_purchase_type=row["final_purchase_type"],
            reviewed_by=row["reviewed_by"],
            reviewed_at=_from_db(row["reviewed_at"]),
            review_note=row["review_note"],
            created_at=_from_db(row["created_at"]),
            updated_at=_from_db(row["updated_at"]),
        )

    @staticmethod
    def _row_to_history(row: sqlite3.Row) -> ReviewHistoryEntry:
        """SQLite Row 를 :class:`ReviewHistoryEntry` 로 변환합니다."""
        changed_at = _from_db(row["changed_at"])
        assert changed_at is not None  # NOT NULL 컬럼
        return ReviewHistoryEntry(
            history_id=row["history_id"],
            purchase_id=row["purchase_id"],
            action=row["action"],
            changed_at=changed_at,
            changed_by=row["changed_by"],
            before_type=row["before_type"],
            after_type=row["after_type"],
            note=row["note"],
            candidates=_candidates_from_json(row["candidates_json"]),
        )


#: 재수출 — 호출자가 상태 상수를 함께 쓰기 편하도록.
__all__ = [
    "CONFIRMED",
    "PENDING",
    "REOPENED",
    "ReviewRepository",
    "ReviewValidationError",
]
