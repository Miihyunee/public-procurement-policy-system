"""
procurement.dashboard.models

대시보드 화면이 그대로 사용할 수 있는 요약 데이터 구조(DTO)와 상태 구분을
정의합니다.

- :class:`DashboardStatus` — 정책 달성 상태(정상/주의/부족) 구분
- :class:`PolicySummary` — 정책 하나에 대한 요약(구매금액·목표율·달성률·부족률·상태)
- :class:`DashboardSummary` — 대시보드 전체 요약(전체 구매액 + 정책별 요약 목록)

.. note::
    본 모듈은 순수 데이터 컨테이너/열거형이며 Repository·Calculator 에 직접
    접근하지 않습니다. 값 조합은
    :class:`procurement.dashboard.data_service.DashboardDataService` 가 담당합니다.
    UI·API·차트는 이번 범위에 포함하지 않습니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

#: 달성 상태 판정 임계값 — 이 값 이상이면 '정상'
NORMAL_THRESHOLD = Decimal("100")

#: 달성 상태 판정 임계값 — 이 값 이상 ~ NORMAL_THRESHOLD 미만이면 '주의'
WARNING_THRESHOLD = Decimal("80")


class DashboardStatus(Enum):
    """정책 달성 상태 구분.

    달성률(%)을 기준으로 세 단계로 나눕니다 (경계값 기준은 PM 확정).

    - ``NORMAL`` (정상): 달성률 >= 100
    - ``WARNING`` (주의): 80 <= 달성률 < 100
    - ``SHORTAGE`` (부족): 달성률 < 80

    위 세 가지와 별개로, 목표율이 등록되지 않아 **달성률을 계산할 수 없는**
    상태를 나타내는 값이 있습니다.

    - ``TARGET_RATE_NOT_SET`` (목표율 미설정): ``target_rate`` 가 없어 계산하지 않음
    - ``COMPANY_DATA_NOT_REGISTERED`` (조회불가): 그 정책의 **기업정보 자체가
      등록되지 않아** 해당 여부를 판단할 수 없음 (STEP 96 §8)

    두 값 모두 달성률로부터 판정되지 않으며
    (:meth:`from_achievement_rate` 는 이 값들을 반환하지 않습니다),
    **서로 다른 상태**입니다.

    ========================  ===================================================
    상태                       뜻
    ========================  ===================================================
    ``COMPANY_DATA_NOT_REGISTERED``
                              어떤 사업자가 이 정책의 기업인지 **모른다**.
                              실적을 셀 수 없다.
    ``TARGET_RATE_NOT_SET``   누가 해당하는지는 알지만, **목표가 없다**.
                              실적은 셀 수 있으나 달성률을 낼 수 없다.
    ========================  ===================================================

    .. warning::
        ⛔ **조회불가를 "미해당" 이나 0% 로 처리하지 않습니다.** 기업정보를 받지
        못한 것과 "해당 기업이 없다" 는 전혀 다른 사실입니다(STEP 96 §8 · §22-7·8).
    """

    NORMAL = "NORMAL"
    WARNING = "WARNING"
    SHORTAGE = "SHORTAGE"
    TARGET_RATE_NOT_SET = "TARGET_RATE_NOT_SET"
    COMPANY_DATA_NOT_REGISTERED = "COMPANY_DATA_NOT_REGISTERED"

    @property
    def label(self) -> str:
        """화면 표시용 한글 상태명을 반환합니다."""
        return _STATUS_LABELS[self]

    @classmethod
    def from_achievement_rate(cls, achievement_rate: Decimal) -> DashboardStatus:
        """달성률(%)로부터 상태를 판정합니다.

        Args:
            achievement_rate: 목표 대비 달성률(%).

        Returns:
            달성률 구간에 해당하는 :class:`DashboardStatus`.
        """
        if achievement_rate >= NORMAL_THRESHOLD:
            return cls.NORMAL
        if achievement_rate >= WARNING_THRESHOLD:
            return cls.WARNING
        return cls.SHORTAGE


#: 상태별 화면 표시용 한글 라벨
_STATUS_LABELS: dict[DashboardStatus, str] = {
    DashboardStatus.NORMAL: "정상",
    DashboardStatus.WARNING: "주의",
    DashboardStatus.SHORTAGE: "부족",
    DashboardStatus.TARGET_RATE_NOT_SET: "목표율 미설정",
    # ⛔ "미해당" 이 아니다. 판단할 근거 자체가 없다는 뜻이다(STEP 96 §8).
    DashboardStatus.COMPANY_DATA_NOT_REGISTERED: "기업정보 미등록",
}


@dataclass(frozen=True, kw_only=True)
class PolicySummary:
    """정책 하나에 대한 대시보드 요약(DTO).

    목표율(``target_rate``)이 등록되지 않은 정책은 달성률을 계산할 수 없으므로,
    계산 관련 값이 모두 ``None`` 이고 상태는
    :attr:`DashboardStatus.TARGET_RATE_NOT_SET` 이 됩니다. 이 경우에도 정책은
    요약에서 제외되지 않으며, 화면은 "목표율 미설정"으로 표시할 수 있습니다.

    Attributes:
        policy_id: 정책 ID.
        policy_code: 정책 코드.
        policy_name: 정책명.
        purchase_amount: 해당 정책 구매금액. 목표율 미설정이면 ``None``
            (계산을 수행하지 않았음을 의미하며 ``0`` 과 구분됩니다).
        total_purchase_amount: 기관 전체 구매액(모든 정책 요약이 공유하는 분모).
        target_rate: 목표 구매비율(%). 미설정이면 ``None``.
        achievement_rate: 목표 대비 달성률(%). 목표율 미설정이면 ``None``.
        shortage_rate: 목표 달성까지 부족한 비율(%). ``max(0, 100 - 달성률)``.
            목표율 미설정이면 ``None``.
        status: 달성 상태(정상/주의/부족/목표율 미설정/기업정보 미등록).
    """

    policy_id: int
    policy_code: str
    policy_name: str
    purchase_amount: Decimal | None
    total_purchase_amount: Decimal
    target_rate: Decimal | None
    achievement_rate: Decimal | None
    shortage_rate: Decimal | None
    status: DashboardStatus


@dataclass(frozen=True, kw_only=True)
class MissingResolutionDate:
    """**결의일자가 없어 기간 산정에서 빠진** 구매의 건수와 금액.

    .. warning::
        ⛔ **계산에 쓰이지 않습니다.** 분모·분자 어느 쪽에도 들어가지 않고,
        달성률을 바꾸지도 않습니다. 화면에 "이만큼이 기간 산정에서 빠졌습니다"
        라고 알려 주기 위한 **표시 전용** 값입니다(``DECISIONS.md`` §0.8.4).

    .. note::
        결의일자 기준으로 연도를 나눌 때만 의미가 있습니다. 지급일·계약일 기준
        조회에서는 이 행들이 빠지지 않으므로 :attr:`applies` 가 ``False`` 이며,
        화면도 표시하지 않습니다.

    Attributes:
        applies: 이 안내가 지금 조회에 해당하는지. 기간 판정 기준일이 결의일자일
            때만 ``True``.
        count: 결의일자가 없는 구매 건수.
        amount: 그 구매들의 금액 합계.
    """

    applies: bool
    count: int
    amount: Decimal


#: 해당 없음(결의일자 기준 조회가 아닐 때).
NOT_APPLICABLE = MissingResolutionDate(applies=False, count=0, amount=Decimal("0"))


@dataclass(frozen=True, kw_only=True)
class DashboardSummary:
    """대시보드 전체 요약(DTO).

    Attributes:
        total_purchase_amount: 기관 전체 구매액.
        policy_summaries: 정책별 요약 목록. 요청한 정책이 없으면 빈 목록.
        missing_resolution_date: 결의일자가 없어 기간 산정에서 빠진 건수·금액.
            ⛔ **위 두 값과 무관합니다** — 계산에 들어가지 않습니다.
    """

    total_purchase_amount: Decimal
    policy_summaries: list[PolicySummary]
    missing_resolution_date: MissingResolutionDate = NOT_APPLICABLE
