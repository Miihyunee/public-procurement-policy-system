"""
procurement.importers.rejection_query

미적재 행 목록의 **검색 · 필터 · 정렬 · 페이지** 조건.

.. warning::
    ⛔ **조회 조건일 뿐입니다.**

    미적재 행을 걸러 본다고 해서 그 행이 실적에 포함되거나 빠지는 것이
    아닙니다. 여기에는 어떤 업무 판단도 없습니다 — 담당자가 130건 중 원하는
    것을 찾아보기 위한 수단입니다(``CUSTOMER_DATA_QUESTIONS.md`` Q5-8 은 확인
    대기).

.. note::
    조건 이름과 검증 방식은 검토 목록(:mod:`procurement.reviews.query`)과
    같은 규약을 따릅니다. 담당자가 두 화면을 같은 방식으로 쓰게 하기 위해서
    입니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from procurement.models.import_rejection import REJECTION_REASONS

#: 전체.
ANY: Final = "ALL"

#: 정렬 기준으로 고를 수 있는 값.
SORT_KEYS: Final[tuple[str, ...]] = (
    "row_number",
    "amount",
    "description",
    "company_name",
    "reason",
)

#: 오름차순 · 내림차순.
ASCENDING: Final = "asc"
DESCENDING: Final = "desc"
SORT_DIRECTIONS: Final[frozenset[str]] = frozenset({ASCENDING, DESCENDING})

#: 한 페이지 기본 건수. ⚠️ 화면 성능을 위한 값이며 업무규칙이 아닙니다.
DEFAULT_PAGE_SIZE: Final = 50

#: 한 번에 요청할 수 있는 최대 건수.
MAX_PAGE_SIZE: Final = 500


class RejectionQueryError(ValueError):
    """조건 값이 허용 범위를 벗어났을 때 발생합니다."""


@dataclass(frozen=True, kw_only=True)
class RejectionQuery:
    """미적재 행 조회 조건.

    Attributes:
        search: 적요 · 거래처명 · 원본 행 번호에 대한 부분 문자열. 빈 값이면
            전체. 띄어쓰기 차이는 무시합니다.
        reason: :data:`~procurement.models.import_rejection.REJECTION_REASONS`
            중 하나, 또는 :data:`ANY`.
        sort: :data:`SORT_KEYS` 중 하나.
        direction: :data:`ASCENDING` 또는 :data:`DESCENDING`.
        page: 1부터 시작하는 페이지 번호.
        page_size: 한 페이지 건수.
    """

    search: str = ""
    reason: str = ANY
    sort: str = "row_number"
    direction: str = ASCENDING
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        """허용값을 벗어나면 즉시 거부합니다.

        조용히 기본값으로 되돌리면 담당자가 **자기가 고른 조건과 다른 목록**을
        보게 됩니다.

        Raises:
            RejectionQueryError: 허용되지 않는 값인 경우.
        """
        if self.reason != ANY and self.reason not in REJECTION_REASONS:
            raise RejectionQueryError(f"허용되지 않는 사유 필터입니다: {self.reason}")
        if self.sort not in SORT_KEYS:
            raise RejectionQueryError(f"허용되지 않는 정렬 기준입니다: {self.sort}")
        if self.direction not in SORT_DIRECTIONS:
            raise RejectionQueryError(f"허용되지 않는 정렬 방향입니다: {self.direction}")
        if self.page < 1:
            raise RejectionQueryError(f"페이지는 1 이상이어야 합니다: {self.page}")
        if not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise RejectionQueryError(
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
