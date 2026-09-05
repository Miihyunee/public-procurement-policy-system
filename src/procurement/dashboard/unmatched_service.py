"""
procurement.dashboard.unmatched_service

**기업정보가 없어 매칭되지 않은 구매**를 사업자등록번호별로 묶어 보여주는
조회 서비스입니다.

대시보드는 지금까지 "기업 미매칭 N건" 이라는 **총계**만 보여 주었습니다. 그
숫자만으로는 담당자가 **어느 기업정보를 먼저 확보해야 하는지** 알 수 없습니다.
이 서비스는 같은 사실을 사업자번호 단위로 접어, 금액이 큰 순서를 보여 줍니다::

    UnmatchedCompanyService → PurchaseRepository.find_unmatched() → SQLite

.. warning::
    ⛔ **읽기 전용입니다.** 기업·인증·구매 어느 것도 만들거나 바꾸지 않습니다.
    Repository 의 조회 메서드 하나만 쓰며, 쓰기 메서드를 주입받지 않습니다.

.. warning::
    ⛔ **업무규칙을 만들지 않습니다.** "이 사업자번호는 확보 대상" 같은 판정을
    하지 않고, 집계된 사실만 돌려줍니다. 정렬·페이지는 화면 편의이며 업무적
    우선순위가 아닙니다.

.. note::
    **조회 조건은 ``PurchaseRepository.find_unmatched()`` 와 완전히 같습니다** —
    ``company_id IS NULL`` 인 구매 전체입니다.
    :class:`~procurement.matchers.company_matcher.CompanyMatcher` 가 실제로
    연결을 시도하는 대상, 그리고 대시보드의 ``unmatched_purchase_count`` 와
    **같은 모집단**이어야 화면의 숫자가 서로 맞습니다.

    ⚠️ 따라서 **대체된(SUPERSEDED) 배치의 행도 포함**됩니다. 계산 대상
    (``find_for_review``)과 모집단이 다르다는 사실은 응답의
    ``includes_superseded`` 로 화면에 그대로 알립니다. 어느 쪽이 옳은지는
    업무 판단이므로 여기서 정하지 않습니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from procurement.database.purchase_repository import PurchaseRepository
from procurement.matchers.business_no import business_no_search_key

#: 정렬 기준 — 미매칭 구매금액 합계.
SORT_AMOUNT: Final = "amount"

#: 정렬 기준 — 미매칭 구매 건수.
SORT_COUNT: Final = "count"

#: 정렬 기준 — 사업자등록번호.
SORT_BUSINESS_NO: Final = "business_no"

#: 고를 수 있는 정렬 기준.
SORT_KEYS: Final[tuple[str, ...]] = (SORT_AMOUNT, SORT_COUNT, SORT_BUSINESS_NO)

#: 오름차순 · 내림차순.
ASCENDING: Final = "asc"
DESCENDING: Final = "desc"
SORT_DIRECTIONS: Final[frozenset[str]] = frozenset({ASCENDING, DESCENDING})

#: 한 페이지 기본 건수. ⚠️ 화면 성능을 위한 값이며 업무규칙이 아닙니다.
DEFAULT_PAGE_SIZE: Final = 50

#: 한 번에 요청할 수 있는 최대 건수.
MAX_PAGE_SIZE: Final = 500


class UnmatchedQueryError(ValueError):
    """조회 조건이 허용 범위를 벗어났을 때 발생합니다."""


@dataclass(frozen=True, kw_only=True)
class UnmatchedQuery:
    """미매칭 기업 조회 조건.

    Attributes:
        search: 사업자등록번호 · 거래처명 부분 문자열. 빈 값이면 전체.
        sort: :data:`SORT_KEYS` 중 하나.
        direction: :data:`ASCENDING` 또는 :data:`DESCENDING`.
        page: 1부터 시작하는 페이지 번호.
        page_size: 한 페이지 건수.
    """

    search: str = ""
    sort: str = SORT_AMOUNT
    direction: str = DESCENDING
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        """허용 범위를 벗어난 조건을 즉시 거부합니다."""
        if self.sort not in SORT_KEYS:
            allowed = " · ".join(SORT_KEYS)
            raise UnmatchedQueryError(
                f"정렬 기준이 올바르지 않습니다: {self.sort!r} (허용: {allowed})"
            )
        if self.direction not in SORT_DIRECTIONS:
            raise UnmatchedQueryError(f"정렬 방향이 올바르지 않습니다: {self.direction!r}")
        if self.page < 1:
            raise UnmatchedQueryError(f"페이지 번호는 1 이상이어야 합니다: {self.page}")
        if not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise UnmatchedQueryError(
                f"한 페이지 건수는 1 이상 {MAX_PAGE_SIZE} 이하여야 합니다: {self.page_size}"
            )

    @property
    def offset(self) -> int:
        """건너뛸 행 수."""
        return (self.page - 1) * self.page_size


@dataclass(frozen=True, kw_only=True)
class UnmatchedCompany:
    """사업자등록번호 하나에 대한 미매칭 집계.

    Attributes:
        business_no: 사업자등록번호. 구매 데이터에 저장된 값 그대로입니다.
        company_names: 이 사업자번호로 들어온 거래처명들. 같은 번호에 표기가
            여러 가지일 수 있어 **전부** 담습니다(하나를 골라 대표로 삼지
            않습니다). 처음 나온 순서를 유지합니다.
        purchase_count: 미매칭 구매 건수.
        total_amount: 미매칭 구매금액 합계.
        amount_share: 전체 미매칭 금액에서 이 사업자번호가 차지하는 비중(%).
            전체가 0 이면 ``0``.
    """

    business_no: str
    company_names: tuple[str, ...]
    purchase_count: int
    total_amount: Decimal
    amount_share: Decimal


@dataclass(frozen=True, kw_only=True)
class UnmatchedPage:
    """미매칭 기업 조회 한 페이지.

    Attributes:
        items: 이 페이지의 집계 행.
        total: 조건에 맞는 사업자번호 수.
        page: 현재 페이지.
        page_size: 한 페이지 건수.
        unmatched_purchase_count: 조건과 무관한 **전체** 미매칭 구매 건수.
        unmatched_total_amount: 조건과 무관한 **전체** 미매칭 구매금액.
        unmatched_business_no_count: 조건과 무관한 **전체** 미매칭 사업자번호 수.
        includes_superseded: 대체된 배치의 행이 모집단에 포함되어 있는지.
            ``True`` 면 화면이 그 사실을 알려야 합니다.
    """

    items: tuple[UnmatchedCompany, ...]
    total: int
    page: int
    page_size: int
    unmatched_purchase_count: int
    unmatched_total_amount: Decimal
    unmatched_business_no_count: int
    includes_superseded: bool


#: 비중 표기 자리수(소수점 둘째 자리). Calculator 의 달성률 표기와 같습니다.
_SHARE_EXPONENT: Final = Decimal("0.01")
_PERCENT: Final = Decimal("100")


class UnmatchedCompanyService:
    """미매칭 구매를 사업자등록번호별로 집계합니다."""

    def __init__(self, purchase_repository: PurchaseRepository) -> None:
        """서비스를 초기화합니다.

        Args:
            purchase_repository: 구매 조회용. ⛔ **쓰기에 사용하지 않습니다.**
        """
        self._purchase_repository = purchase_repository

    def search(self, query: UnmatchedQuery) -> UnmatchedPage:
        """조건에 맞는 집계를 한 페이지 돌려줍니다.

        Args:
            query: 조회 조건.

        Returns:
            :class:`UnmatchedPage`. 미매칭이 없으면 빈 페이지입니다.
        """
        unmatched = self._purchase_repository.find_unmatched()

        # 사업자번호별로 접는다. dict 는 삽입 순서를 유지하므로, 같은 번호의
        # 거래처명 표기도 처음 나온 순서 그대로 남는다.
        counts: dict[str, int] = {}
        amounts: dict[str, Decimal] = {}
        names: dict[str, list[str]] = {}
        for purchase in unmatched:
            key = purchase.business_no
            counts[key] = counts.get(key, 0) + 1
            amounts[key] = amounts.get(key, Decimal("0")) + purchase.amount
            bucket = names.setdefault(key, [])
            if purchase.company_name not in bucket:
                bucket.append(purchase.company_name)

        whole_amount = sum(amounts.values(), Decimal("0"))
        rows = [
            UnmatchedCompany(
                business_no=business_no,
                company_names=tuple(names[business_no]),
                purchase_count=counts[business_no],
                total_amount=amounts[business_no],
                amount_share=self._share(amounts[business_no], whole_amount),
            )
            for business_no in counts
        ]

        kept = [row for row in rows if _keeps(row, query.search)]
        ordered = _sorted(kept, query.sort, descending=query.direction == DESCENDING)
        window = ordered[query.offset : query.offset + query.page_size]

        return UnmatchedPage(
            items=tuple(window),
            total=len(kept),
            page=query.page,
            page_size=query.page_size,
            unmatched_purchase_count=len(unmatched),
            unmatched_total_amount=whole_amount,
            unmatched_business_no_count=len(rows),
            includes_superseded=self._includes_superseded(),
        )

    @staticmethod
    def _share(amount: Decimal, whole: Decimal) -> Decimal:
        """전체 미매칭 금액 대비 비중(%)."""
        if whole == 0:
            return Decimal("0")
        return (amount / whole * _PERCENT).quantize(_SHARE_EXPONENT)

    def _includes_superseded(self) -> bool:
        """모집단에 대체된 배치의 행이 섞여 있는지.

        ⛔ 걸러 내지 않습니다. ``find_unmatched()`` 의 조건을 그대로 쓰는 것이
        :class:`CompanyMatcher` · 대시보드 총계와 숫자를 맞추는 유일한 방법이며,
        어느 모집단이 옳은지는 업무 판단입니다. 여기서는 **사실만 알립니다.**
        """
        every = len(self._purchase_repository.find_all())
        return every > len(self._purchase_repository.find_for_review())


def _keeps(row: UnmatchedCompany, search: str) -> bool:
    """사업자번호 또는 거래처명에 검색어가 들어 있는가."""
    needle = search.strip()
    if not needle:
        return True
    if needle in row.business_no:
        return True
    # 하이픈이 있는 표기로 넣어도 찾을 수 있어야 한다 — 검토 화면 검색과 같은
    # 이유다(STEP 73 검수에서 발견).
    number = business_no_search_key(needle)
    if number and number in business_no_search_key(row.business_no):
        return True
    return any(needle in name for name in row.company_names)


def _sorted(rows: list[UnmatchedCompany], key: str, *, descending: bool) -> list[UnmatchedCompany]:
    """정렬합니다. 값이 같으면 사업자번호로 갈라 **항상 같은 순서**가 되게 합니다."""
    if key == SORT_AMOUNT:
        ordered = sorted(rows, key=lambda row: (row.total_amount, row.business_no))
    elif key == SORT_COUNT:
        ordered = sorted(rows, key=lambda row: (row.purchase_count, row.business_no))
    else:
        ordered = sorted(rows, key=lambda row: row.business_no)
    return list(reversed(ordered)) if descending else ordered
