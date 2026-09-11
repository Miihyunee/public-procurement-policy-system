"""
procurement.core.description_hints

적요에 **고객이 말한 낱말이 들어 있는지** 를 찾아 담당자에게 보여줄 **참고
근거**를 만듭니다.

.. warning::
    🔴 **구매유형을 판정하지 않습니다.**

    이 모듈이 돌려주는 것은 "이 낱말이 적요에 들어 있다" 는 **관찰 사실**뿐
    입니다. 어떤 유형인지 말하지 않고, 점수·순위·추천도 만들지 않습니다.
    확정은 담당자만 합니다.

    ⛔ 다음과 같은 코드를 **여기에도, 이것을 쓰는 어디에도 만들지 않습니다.**

    .. code-block:: python

        if "공사" in description:     # ⛔ 금지
            purchase_type = "공사"

.. warning::
    ⛔ **낱말 사이에 우선순위를 만들지 않습니다.**

    한 적요에 여러 낱말이 들어 있으면 **전부** 돌려줍니다. 어느 것이 이기는지
    정하지 않습니다. 실데이터에 `나무심기 조성공사 공사 및 용역 준공금` 처럼
    공사 낱말과 용역 낱말이 함께 든 적요가 실재합니다.

.. warning::
    ⛔ **낱말이 유형을 뜻하지 않습니다.** 확정 1,744건 실측에서 이렇게 갈립니다.

    ==================== ====================================================
    ``용역 준공금``        4건 중 **2건이 공사** — 용역이라고 볼 수 없습니다
    ``기념품``            36건 중 **9건이 용역**(`홍보 기념품 구입`)
    ``수수료``            119건 중 **7건이 물품**(소프트웨어 라이선스 구매)
    ==================== ====================================================

    고객도 *"수수료는 일반적으로 용역으로 보지만 100% 용역은 아니다"* 라고
    답했습니다. 경계 사례의 처리 방식은 **아직 확인 전**입니다.

.. note::
    **낱말 목록은 고객이 직접 말한 것뿐입니다**(2026-08-25 회신). 우리가 추론해
    넓히지 않습니다. 아래 세 묶음으로 나눠 둔 것은 **고객이 어느 맥락에서
    말했는지를 사람이 알아보기 위한 것**이며, 묶음 이름은 **밖으로 나가지
    않습니다** — 나가는 순간 판정으로 읽히기 때문입니다.

.. note::
    적요 비교는 :func:`~procurement.core.description_key.normalize_description`
    을 **그대로 재사용**합니다. 새 정규화 규칙을 만들지 않습니다.
    실측상 이 편이 고객 표현을 덜 놓칩니다 — 원문 비교로는 `26년 6월 철거 공사
    용역비`(띄어 쓴 `철거 공사`)와 `녹색생활실천학교 용역선금`(붙여 쓴
    `용역선금`) 3건을 놓쳤습니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from procurement.core.description_key import normalize_description

#: 🟢 고객이 **외주용역비에서 공사를 가릴 때 본다**고 말한 낱말 (2026-08-25).
#:
#: ⛔ 이 낱말이 있다고 공사인 것이 아닙니다 — 담당자가 보는 단서일 뿐입니다.
_MENTIONED_FOR_CONSTRUCTION: Final[tuple[str, ...]] = (
    "하도급",
    "노무비",
    "조성공사",
    "철거공사",
    "민원공사",
    "공사",
)

#: 🟢 고객이 **용역 성격의 예**로 든 낱말 (2026-08-25).
_MENTIONED_FOR_SERVICE: Final[tuple[str, ...]] = (
    "용역 선금",
    "용역 완수금",
    "용역 준공금",
    "측량",
    "자문",
    "수수료",
    "용역",
)

#: 🟢 고객이 **물품 성격의 예**로 든 낱말 (2026-08-25).
_MENTIONED_FOR_GOODS: Final[tuple[str, ...]] = (
    "현수막",
    "인쇄",
    "책",
    "소모성",
    "기념품",
    "피복",
)

#: 찾아볼 낱말 전체. 고객이 말한 순서를 그대로 이어 붙입니다.
#:
#: ⚠️ 순서는 **표시 순서**일 뿐이며 우선순위가 아닙니다.
HINT_KEYWORDS: Final[tuple[str, ...]] = (
    *_MENTIONED_FOR_CONSTRUCTION,
    *_MENTIONED_FOR_SERVICE,
    *_MENTIONED_FOR_GOODS,
)


@dataclass(frozen=True, kw_only=True)
class DescriptionHint:
    """적요에서 발견된 낱말 하나.

    ⛔ **유형·점수·순위 필드가 의도적으로 없습니다.** 타입 수준에서 "이것은
    판정이 아니다" 를 보장하기 위해서입니다.
    :class:`~procurement.models.classification.TypeCandidate` 를 쓰지 않은
    이유도 같습니다 — 그쪽은 ``purchase_type`` 과 ``score`` 를 **반드시**
    요구하므로, 쓰는 순간 유형과 점수를 지어내야 합니다.

    Attributes:
        keyword: 발견된 낱말. 고객이 말한 표기 그대로입니다.
        text: 화면에 그대로 쓸 수 있는 문장.
    """

    keyword: str
    text: str


def find_hints(description: str | None) -> tuple[DescriptionHint, ...]:
    """적요에 고객이 말한 낱말이 들어 있는지 찾습니다.

    ⛔ 유형을 판정하지 않습니다. 발견된 낱말을 **전부** 돌려줄 뿐이며, 그중
    어느 것이 우선인지 정하지 않습니다.

    Args:
        description: 원본 적요. ``None`` 이거나 공백일 수 있습니다.

    Returns:
        발견된 :class:`DescriptionHint` 들. 없으면 빈 튜플입니다.

    Examples:
        >>> [hint.keyword for hint in find_hints("○○시설 하도급 공사비")]
        ['하도급', '공사']
        >>> [hint.keyword for hint in find_hints("Adobe CAD 라이선스 수수료")]
        ['수수료']
        >>> find_hints("12월 전기요금")
        ()
        >>> find_hints(None)
        ()
    """
    haystack = normalize_description(description)
    if not haystack:
        return ()
    return tuple(
        DescriptionHint(keyword=keyword, text=f"적요에 '{keyword}' 포함")
        for keyword in HINT_KEYWORDS
        if normalize_description(keyword) in haystack
    )
