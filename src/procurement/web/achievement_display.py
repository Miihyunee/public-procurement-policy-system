"""
procurement.web.achievement_display

달성률을 **화면에 어떻게 보여줄지**만 정하는 표시 전용 구간 판정입니다.

.. danger::
    **이 모듈은 계산·판정에 관여하지 않습니다.**

    여기의 구간 값은 **법정 기준이 아니며**, 화면 UX 를 확인하기 위해 PM 이 지정한
    **임시 표시 기준**입니다. 다음에 사용해서는 안 됩니다.

    - 정책 달성 여부의 공식 판정
    - 정부 제출용 실적 판단
    - Rule Engine · 실제 목표율 계산 · 법정 의무비율 판단

기존 상태 체계와 **완전히 분리**되어 있습니다.

========================  ==========================================  =========
구분                       값                                          용도
========================  ==========================================  =========
:class:`~procurement.dashboard.models.DashboardStatus`
                          ``NORMAL`` / ``WARNING`` / ``SHORTAGE`` /
                          ``TARGET_RATE_NOT_SET``                      **계산 결과**
:class:`AchievementDisplayLevel`
                          ``LEVEL_1`` ~ ``LEVEL_6``                    **화면 표시**
========================  ==========================================  =========

두 체계는 서로 변환하거나 섞지 않습니다. 특히 기존 ``WARNING``(달성률 80~99%)과
표시 기준의 "주의"(40~59%)는 **의미가 전혀 다르므로**, 코드값을 ``LEVEL_n`` 으로
두어 라벨이 같아 보여도 혼동되지 않게 했습니다.

구간 값은 :data:`procurement.core.config.settings.DASHBOARD_ACHIEVEMENT_DISPLAY_THRESHOLDS`
로 바꿀 수 있습니다. **코드를 고치지 않고** 기준을 변경할 수 있어야 하기 때문입니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

#: 표시 구간 코드 — 낮은 쪽부터
LEVEL_CODES: tuple[str, ...] = (
    "LEVEL_1",
    "LEVEL_2",
    "LEVEL_3",
    "LEVEL_4",
    "LEVEL_5",
    "LEVEL_6",
)

#: 구간별 화면 라벨 (PM 지정 임시 표기)
LEVEL_LABELS: tuple[str, ...] = (
    "위험",
    "미달",
    "주의",
    "적정",
    "충족 임박",
    "충족",
)

#: 구간 경계 기본값(%). 각 값은 **해당 구간이 시작되는 달성률**입니다.
#: ``20 / 40 / 60 / 80 / 100`` → 0~19 / 20~39 / 40~59 / 60~79 / 80~99 / 100 이상
DEFAULT_THRESHOLDS: tuple[Decimal, ...] = (
    Decimal("20"),
    Decimal("40"),
    Decimal("60"),
    Decimal("80"),
    Decimal("100"),
)

#: 계산되지 않은 정책에 사용하는 코드. 임의로 0% 구간에 넣지 않습니다.
LEVEL_NOT_CALCULATED = "NOT_CALCULATED"

#: 계산되지 않은 정책의 화면 라벨
LABEL_NOT_CALCULATED = "미계산"


class ThresholdConfigError(ValueError):
    """표시 구간 설정값이 올바르지 않을 때 발생합니다."""


@dataclass(frozen=True, kw_only=True)
class AchievementDisplayLevel:
    """달성률 하나에 대한 표시 구간.

    Attributes:
        code: ``LEVEL_1`` ~ ``LEVEL_6`` 또는 :data:`LEVEL_NOT_CALCULATED`.
        label: 화면 라벨.
        index: 0(가장 낮음) ~ 5(가장 높음). 미계산이면 ``None``.
            색 강도를 단계적으로 주는 용도이며, 값 자체에 업무 의미는 없습니다.
    """

    code: str
    label: str
    index: int | None


#: 계산되지 않은 정책에 사용하는 표시 구간
NOT_CALCULATED = AchievementDisplayLevel(
    code=LEVEL_NOT_CALCULATED, label=LABEL_NOT_CALCULATED, index=None
)


def parse_thresholds(raw: str | None) -> tuple[Decimal, ...]:
    """설정 문자열을 구간 경계로 변환합니다.

    Args:
        raw: ``"20,40,60,80,100"`` 형태. ``None`` 이거나 비어 있으면
            :data:`DEFAULT_THRESHOLDS` 를 사용합니다.

    Returns:
        오름차순 경계값 5개.

    Raises:
        ThresholdConfigError: 개수가 5개가 아니거나, 숫자가 아니거나,
            오름차순이 아닌 경우.
    """
    if raw is None or not raw.strip():
        return DEFAULT_THRESHOLDS

    parts = [part.strip() for part in raw.split(",") if part.strip()]
    expected = len(LEVEL_CODES) - 1
    if len(parts) != expected:
        raise ThresholdConfigError(
            f"표시 구간 경계는 {expected} 개여야 합니다: {raw!r} ({len(parts)} 개)"
        )

    try:
        values = tuple(Decimal(part) for part in parts)
    except InvalidOperation as exc:
        raise ThresholdConfigError(f"숫자가 아닌 값이 있습니다: {raw!r}") from exc

    for lower, upper in zip(values, values[1:], strict=False):
        if lower >= upper:
            raise ThresholdConfigError(f"경계값은 오름차순이어야 합니다: {raw!r}")
    return values


def resolve_level(
    achievement_rate: Decimal | None, thresholds: tuple[Decimal, ...] = DEFAULT_THRESHOLDS
) -> AchievementDisplayLevel:
    """달성률을 표시 구간으로 변환합니다.

    Args:
        achievement_rate: 목표 대비 달성률(%). 계산되지 않았으면 ``None``.
        thresholds: 구간 경계. 생략하면 기본값을 사용합니다.

    Returns:
        :class:`AchievementDisplayLevel`. ``achievement_rate`` 가 ``None`` 이면
        :data:`NOT_CALCULATED` — **0% 구간으로 내려보내지 않습니다.**
    """
    if achievement_rate is None:
        return NOT_CALCULATED

    index = 0
    for boundary in thresholds:
        if achievement_rate >= boundary:
            index += 1
        else:
            break

    return AchievementDisplayLevel(
        code=LEVEL_CODES[index], label=LEVEL_LABELS[index], index=index
    )


def describe_levels(thresholds: tuple[Decimal, ...] = DEFAULT_THRESHOLDS) -> list[dict[str, str]]:
    """구간표를 화면에 내려줄 형태로 만듭니다.

    화면이 경계값을 하드코딩하지 않도록 서버가 표를 제공합니다.

    Args:
        thresholds: 구간 경계.

    Returns:
        ``code`` · ``label`` · ``min_rate`` · ``max_rate`` 를 담은 목록.
        마지막 구간의 ``max_rate`` 는 빈 문자열(상한 없음)입니다.
    """
    bounds = [Decimal("0"), *thresholds]
    items: list[dict[str, str]] = []
    for index, code in enumerate(LEVEL_CODES):
        upper = "" if index == len(LEVEL_CODES) - 1 else str(bounds[index + 1] - 1)
        items.append(
            {
                "code": code,
                "label": LEVEL_LABELS[index],
                "min_rate": str(bounds[index]),
                "max_rate": upper,
            }
        )
    return items
