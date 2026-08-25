"""
procurement.importers.rematch_service

**미매칭 구매를 기업과 다시 연결**하는 운영 진입점입니다.

구매데이터가 먼저 들어오고 기업정보가 나중에 확보되는 것이 이 시스템의 정상
흐름입니다(``PURCHASE_IMPORT_DESIGN.md`` §6.3 "경우 B"). 연결 로직 자체는
:meth:`~procurement.importers.purchase_importer.PurchaseImporter.rematch` 에
이미 있었지만 **부를 방법이 없었습니다.** 이 서비스는 그것을 부르고, 담당자가
결과를 확인할 수 있도록 **전후 건수를 재서** 함께 돌려줍니다::

    RematchService → PurchaseImporter.rematch() → CompanyMatcher.match_all()
                   → PurchaseRepository (전후 건수 측정)

.. warning::
    ⛔ **기업정보를 만들지 않습니다.** 이 서비스는
    :class:`~procurement.database.company_repository.CompanyRepository` 를
    **주입받지 않으므로** 구조적으로 기업을 만들 수 없습니다. 기업이 없으면
    그 구매는 그대로 미매칭으로 남습니다.

.. warning::
    ⛔ **연결 규칙을 바꾸지 않습니다.** 사업자등록번호 완전 일치라는 기존 규칙을
    그대로 씁니다(:class:`~procurement.matchers.company_matcher.CompanyMatcher`).
    거래처명 유사도·부분 일치 같은 것을 새로 만들지 않습니다.

.. warning::
    ⛔ **이미 연결된 구매를 건드리지 않습니다.** ``match_all()`` 이
    ``find_unmatched()`` 만 순회하므로, 몇 번을 실행해도 기존 연결이 바뀌거나
    끊기지 않습니다(**멱등**).

.. note::
    **오류 건수를 만들어 내지 않습니다.**

    ``rematch()`` 는 새로 연결된 **건수(int) 하나**만 돌려줍니다. 실패 사유를
    구분해 주는 통로가 없고,
    :meth:`~procurement.matchers.company_matcher.CompanyMatcher.match_purchase`
    는 기업을 못 찾으면 예외 대신 ``False`` 를 돌려줍니다. 따라서 이 서비스도
    "오류 N건" 을 지어내지 않고, **연결되지 않고 남은 건수**만 사실대로
    보고합니다(:attr:`RematchResult.still_unmatched`).
"""

from __future__ import annotations

from dataclasses import dataclass

from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.purchase_importer import PurchaseImporter


@dataclass(frozen=True, kw_only=True)
class RematchResult:
    """재매칭 한 번의 결과.

    Attributes:
        unmatched_before: 실행 **전** 미매칭 구매 건수.
        attempted: 연결을 시도한 건수. ``match_all()`` 이 실행 시점의
            ``find_unmatched()`` 를 그대로 순회하므로
            :attr:`unmatched_before` 와 같습니다.
        matched: **새로** 연결된 건수. ``rematch()`` 가 돌려준 값 그대로입니다.
        still_unmatched: 실행 **후**에도 남은 미매칭 건수.

            ⚠️ "오류" 가 아닙니다. 대부분은 **해당 사업자번호의 기업정보가 아직
            등록되지 않았다**는 뜻이며, 기업을 등록한 뒤 다시 실행하면
            연결됩니다.
    """

    unmatched_before: int
    attempted: int
    matched: int
    still_unmatched: int


class RematchService:
    """미매칭 구매를 기업과 다시 연결하고, 전후 건수를 함께 돌려줍니다."""

    def __init__(
        self,
        importer: PurchaseImporter,
        purchase_repository: PurchaseRepository,
    ) -> None:
        """서비스를 초기화합니다.

        Args:
            importer: 연결을 수행할 :class:`PurchaseImporter`. ⛔ **적재에
                사용하지 않습니다** — :meth:`rematch` 만 부릅니다.
            purchase_repository: 전후 건수 측정용. ⛔ **쓰기에 사용하지
                않습니다** — 세는 일만 합니다.
        """
        self._importer = importer
        self._purchase_repository = purchase_repository

    def rematch(self) -> RematchResult:
        """미매칭 구매를 기업과 다시 연결합니다.

        ⛔ 기업정보를 만들지 않습니다. 등록된 기업이 있는 건만 연결되고,
        나머지는 그대로 미매칭으로 남습니다.

        ⚠️ 몇 번을 실행해도 안전합니다. 두 번째 실행은 남은 미매칭만 다시 보고,
        새로 등록된 기업이 없으면 ``matched=0`` 입니다.

        Returns:
            :class:`RematchResult`. 미매칭이 하나도 없으면 전부 ``0`` 입니다.
        """
        before = len(self._purchase_repository.find_unmatched())
        matched = self._importer.rematch()
        after = len(self._purchase_repository.find_unmatched())

        return RematchResult(
            unmatched_before=before,
            # ``match_all()`` 은 실행 시점의 미매칭 전량을 순회한다.
            attempted=before,
            matched=matched,
            still_unmatched=after,
        )
