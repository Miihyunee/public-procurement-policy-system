"""
STEP 87 — **계약일자·지급일이 없는 파일**이 그대로 적재되는가.

🟢 2026-09-02 PM 확정:

    원본에 존재하지 않는 날짜 때문에 결의일자가 정상적으로 존재하는 거래까지
    미적재시키지 않는다.

고객 원본에는 표준 양식의 두 컬럼(계약일자 · 지급일)이 **아예 없습니다**.
그래서 STEP 85 시점에는 실측 2,292행이 **한 행도** 적재되지 않았습니다.

무엇을 지키는가
===============

1. 두 날짜가 비어 있어도 **검증을 통과**하고 **적재**된다.
2. ⛔ 없는 날짜를 **다른 날짜로 채우지 않는다** — NULL 로 남는다.
3. ⛔ 완화가 **번지지 않았다** — 결의일자·사업자등록번호·금액은 그대로 필수.
4. ⛔ 0원·음수 미적재 규칙은 **그대로**다.
5. 창업기업 OR 규칙은 계약일자가 없으면 **결의일자만으로** 판정한다.

.. note::
    합성 데이터만 씁니다. 실제 거래처명·사업자등록번호를 쓰지 않습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.calculators.rules import ResolutionOrContractDateRule, RuleContext
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.company_repository import CompanyRepository
from procurement.database.purchase_repository import (
    PurchaseRepository,
    PurchaseValidationError,
)
from procurement.importers.purchase_importer import PurchaseImporter
from procurement.models.purchase import Purchase
from procurement.uploads.mapping import to_import_rows
from procurement.uploads.validation import validate_headers, validate_rows

#: 합성 사업자등록번호 — 실제 업체의 번호가 아닙니다.
_BUSINESS_NO = "104-86-48203"

#: 고객 원본과 **같은 모양**: 계약일자·지급일 칸이 비어 있다.
_HEADERS = [
    "결의일자",
    "계약일자",
    "지급일",
    "기업명",
    "사업자등록번호",
    "계",
    "신고기준일",
    "적요",
    "예산과목",
]


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "결의일자": "2026-01-16",
        "계약일자": "",  # ⛔ 원본에 없는 컬럼 — 빈 채로 둔다
        "지급일": "",  # 〃
        "기업명": "합성기업 가",
        "사업자등록번호": _BUSINESS_NO,
        "계": "110440",
        "신고기준일": "2026-01-01",
        "적요": "합성 적요",
        "예산과목": "임차료",
    }
    row.update(overrides)
    return row


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "step87.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def importer(db_path: Path) -> PurchaseImporter:
    return PurchaseImporter(PurchaseRepository(db_path), CompanyRepository(db_path))


class TestTheFileValidates:
    """검증 단계에서 막히지 않는가."""

    def test_the_headers_are_complete(self) -> None:
        assert validate_headers(_HEADERS) == []

    def test_a_row_without_the_two_dates_passes(self) -> None:
        report = validate_rows([_row()])
        assert report.error_row_count == 0
        assert len(report.rows) == 1

    def test_many_rows_pass(self) -> None:
        """⭐ 실데이터와 같은 조건 — 전 행이 두 날짜 없이 통과한다."""
        report = validate_rows([_row() for _ in range(50)])
        assert len(report.rows) == 50
        assert report.errors == []

    def test_a_missing_resolution_date_still_fails(self) -> None:
        """⛔ 완화가 **기준일까지 번지지 않았다.**"""
        report = validate_rows([_row(결의일자="")])
        assert report.error_row_count == 1
        assert any(issue.header == "결의일자" for issue in report.errors)

    def test_a_missing_business_no_still_fails(self) -> None:
        report = validate_rows([_row(사업자등록번호="")])
        assert report.error_row_count == 1

    def test_a_missing_amount_still_fails(self) -> None:
        report = validate_rows([_row(계="")])
        assert report.error_row_count == 1

    def test_a_broken_date_still_fails(self) -> None:
        """⛔ 값이 **있는데** 형식이 틀리면 조용히 버리지 않는다."""
        report = validate_rows([_row(계약일자="2026년 2월 20일")])
        assert report.error_row_count == 1


class TestTheRowsImport:
    """적재 단계에서 막히지 않는가."""

    def test_the_row_is_stored(self, importer: PurchaseImporter, db_path: Path) -> None:
        report = validate_rows([_row()])
        result = importer.import_rows(list(to_import_rows(report.rows)))

        assert result.failed_count == 0
        assert result.stored_count == 1

    def test_the_missing_dates_stay_missing(
        self, importer: PurchaseImporter, db_path: Path
    ) -> None:
        """⭐ ⛔ **다른 날짜로 채우지 않는다.**"""
        report = validate_rows([_row()])
        importer.import_rows(list(to_import_rows(report.rows)))

        stored = PurchaseRepository(db_path).find_all()[0]
        assert stored.contract_date is None
        assert stored.payment_date is None
        # 있는 값은 그대로 들어간다.
        assert stored.resolution_date == date(2026, 1, 16)
        assert stored.issue_date == date(2026, 1, 1)
        assert stored.amount == Decimal("110440")

    def test_the_business_number_is_not_altered(
        self, importer: PurchaseImporter, db_path: Path
    ) -> None:
        """하이픈만 떼고 숫자는 그대로다."""
        report = validate_rows([_row()])
        importer.import_rows(list(to_import_rows(report.rows)))

        stored = PurchaseRepository(db_path).find_all()[0]
        assert stored.business_no == _BUSINESS_NO.replace("-", "")

    def test_the_whole_file_imports(self, importer: PurchaseImporter, db_path: Path) -> None:
        """⭐ 여러 행이 **전부** 적재된다 — 미적재 0."""
        rows = [_row(계=str(1000 + i)) for i in range(30)]
        report = validate_rows(rows)
        result = importer.import_rows(list(to_import_rows(report.rows)))

        assert result.stored_count == 30
        assert result.failed_count == 0
        assert len(PurchaseRepository(db_path).find_all()) == 30


class TestTheOtherRulesAreUnchanged:
    """⛔ 이번 완화가 다른 규칙으로 번지지 않았는가."""

    @pytest.mark.parametrize("amount", ["0", "-500"])
    def test_zero_and_negative_are_still_rejected(
        self, importer: PurchaseImporter, amount: str
    ) -> None:
        """⛔ 0원·음수 미적재 규칙 그대로(🔴 §0.12.16 미확정)."""
        report = validate_rows([_row(계=amount)])
        result = importer.import_rows(list(to_import_rows(report.rows)))
        assert result.stored_count == 0
        assert result.failed_count == 1

    def test_the_repository_still_rejects_non_positive_amounts(self, db_path: Path) -> None:
        with pytest.raises(PurchaseValidationError):
            PurchaseRepository(db_path).insert(
                Purchase(
                    business_no="1048648203",
                    company_name="합성기업 가",
                    resolution_date=date(2026, 1, 16),
                    amount=Decimal("0"),
                )
            )

    def test_the_repository_still_requires_the_identifiers(self, db_path: Path) -> None:
        """⛔ 사업자등록번호·업체명 필수는 그대로."""
        with pytest.raises(PurchaseValidationError):
            PurchaseRepository(db_path).insert(
                Purchase(
                    business_no="  ",
                    company_name="합성기업 가",
                    resolution_date=date(2026, 1, 16),
                    amount=Decimal("100"),
                )
            )


class TestTheStartupRuleWithoutAContractDate:
    """🟢 창업기업 규칙은 **바뀌지 않았다** — 없는 쪽이 빠질 뿐이다."""

    VALID = [(date(2026, 1, 1), date(2026, 12, 31))]

    def _matches(self, resolution: date | None, contract: date | None) -> bool:
        purchase = Purchase(
            business_no="1048648203",
            company_name="합성기업 가",
            resolution_date=resolution,
            contract_date=contract,
            amount=Decimal("100000"),
        )
        return ResolutionOrContractDateRule().matches(
            RuleContext(purchase=purchase, validity_ranges=self.VALID)
        )

    def test_the_resolution_date_alone_can_accept(self) -> None:
        """계약일자가 없어도 결의일자가 기간 안이면 인정된다."""
        assert self._matches(resolution=date(2026, 6, 1), contract=None) is True

    def test_the_resolution_date_alone_can_reject(self) -> None:
        assert self._matches(resolution=date(2027, 6, 1), contract=None) is False

    def test_the_contract_date_alone_still_works(self) -> None:
        """⛔ OR 의 다른 쪽도 그대로다."""
        assert self._matches(resolution=None, contract=date(2026, 6, 1)) is True

    def test_neither_date_is_rejected(self) -> None:
        """⛔ 둘 다 없으면 판정하지 않는다 — 임의로 인정하지 않는다."""
        assert self._matches(resolution=None, contract=None) is False
