"""
procurement.matchers.company_matcher

구매실적(Purchase)과 기업(Company)을 사업자등록번호 기준으로 연결하는
Matcher 서비스입니다.

``docs/DATA_MAPPING.md`` 의 원칙에 따라 사업자등록번호(``business_no``)를
모든 데이터 연결의 기준 키로 사용합니다. 매칭 결과는
``Purchase.company_id`` 에 반영되며, 이후 정책 달성률 계산(Calculator)의
입력 데이터가 됩니다.

사용 예:
    from procurement.database import CompanyRepository, PurchaseRepository
    from procurement.matchers.company_matcher import CompanyMatcher

    matcher = CompanyMatcher(CompanyRepository(), PurchaseRepository())
    matched_count = matcher.match_all()

.. note::
    본 서비스는 Repository 를 통해서만 데이터에 접근하며 SQL 을 직접 다루지
    않습니다. 없는 데이터에 대해 예외를 발생시키지 않고 ``False`` 를 반환합니다.
"""

from __future__ import annotations

from procurement.database.company_repository import CompanyRepository
from procurement.database.purchase_repository import PurchaseRepository


class CompanyMatcher:
    """사업자등록번호를 기준으로 Purchase 와 Company 를 연결합니다."""

    def __init__(
        self,
        company_repository: CompanyRepository,
        purchase_repository: PurchaseRepository,
    ) -> None:
        """Matcher 를 초기화합니다.

        Args:
            company_repository: 기업 조회에 사용할 :class:`CompanyRepository`.
            purchase_repository: 구매실적 조회/갱신에 사용할 :class:`PurchaseRepository`.
        """
        self._company_repository = company_repository
        self._purchase_repository = purchase_repository

    def match_purchase(self, purchase_id: int) -> bool:
        """구매실적 한 건을 기업과 연결합니다.

        구매실적을 조회하고, 그 사업자등록번호로 기업을 찾아
        ``Purchase.company_id`` 를 갱신합니다.

        Args:
            purchase_id: 매칭할 구매실적의 내부 고유 ID.

        Returns:
            매칭에 성공하면 ``True``.
            구매실적이 없거나 해당 사업자등록번호의 기업이 없으면 ``False``.
        """
        purchase = self._purchase_repository.find_by_id(purchase_id)
        if purchase is None:
            return False

        company = self._company_repository.find_by_business_no(purchase.business_no)
        if company is None or company.company_id is None:
            return False

        return self._purchase_repository.update_company_id(purchase_id, company.company_id)

    def match_all(self) -> int:
        """미매칭 구매실적 전체를 기업과 연결합니다.

        ``company_id`` 가 없는 구매실적만 대상으로 하므로, 이미 매칭된 건은
        다시 처리하지 않습니다.

        Returns:
            매칭에 성공한 구매실적 건수.
        """
        matched = 0
        for purchase in self._purchase_repository.find_unmatched():
            if purchase.purchase_id is None:
                continue
            if self.match_purchase(purchase.purchase_id):
                matched += 1
        return matched
