"""
procurement.reviews.query

검토 목록의 **검색 · 필터 · 정렬 · 페이지** 조건.

.. warning::
    ⛔ **우선순위를 정하지 않습니다.**

    "이 건을 먼저 보라" 고 시스템이 결정하지 않습니다. 담당자가 **직접 조건을
    고르는** 것이며, 이 모듈은 그 조건을 담을 뿐입니다.

    ``score_gap`` 이나 ``dominant_ratio`` 로 정렬할 수 있지만, 그 값으로
    "위험도" · "검토 대상" 같은 새 개념을 만들지 않았습니다.

.. warning::
    ⛔ **자동 확정 기준이 없습니다.** 어떤 필터도 값을 바꾸지 않으며, 목록을
    좁혀 보여줄 뿐입니다.

.. note::
    **왜 서버에서 거르는가.** 조건에 맞는 **한 페이지만** 내려보내기 위해서
    입니다. 전체를 브라우저로 내려받아 거르면 건수가 늘수록 첫 화면이 느려지고
    메모리도 커집니다(``REVIEW_INTERFACE_DESIGN.md`` §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from procurement.models.review import CONFIRMED, PENDING, REOPENED

# ----------------------------------------------------------------------
# 상태 필터 — ⛔ 새 상태를 만들지 않고 기존 정의를 그대로 씁니다.
# ----------------------------------------------------------------------
#: 전체.
ANY: Final = "ALL"

#: 검토 상태 필터로 쓸 수 있는 값.
STATUS_FILTERS: Final[frozenset[str]] = frozenset({ANY, PENDING, CONFIRMED, REOPENED})

# ----------------------------------------------------------------------
# 확정 여부
# ----------------------------------------------------------------------
#: 담당자가 확정한 건.
DECIDED: Final = "DECIDED"

#: 아직 확정하지 않은 건(재검토 포함).
UNDECIDED: Final = "UNDECIDED"

#: 확정 여부 필터.
DECISION_FILTERS: Final[frozenset[str]] = frozenset({ANY, DECIDED, UNDECIDED})

# ----------------------------------------------------------------------
# 과거 확정 이력
# ----------------------------------------------------------------------
#: 과거 확정 이력이 있는 건.
HAS_HISTORY: Final = "HAS_HISTORY"

#: 과거 확정 이력이 없는 건(처음 보는 적요).
NO_HISTORY_ONLY: Final = "NO_HISTORY"

#: 과거에 여러 유형으로 갈렸던 건.
HISTORY_MIXED: Final = "MIXED"

#: 1순위 후보가 과거 최다 유형과 **같은** 건.
HISTORY_AGREES: Final = "AGREES"

#: 1순위 후보가 과거 최다 유형과 **다른** 건.
HISTORY_DIFFERS: Final = "DIFFERS"

#: 과거 이력 필터.
HISTORY_FILTERS: Final[frozenset[str]] = frozenset(
    {ANY, HAS_HISTORY, NO_HISTORY_ONLY, HISTORY_MIXED, HISTORY_AGREES, HISTORY_DIFFERS}
)

# ----------------------------------------------------------------------
# 후보 수
# ----------------------------------------------------------------------
#: 후보가 없는 건.
NO_CANDIDATE: Final = "NONE"

#: 후보가 정확히 1개인 건.
ONE_CANDIDATE: Final = "ONE"

#: 후보가 2개 이상인 건.
MANY_CANDIDATES: Final = "MANY"

#: 후보 수 필터.
CANDIDATE_FILTERS: Final[frozenset[str]] = frozenset(
    {ANY, NO_CANDIDATE, ONE_CANDIDATE, MANY_CANDIDATES}
)

# ----------------------------------------------------------------------
# 정렬
# ----------------------------------------------------------------------
#: 정렬 기준으로 고를 수 있는 값.
#:
#: ⚠️ ``score_gap`` · ``dominant_ratio`` 로 **줄 세울 수는** 있으나, 그것이
#: "먼저 봐야 할 순서" 라는 뜻은 아닙니다. 순서를 고르는 것은 담당자입니다.
SORT_KEYS: Final[tuple[str, ...]] = (
    "purchase_id",
    "resolution_date",
    "issue_date",
    "amount",
    "description",
    "status",
    "candidate_count",
    "score_gap",
    "has_history",
    "dominant_ratio",
)

#: 오름차순.
ASCENDING: Final = "asc"

#: 내림차순.
DESCENDING: Final = "desc"

#: 정렬 방향.
SORT_DIRECTIONS: Final[frozenset[str]] = frozenset({ASCENDING, DESCENDING})

# ----------------------------------------------------------------------
# 페이지
# ----------------------------------------------------------------------
#: 한 페이지 기본 건수.
#:
#: ⚠️ **업무규칙이 아니라 화면 성능을 위한 값**입니다. 카드 하나가 세로로
#: 길어 한 화면에 몇 장 이상은 어차피 보이지 않습니다.
DEFAULT_PAGE_SIZE: Final = 20

#: 한 번에 요청할 수 있는 최대 건수. 실수로 전체를 끌어오는 것을 막습니다.
MAX_PAGE_SIZE: Final = 200


class ReviewQueryError(ValueError):
    """조건 값이 허용 범위를 벗어났을 때 발생합니다."""


@dataclass(frozen=True, kw_only=True)
class ReviewQuery:
    """검토 목록 조회 조건.

    Attributes:
        search: 적요 부분 문자열. 빈 값이면 전체. 띄어쓰기 차이는 무시합니다.
        status: :data:`STATUS_FILTERS` 중 하나.
        decision: :data:`DECISION_FILTERS` 중 하나.
        history: :data:`HISTORY_FILTERS` 중 하나.
        candidates: :data:`CANDIDATE_FILTERS` 중 하나.
        batch_id: 이 업로드 배치로 들어온 행만. ``None`` 이면 제한 없음.

            ⚠️ **화면이 만들어 내는 값이 아닙니다.** 담당자가 기간을 고르면
            백엔드가 알려 준 그 기간의 **현재 배치 ID** 를 그대로 보냅니다
            (``GET /imports/periods``). 대체된 배치는 애초에 목록에 없으므로
            여기로 들어오지 않습니다.
        ambiguous_only: 분석기가 애매하다고 표시한 건만.
        sort: :data:`SORT_KEYS` 중 하나.
        direction: :data:`ASCENDING` 또는 :data:`DESCENDING`.
        page: 1부터 시작하는 페이지 번호.
        page_size: 한 페이지 건수.
    """

    search: str = ""
    status: str = ANY
    decision: str = ANY
    history: str = ANY
    candidates: str = ANY
    batch_id: int | None = None
    ambiguous_only: bool = False
    sort: str = "purchase_id"
    direction: str = ASCENDING
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        """허용값을 벗어나면 즉시 거부합니다.

        조용히 기본값으로 되돌리면 담당자가 **자기가 고른 조건과 다른 목록**을
        보게 됩니다.

        Raises:
            ReviewQueryError: 허용되지 않는 값인 경우.
        """
        _require(self.status, STATUS_FILTERS, "상태 필터")
        _require(self.decision, DECISION_FILTERS, "확정 여부 필터")
        _require(self.history, HISTORY_FILTERS, "과거 이력 필터")
        _require(self.candidates, CANDIDATE_FILTERS, "후보 수 필터")
        _require(self.sort, frozenset(SORT_KEYS), "정렬 기준")
        _require(self.direction, SORT_DIRECTIONS, "정렬 방향")

        if self.batch_id is not None and self.batch_id < 1:
            raise ReviewQueryError(f"배치 ID 는 1 이상이어야 합니다: {self.batch_id}")
        if self.page < 1:
            raise ReviewQueryError(f"페이지는 1 이상이어야 합니다: {self.page}")
        if not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise ReviewQueryError(
                f"페이지 크기는 1~{MAX_PAGE_SIZE} 이어야 합니다: {self.page_size}"
            )

    @property
    def descending(self) -> bool:
        """내림차순인가."""
        return self.direction == DESCENDING

    @property
    def offset(self) -> int:
        """건너뛸 건수."""
        return (self.page - 1) * self.page_size


def _require(value: str, allowed: frozenset[str], label: str) -> None:
    """허용 목록에 없으면 예외를 냅니다."""
    if value not in allowed:
        options = " · ".join(sorted(allowed))
        raise ReviewQueryError(f"허용되지 않는 {label}입니다: {value!r} (허용: {options})")


@dataclass(frozen=True, kw_only=True)
class PageInfo:
    """페이지 상태.

    Attributes:
        page: 현재 페이지(1부터).
        page_size: 한 페이지 건수.
        total: 조건에 맞는 **전체** 건수(페이지 밖 포함).
    """

    page: int
    page_size: int
    total: int

    @property
    def total_pages(self) -> int:
        """전체 페이지 수. 결과가 0건이면 1(빈 첫 페이지)."""
        if self.total <= 0:
            return 1
        return -(-self.total // self.page_size)  # 올림 나눗셈

    @property
    def has_previous(self) -> bool:
        """이전 페이지가 있는가."""
        return self.page > 1

    @property
    def has_next(self) -> bool:
        """다음 페이지가 있는가."""
        return self.page < self.total_pages


#: 정렬 시 값이 없는 항목을 **항상 뒤로** 보내기 위한 표식.
#:
#: 오름차순이든 내림차순이든 "값 없음" 이 맨 뒤에 오게 합니다. 값이 있는
#: 것부터 보는 편이 담당자에게 자연스럽고, 방향을 바꿨다고 빈 값이 앞으로
#: 몰려오면 혼란스럽기 때문입니다.
MISSING_LAST: Final = 1

#: 값이 있는 항목.
PRESENT_FIRST: Final = 0


def sort_bucket(value: object | None) -> int:
    """값이 있으면 :data:`PRESENT_FIRST`, 없으면 :data:`MISSING_LAST`."""
    return PRESENT_FIRST if value is not None else MISSING_LAST


def missing_decimal() -> Decimal:
    """값이 없을 때 비교에 쓸 대체값. 실제 정렬 위치는 버킷이 정합니다."""
    return Decimal("0")
