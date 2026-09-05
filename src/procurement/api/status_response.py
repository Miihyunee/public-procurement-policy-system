"""
procurement.api.status_response

데이터 적재 현황(:class:`DataStatus`)을 **API 응답 전용 Pydantic 모델**로
변환합니다.

직렬화 규칙은 기존 :mod:`procurement.api.response` 와 동일합니다.

- ``Decimal`` → **문자열**(정밀도 보존).
- ``date`` → ``YYYY-MM-DD`` 문자열(Pydantic 기본 직렬화).

.. note::
    응답에는 **기간 필터가 적용되지 않았다는 사실**이 반드시 포함됩니다
    (``period_filter_applied`` · ``period_notice``). 연도별 집계는 D-23 ~ D-27
    확정 후 별도 Issue 에서 구현합니다. 화면이 "2026년" 을 선택해도 지금은
    전체 데이터가 표시되며, 그 사실을 응답으로 알려 화면이 오해를 부르지 않도록
    합니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer

from procurement.dashboard.status_service import DataStatus

#: 기간 판정 기준일 설정이 비어 있어 기간 조회를 쓸 수 없을 때의 문구.
#:
#: 🟢 2026-09-02 PM 확정(STEP 86) 이후로 이 문구는 **"확정되지 않았다"** 가
#: 아니라 **"설정이 비어 있다"** 는 뜻이다 — 확정된 기준일은 결의일자이며
#: 기본값도 그것이다.
PERIOD_NOTICE_UNAVAILABLE = (
    "기간 필터 사용 불가 — 연도 귀속 기준일 설정이 비어 있습니다. "
    "확정된 기준일은 결의일자입니다. 표시된 수치는 전체 데이터 기준입니다."
)

#: 기간 조회는 가능하지만 이 응답 자체는 전체 기준일 때의 문구
PERIOD_NOTICE_AVAILABLE = (
    "적재 현황은 전체 데이터 기준입니다. 달성률은 선택한 연도 기준으로 계산됩니다."
)


class DataStatusResponseModel(BaseModel):
    """데이터 적재 현황 API 응답 모델.

    Attributes:
        requested_year: 화면이 선택한 연도. 지정하지 않으면 ``null``.
            **현재는 조회 조건으로 사용되지 않고 그대로 되돌려줍니다.**
        period_filter_applied: 기간 필터 적용 여부. 현재 구현에서는 항상
            ``false`` 입니다.
        period_notice: 기간 필터 미적용 사실을 알리는 화면 표시용 문구.
        purchase_count: 적재된 구매 건수.
        purchase_total_amount: 구매금액 합계(직렬화 시 문자열).
        matched_purchase_count: 기업 매칭이 끝난 구매 건수.
        unmatched_purchase_count: 기업 매칭이 되지 않은 구매 건수.
        earliest_payment_date: 가장 이른 지급일. 데이터가 없으면 ``null``.
        latest_payment_date: 가장 늦은 지급일. 데이터가 없으면 ``null``.
        earliest_contract_date: 가장 이른 계약일. 데이터가 없으면 ``null``.
        latest_contract_date: 가장 늦은 계약일. 데이터가 없으면 ``null``.
        company_count: 적재된 기업 수.
        certification_count: 적재된 인증 건수.
        policy_count: 등록된 정책 수(비활성 포함).
        policy_with_target_rate_count: 목표율이 설정된 활성 정책 수.
        batch_count: 등록된 업로드 배치 수(대체된 배치 포함).
        active_batch_count: 계산에 사용되는 ACTIVE 배치 수.
        superseded_batch_count: 재업로드로 대체된 배치 수.
        calculation_target_count: 계산 대상 구매 건수(대체된 배치 제외).
        period_filter_available: 기간(연도) 조회를 사용할 수 있는지 여부.
            ``PURCHASE_PERIOD_DATE_FIELD`` 설정이 있어야 ``true`` 가 됩니다.
        period_date_field: 기간 판정에 사용하도록 설정된 날짜 컬럼. 미설정이면
            ``null``.
        data_mode: 현재 데이터 모드(``demo`` / ``operational``). 설정값에서 옵니다.
    """

    model_config = ConfigDict(frozen=True)

    requested_year: int | None
    period_filter_applied: bool
    period_notice: str
    purchase_count: int
    purchase_total_amount: Decimal
    matched_purchase_count: int
    unmatched_purchase_count: int
    earliest_payment_date: date | None
    latest_payment_date: date | None
    earliest_contract_date: date | None
    latest_contract_date: date | None
    company_count: int
    certification_count: int
    policy_count: int
    policy_with_target_rate_count: int
    batch_count: int
    active_batch_count: int
    superseded_batch_count: int
    calculation_target_count: int
    period_filter_available: bool
    period_date_field: str | None
    data_mode: str

    @field_serializer("purchase_total_amount", when_used="always")
    def _serialize_decimal(self, value: Decimal) -> str:
        """``Decimal`` 필드를 문자열로 직렬화합니다(python·json 모드 공통)."""
        return str(value)

    @classmethod
    def from_status(
        cls,
        status: DataStatus,
        *,
        data_mode: str,
        requested_year: int | None = None,
        period_date_field: str | None = None,
    ) -> DataStatusResponseModel:
        """:class:`DataStatus` 로부터 응답 모델을 생성합니다.

        Args:
            status: 저장소 적재 현황.
            data_mode: 현재 데이터 모드 문자열.
            requested_year: 화면이 선택한 연도(있으면 그대로 되돌려줍니다).
            period_date_field: 기간 판정에 사용하도록 설정된 날짜 컬럼.
                ``None`` 이면 기간 조회를 사용할 수 없습니다(D-24 미확정).

        Returns:
            :class:`DataStatusResponseModel`. 적재 현황 자체는 항상 전체 기준
            이므로 ``period_filter_applied`` 는 ``False`` 입니다.
        """
        available = period_date_field is not None
        return cls(
            requested_year=requested_year,
            period_filter_applied=False,
            period_notice=PERIOD_NOTICE_AVAILABLE if available else PERIOD_NOTICE_UNAVAILABLE,
            purchase_count=status.purchase_count,
            purchase_total_amount=status.purchase_total_amount,
            matched_purchase_count=status.matched_purchase_count,
            unmatched_purchase_count=status.unmatched_purchase_count,
            earliest_payment_date=status.earliest_payment_date,
            latest_payment_date=status.latest_payment_date,
            earliest_contract_date=status.earliest_contract_date,
            latest_contract_date=status.latest_contract_date,
            company_count=status.company_count,
            certification_count=status.certification_count,
            policy_count=status.policy_count,
            policy_with_target_rate_count=status.policy_with_target_rate_count,
            batch_count=status.batch_count,
            active_batch_count=status.active_batch_count,
            superseded_batch_count=status.superseded_batch_count,
            calculation_target_count=status.calculation_target_count,
            period_filter_available=available,
            period_date_field=period_date_field,
            data_mode=data_mode,
        )
