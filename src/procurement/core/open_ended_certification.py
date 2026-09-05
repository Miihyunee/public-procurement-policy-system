"""
procurement.core.open_ended_certification

**종료일이 없는 인증**(= 시작일 이후로 계속 유효)을 인정하는 정책 목록.

🟢 2026-09-04 고객 확정(STEP 108 §2):

    사회적기업과 사회적협동조합은 종료일이 없으며 계속 유효한 것으로 판단한다.

🟢 2026-09-05 고객 확정(STEP 110):

    종료(취소)일자가 없으면 그냥 사회적기업, 사회적협동조합과 같은 규칙으로
    가면 된다.

장애인표준사업장 자료도 「인증일자」만 있고 종료일 칸이 없어 같은 규칙을
적용합니다. 세 정책 모두 실제 자료에 종료일 칸 자체가 없습니다.

.. warning::
    이 명단은 **고객이 확정한 정책만** 담습니다. 다른 정책(여성기업·
    창업기업·장애인기업 등)은 종료일이 여전히 **필수**입니다 — 그래야
    종료일이 비어 있는 파일이 조용히 "영원히 유효" 로 바뀌지 않습니다.
    ⛔ 새 정책을 여기에 넣는 것은 고객 확정이 있을 때뿐입니다.

.. warning::
    ⛔ 없는 종료일을 지어내지 않습니다. 인가일 + N년, 해당연도 12월 31일,
    ``9999-12-31`` 같은 값은 전부 시스템이 만들어낸 규칙이며, 담당자가
    확인한 적 없는 숫자가 실적이 되게 만듭니다. 종료일이 없으면 ``None``
    으로 두고, 판정에서 "끝이 없다" 로 해석합니다.
"""

from __future__ import annotations

from typing import Final

#: 종료일 없는 인증을 인정하는 정책 코드.
OPEN_ENDED_POLICY_CODES: Final[frozenset[str]] = frozenset(
    {
        # 🟢 2026-09-04 고객 확정
        "SOCIAL_ENTERPRISE",
        "SOCIAL_COOPERATIVE",
        # 🟢 2026-09-05 고객 확정 — "종료(취소)일자가 없으면 그냥 사회적기업,
        #    사회적협동조합과 같은 규칙으로 가면 된다."
        "DISABLED_STANDARD_WORKPLACE",
    }
)


def allows_open_ended(policy_code: str | None) -> bool:
    """이 정책이 **종료일 없는 인증**을 인정하는지 여부.

    Args:
        policy_code: 정책 코드. ``None`` 이면 ``False``.

    Returns:
        고객이 확정한 정책이면 ``True``, 그 외에는 ``False``.
    """
    return policy_code in OPEN_ENDED_POLICY_CODES
