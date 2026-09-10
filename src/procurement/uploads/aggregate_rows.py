"""
procurement.uploads.aggregate_rows

고객 원본에 섞여 있는 **집계 행**(소계·합계)을 가려냅니다.

왜 필요한가
===========
기관이 회계 시스템에서 내려받는 구매실적 원본에는 거래 한 건이 아니라
**여러 건을 더한 줄**이 함께 들어 있습니다. 실측 원본 2,305행 중 13행이
그런 줄이었고, 그 13행의 「계」 합계만 485억이었습니다(STEP 159).

이 줄들은 거래가 아니므로 저장하면 실적이 그만큼 부풀려집니다. 반대로
지금처럼 **오류**로 보면 「한 행이라도 오류면 저장하지 않는다」는 원칙에
걸려 정상 거래 2,292행까지 함께 막힙니다. 담당자는 매달 원본을 열어 그
줄들을 지워야 하고, 지우는 순간 원본이 아니게 됩니다.

무엇을 집계 행으로 보는가
=========================
🟢 2026-09-10 PM 확정(STEP 160 A-1) — 아래 **네 칸이 모두 비어 있을 때만**
집계 행으로 봅니다.

    기업명 · 사업자등록번호 · 결의일자 · 신고기준일

⛔ 하지 않는 것
===============
- 빈 칸이 하나라도 있으면 건너뛰기 — 네 칸이 **모두** 비어야 합니다.
- 금액이 크다는 이유로 판정하기
- 적요가 「합계」라는 글자라는 이유로 판정하기
- 조용히 버리기 — 몇 행을 뺐는지 결과에 반드시 적습니다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

#: 이 넷이 **모두** 비어 있으면 거래 행이 아니라 집계 행입니다.
#:
#: 머리글 별칭(``거래처명`` → ``기업명`` 등)이 이미 적용된 뒤의 이름입니다.
AGGREGATE_KEY_HEADERS: Final[tuple[str, ...]] = (
    "기업명",
    "사업자등록번호",
    "결의일자",
    "신고기준일",
)


def _blank(value: object) -> bool:
    """빈 칸인가. 공백만 있는 칸도 빈 칸으로 봅니다."""
    return value is None or not str(value).strip()


def is_aggregate_row(row: Mapping[str, object]) -> bool:
    """이 행이 **집계 행**인지 판정합니다.

    Args:
        row: 머리글 → 값 매핑. 머리글 별칭이 적용된 뒤여야 합니다.

    Returns:
        :data:`AGGREGATE_KEY_HEADERS` 네 칸이 모두 비어 있으면 ``True``.
    """
    return all(_blank(row.get(header)) for header in AGGREGATE_KEY_HEADERS)


def split_aggregate_rows(
    rows: Sequence[Mapping[str, object]], *, first_row_number: int
) -> tuple[list[tuple[int, Mapping[str, object]]], tuple[int, ...]]:
    """거래 행과 집계 행을 가릅니다.

    ⭐ **엑셀 행 번호를 함께 돌려줍니다.** 집계 행을 빼고 나면 남은 행의
    순서가 밀리는데, 그대로 번호를 다시 매기면 오류 메시지가 엉뚱한 행을
    가리킵니다. 담당자는 그 번호로 원본을 찾습니다.

    Args:
        rows: 엑셀에서 읽은 행. 머리글 별칭이 적용된 뒤여야 합니다.
        first_row_number: 첫 행의 엑셀 행 번호(머리글이 1행이면 2).

    Returns:
        ``(거래 행 [(엑셀 행 번호, 행)], 집계 행의 엑셀 행 번호들)``.
    """
    data: list[tuple[int, Mapping[str, object]]] = []
    aggregates: list[int] = []
    for offset, row in enumerate(rows):
        row_number = first_row_number + offset
        if is_aggregate_row(row):
            aggregates.append(row_number)
        else:
            data.append((row_number, row))
    return data, tuple(aggregates)
