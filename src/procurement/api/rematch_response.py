"""
procurement.api.rematch_response

재매칭 결과(:class:`RematchResult`)를 **API 응답 전용 Pydantic 모델**로
변환합니다.

.. note::
    **없는 정보를 만들어 내지 않습니다.** 기존
    :meth:`~procurement.importers.purchase_importer.PurchaseImporter.rematch`
    는 새로 연결된 건수 하나만 돌려주며 실패 사유를 구분해 주지 않습니다.
    그래서 이 응답에도 "오류 N건" 이 없고, **연결되지 않고 남은 건수**만
    사실대로 담습니다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from procurement.importers.rematch_service import RematchResult


class RematchResponseModel(BaseModel):
    """재매칭 한 번의 결과.

    Attributes:
        unmatched_before: 실행 전 미매칭 구매 건수.
        attempted: 연결을 시도한 건수.
        matched: 새로 연결된 건수.
        still_unmatched: 실행 후에도 남은 미매칭 건수.
        notice: 결과를 설명하는 화면 표시용 문구.
    """

    model_config = ConfigDict(frozen=True)

    unmatched_before: int
    attempted: int
    matched: int
    still_unmatched: int
    notice: str

    @classmethod
    def from_result(cls, result: RematchResult) -> RematchResponseModel:
        """:class:`RematchResult` 로부터 응답 모델을 생성합니다."""
        return cls(
            unmatched_before=result.unmatched_before,
            attempted=result.attempted,
            matched=result.matched,
            still_unmatched=result.still_unmatched,
            notice=_notice(result),
        )


#: 미매칭이 애초에 없었을 때.
NOTICE_NOTHING_TO_DO = "미매칭 구매가 없어 재매칭할 대상이 없습니다."

#: 시도했으나 하나도 연결되지 않았을 때.
#:
#: ⛔ "오류" 라고 쓰지 않는다 — 기업정보가 아직 없다는 뜻일 뿐이다.
NOTICE_NONE_MATCHED = (
    "새로 연결된 구매가 없습니다. 해당 사업자등록번호의 기업정보가 아직 "
    "등록되지 않았습니다 — 기업정보를 등록한 뒤 다시 실행하면 연결됩니다."
)


def _notice(result: RematchResult) -> str:
    """결과를 설명하는 문구를 고릅니다.

    ⛔ 업무 판단이 아니라 **결과 설명**입니다. 남은 건을 "오류" · "실패" 로
    부르지 않습니다.
    """
    if result.attempted == 0:
        return NOTICE_NOTHING_TO_DO
    if result.matched == 0:
        return NOTICE_NONE_MATCHED
    if result.still_unmatched == 0:
        return f"구매 {result.matched}건을 기업정보와 연결했습니다. 남은 미매칭이 없습니다."
    return (
        f"구매 {result.matched}건을 기업정보와 연결했습니다. "
        f"{result.still_unmatched}건은 기업정보가 아직 등록되지 않아 그대로 남았습니다."
    )
