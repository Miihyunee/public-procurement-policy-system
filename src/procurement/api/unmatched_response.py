"""
procurement.api.unmatched_response

미매칭 기업 집계(:class:`UnmatchedPage`)를 **API 응답 전용 Pydantic 모델**로
변환합니다.

직렬화 규칙은 기존 :mod:`procurement.api.status_response` 와 동일합니다.

- ``Decimal`` → **문자열**(정밀도 보존).

.. note::
    응답에는 **모집단이 무엇인지**가 함께 담깁니다(``includes_superseded``).
    이 조회는 :meth:`PurchaseRepository.find_unmatched` 와 같은 조건이라 대체된
    배치의 행도 포함하며, 계산 대상(``calculation_target_count``)과 모집단이
    다릅니다. 그 사실을 응답으로 알려 화면이 오해를 부르지 않도록 합니다.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer

from procurement.dashboard.unmatched_service import UnmatchedCompany, UnmatchedPage


class UnmatchedCompanyResponseModel(BaseModel):
    """사업자등록번호 하나의 미매칭 집계.

    Attributes:
        business_no: 사업자등록번호. 저장된 값 그대로입니다.
        company_names: 이 사업자번호로 들어온 거래처명들. 표기가 여러 가지일 수
            있어 **전부** 담습니다.
        purchase_count: 미매칭 구매 건수.
        total_amount: 미매칭 구매금액 합계(직렬화 시 문자열).
        amount_share: 전체 미매칭 금액 대비 비중(%, 직렬화 시 문자열).
    """

    model_config = ConfigDict(frozen=True)

    business_no: str
    company_names: list[str]
    purchase_count: int
    total_amount: Decimal
    amount_share: Decimal

    @field_serializer("total_amount", "amount_share", when_used="always")
    def _serialize_decimal(self, value: Decimal) -> str:
        """``Decimal`` 필드를 문자열로 직렬화합니다(python·json 모드 공통)."""
        return str(value)

    @classmethod
    def from_row(cls, row: UnmatchedCompany) -> UnmatchedCompanyResponseModel:
        """집계 한 줄로부터 응답 모델을 생성합니다."""
        return cls(
            business_no=row.business_no,
            company_names=list(row.company_names),
            purchase_count=row.purchase_count,
            total_amount=row.total_amount,
            amount_share=row.amount_share,
        )


class UnmatchedPageResponseModel(BaseModel):
    """미매칭 기업 조회 한 페이지.

    Attributes:
        items: 이 페이지의 집계 행.
        total: 조건에 맞는 사업자번호 수.
        page: 현재 페이지.
        page_size: 한 페이지 건수.
        unmatched_purchase_count: 조건과 무관한 전체 미매칭 구매 건수.
        unmatched_total_amount: 조건과 무관한 전체 미매칭 구매금액(문자열).
        unmatched_business_no_count: 조건과 무관한 전체 미매칭 사업자번호 수.
        includes_superseded: 대체된 배치의 행이 모집단에 포함되어 있는지.
        notice: 모집단을 설명하는 화면 표시용 문구.
    """

    model_config = ConfigDict(frozen=True)

    items: list[UnmatchedCompanyResponseModel]
    total: int
    page: int
    page_size: int
    unmatched_purchase_count: int
    unmatched_total_amount: Decimal
    unmatched_business_no_count: int
    includes_superseded: bool
    notice: str

    @field_serializer("unmatched_total_amount", when_used="always")
    def _serialize_decimal(self, value: Decimal) -> str:
        """``Decimal`` 필드를 문자열로 직렬화합니다."""
        return str(value)

    @classmethod
    def from_page(cls, page: UnmatchedPage) -> UnmatchedPageResponseModel:
        """:class:`UnmatchedPage` 로부터 응답 모델을 생성합니다."""
        return cls(
            items=[UnmatchedCompanyResponseModel.from_row(row) for row in page.items],
            total=page.total,
            page=page.page,
            page_size=page.page_size,
            unmatched_purchase_count=page.unmatched_purchase_count,
            unmatched_total_amount=page.unmatched_total_amount,
            unmatched_business_no_count=page.unmatched_business_no_count,
            includes_superseded=page.includes_superseded,
            notice=_notice(page),
        )


#: 대체된 배치가 없을 때의 문구.
NOTICE_PLAIN = (
    "기업정보가 등록되지 않아 연결되지 않은 구매입니다. 기업정보를 등록한 뒤 재매칭하면 연결됩니다."
)

#: 대체된 배치의 행이 섞여 있을 때 덧붙이는 문구.
#:
#: ⛔ 업무 판단이 아니라 **모집단 설명**입니다. 어느 쪽이 옳은지 말하지 않습니다.
NOTICE_SUPERSEDED = (
    "기업정보가 등록되지 않아 연결되지 않은 구매입니다. "
    "기업정보를 등록한 뒤 재매칭하면 연결됩니다. "
    "⚠️ 이 집계는 대시보드의 '기업 미매칭' 총계와 같은 기준이라 "
    "재업로드로 대체된 배치의 행도 포함합니다(계산 대상 건수와 다를 수 있습니다)."
)


def _notice(page: UnmatchedPage) -> str:
    """모집단을 설명하는 문구를 고릅니다."""
    return NOTICE_SUPERSEDED if page.includes_superseded else NOTICE_PLAIN
