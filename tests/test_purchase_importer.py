"""
구매데이터 Import 테스트.

정상/경고/실패 구분, 값 정규화, 기업 연결(매칭), 재매칭을 검증하고,
Import 부터 Dashboard 까지 전체 흐름이 이어지는지 확인합니다.

설계는 ``docs/PURCHASE_IMPORT_DESIGN.md`` 를 따릅니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import init_db
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers import ImportStatus, PurchaseImporter
from procurement.models import Certification, Company, Policy

#: 체크섬까지 유효한 번호 — 경고 없는 "정상" 케이스에 사용
BUSINESS_NO = "1018116293"
#: 형식은 맞지만 체크섬이 틀린 번호 — 경고 케이스에 사용
CHECKSUM_INVALID_NO = "1234567890"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "import.db"
    init_db(path)
    return path


@pytest.fixture
def importer(db_path: Path) -> PurchaseImporter:
    return PurchaseImporter(PurchaseRepository(db_path), CompanyRepository(db_path))


def _row(**overrides: object) -> dict[str, Any]:
    """정상 행을 만들고 일부 값만 바꿉니다."""
    row: dict[str, Any] = {
        "business_no": BUSINESS_NO,
        "company_name": "가기업",
        "contract_date": "2026-03-01",
        "payment_date": "2026-03-15",
        "amount": "3,000,000",
    }
    row.update(overrides)
    return row


def _add_company(db_path: Path, business_no: str = BUSINESS_NO) -> int:
    saved = CompanyRepository(db_path).insert(
        Company(
            business_no=business_no,
            company_name="가기업",
            representative_name="김대표",
        )
    )
    assert saved.company_id is not None
    return saved.company_id


class TestImportNormalValues:
    """정상 데이터 적재를 검증합니다."""

    def test_stores_purchase(self, importer: PurchaseImporter, db_path: Path) -> None:
        _add_company(db_path)
        report = importer.import_rows([_row()])
        assert report.imported_count == 1
        assert report.failed_count == 0
        assert PurchaseRepository(db_path).count() == 1

    def test_normalizes_values(self, importer: PurchaseImporter, db_path: Path) -> None:
        """사업자번호·날짜·금액이 정규화되어 저장됩니다."""
        _add_company(db_path)
        importer.import_rows([_row(business_no="101-81-16293", amount="3,000,000원")])

        purchase = PurchaseRepository(db_path).find_all()[0]
        assert purchase.business_no == BUSINESS_NO  # 하이픈 제거
        assert purchase.contract_date == date(2026, 3, 1)
        assert purchase.payment_date == date(2026, 3, 15)
        assert purchase.amount == Decimal("3000000")  # 콤마·단위 제거

    @pytest.mark.parametrize("value", ["2026-03-01", "20260301", "2026/03/01", "2026.03.01"])
    def test_accepts_date_formats(
        self, importer: PurchaseImporter, db_path: Path, value: str
    ) -> None:
        _add_company(db_path)
        report = importer.import_rows([_row(contract_date=value)])
        assert report.stored_count == 1

    def test_accepts_date_object(self, importer: PurchaseImporter, db_path: Path) -> None:
        _add_company(db_path)
        report = importer.import_rows([_row(contract_date=date(2026, 3, 1))])
        assert report.stored_count == 1

    @pytest.mark.parametrize("value", [3000000, 3000000.0, Decimal("3000000"), "3000000"])
    def test_accepts_amount_types(
        self, importer: PurchaseImporter, db_path: Path, value: object
    ) -> None:
        _add_company(db_path)
        report = importer.import_rows([_row(amount=value)])
        assert report.stored_count == 1
        assert PurchaseRepository(db_path).find_all()[0].amount == Decimal("3000000")


class TestImportFailures:
    """검증 실패로 적재하지 않는 경우를 검증합니다."""

    def test_missing_business_no(self, importer: PurchaseImporter, db_path: Path) -> None:
        report = importer.import_rows([_row(business_no=None)])
        assert report.failed_count == 1
        assert PurchaseRepository(db_path).count() == 0

    def test_invalid_business_no(self, importer: PurchaseImporter) -> None:
        report = importer.import_rows([_row(business_no="12345abcde")])
        assert report.failed_count == 1

    def test_nine_digit_business_no_is_failed_not_corrected(
        self, importer: PurchaseImporter, db_path: Path
    ) -> None:
        """9자리 사업자번호는 자동 보정하지 않고 실패로 처리합니다(PM 결정).

        임의로 앞자리 0 을 채우면 **다른 기업과 잘못 연결될 위험**이 있어
        적재하지 않고, 원인을 리포트에 남깁니다.
        """
        _add_company(db_path)
        report = importer.import_rows([_row(business_no="101811629")])

        assert report.failed_count == 1
        assert PurchaseRepository(db_path).count() == 0
        assert any("앞자리 0" in message for message in report.rows[0].messages)

    def test_nine_digit_original_is_traceable(self, importer: PurchaseImporter) -> None:
        """실패해도 원본 값을 추적할 수 있어야 합니다."""
        report = importer.import_rows([_row(business_no="101811629")])
        assert any("101811629" in message for message in report.rows[0].messages)

    def test_missing_required_column(self, importer: PurchaseImporter) -> None:
        """필수 키가 아예 없으면 실패합니다 — 사업자등록번호."""
        row = _row()
        del row["business_no"]
        report = importer.import_rows([row])
        assert report.failed_count == 1
        assert any("사업자등록번호" in message for message in report.rows[0].messages)

    def test_a_missing_payment_date_no_longer_fails(self, importer: PurchaseImporter) -> None:
        """지급일 키가 없어도 **적재된다** — 🟢 2026-09-02 PM 확정(STEP 87).

        .. note::
            **기대값이 바뀐 이유** — 이 시험은 ``payment_date`` 키를 지우면
            실패하는 것을 잠그고 있었습니다. PM 이 *"실적 산정 및 연도 귀속
            기준은 결의일자"* 이며 *"원본에 존재하지 않는 날짜 때문에 정상
            거래를 미적재시키지 않는다"* 로 확정했으므로, 이제는 **적재되는지**
            를 잠급니다. ⛔ 필수 키 검사 자체를 지운 것이 아니라 위 시험이
            사업자등록번호로 그대로 남아 있습니다.
        """
        row = _row()
        del row["payment_date"]
        report = importer.import_rows([row])

        assert report.failed_count == 0
        assert report.stored_count == 1

    def test_a_missing_contract_date_no_longer_fails(self, importer: PurchaseImporter) -> None:
        """계약일자도 마찬가지다(사유는 위와 같음)."""
        row = _row()
        del row["contract_date"]
        report = importer.import_rows([row])

        assert report.failed_count == 0
        assert report.stored_count == 1

    def test_a_row_with_neither_date_is_stored(
        self, importer: PurchaseImporter, db_path: Path
    ) -> None:
        """⭐ 고객 원본과 같은 모습 — 두 날짜가 모두 없고 결의일자만 있다.

        ⛔ 없는 날짜를 다른 날짜로 채우지 않는다는 것까지 확인한다.
        """
        row = _row()
        del row["payment_date"]
        del row["contract_date"]
        row["resolution_date"] = "2026-03-05"
        report = importer.import_rows([row])

        assert report.stored_count == 1
        stored = PurchaseRepository(db_path).find_all()[0]
        assert stored.contract_date is None
        assert stored.payment_date is None
        assert stored.resolution_date == date(2026, 3, 5)

    def test_invalid_date(self, importer: PurchaseImporter) -> None:
        report = importer.import_rows([_row(contract_date="2026년 3월 1일")])
        assert report.failed_count == 1
        assert any("계약일" in message for message in report.rows[0].messages)

    def test_invalid_amount(self, importer: PurchaseImporter) -> None:
        report = importer.import_rows([_row(amount="삼백만원")])
        assert report.failed_count == 1
        assert any("금액" in message for message in report.rows[0].messages)

    def test_zero_amount_is_rejected_by_current_constraint(
        self, importer: PurchaseImporter
    ) -> None:
        """금액 0 은 현재 Repository 제약으로 저장되지 않습니다.

        PM 결정 D-003(음수·0 보존)과 충돌하는 부분으로, 별도 Issue 대상입니다.
        Import 는 사유를 그대로 리포트에 남깁니다.
        """
        report = importer.import_rows([_row(amount="0")])
        assert report.failed_count == 1
        assert any("0 보다 커야" in message for message in report.rows[0].messages)

    def test_negative_amount_is_rejected_by_current_constraint(
        self, importer: PurchaseImporter
    ) -> None:
        report = importer.import_rows([_row(amount="-1000")])
        assert report.failed_count == 1

    def test_failed_row_keeps_row_number(self, importer: PurchaseImporter) -> None:
        """실패한 행을 지목할 수 있어야 합니다."""
        report = importer.import_rows([_row(), _row(amount="오류")])
        failed = report.failed_rows()
        assert [row.row_number for row in failed] == [2]

    def test_failure_does_not_stop_processing(
        self, importer: PurchaseImporter, db_path: Path
    ) -> None:
        """한 행이 실패해도 나머지 행은 계속 처리됩니다."""
        _add_company(db_path)
        report = importer.import_rows([_row(), _row(business_no=None), _row()])
        assert report.total_count == 3
        assert report.stored_count == 2
        assert report.failed_count == 1


class TestImportWarnings:
    """저장하되 확인이 필요한 경우를 검증합니다."""

    def test_unmatched_company_is_warning(self, importer: PurchaseImporter, db_path: Path) -> None:
        """기업이 없어도 저장하고 경고로 표시합니다(방안 C)."""
        report = importer.import_rows([_row()])
        assert report.warning_count == 1
        assert report.rows[0].status is ImportStatus.WARNING
        assert report.rows[0].matched is False
        assert PurchaseRepository(db_path).count() == 1

    def test_checksum_warning_is_not_rejected(
        self, importer: PurchaseImporter, db_path: Path
    ) -> None:
        """체크섬 오류는 경고일 뿐 적재를 막지 않습니다(D-002)."""
        _add_company(db_path)
        report = importer.import_rows([_row(business_no=CHECKSUM_INVALID_NO)])
        assert report.stored_count == 1
        assert any("체크섬" in message for message in report.rows[0].messages)

    def test_missing_company_name_uses_placeholder(
        self, importer: PurchaseImporter, db_path: Path
    ) -> None:
        _add_company(db_path)
        report = importer.import_rows([_row(company_name=None)])
        assert report.stored_count == 1
        assert PurchaseRepository(db_path).find_all()[0].company_name == "(미상)"
        assert any("업체명" in message for message in report.rows[0].messages)

    def test_contract_after_payment_is_warning_not_failure(
        self, importer: PurchaseImporter, db_path: Path
    ) -> None:
        """계약일이 지급일보다 늦어도 거부하지 않습니다(선지급 가능)."""
        _add_company(db_path)
        report = importer.import_rows([_row(contract_date="2026-05-01", payment_date="2026-03-15")])
        assert report.stored_count == 1
        assert any("계약일" in message for message in report.rows[0].messages)


class TestCompanyMatching:
    """기업 연결(매칭) 동작을 검증합니다."""

    def test_matches_existing_company(self, importer: PurchaseImporter, db_path: Path) -> None:
        company_id = _add_company(db_path)
        report = importer.import_rows([_row()])
        assert report.matched_count == 1
        assert PurchaseRepository(db_path).find_all()[0].company_id == company_id

    def test_matches_after_normalization(self, importer: PurchaseImporter, db_path: Path) -> None:
        """하이픈 표기도 정규화 후 매칭됩니다(G-2 해소 확인)."""
        _add_company(db_path)
        report = importer.import_rows([_row(business_no="101-81-16293")])
        assert report.matched_count == 1

    def test_unmatched_when_company_absent(self, importer: PurchaseImporter, db_path: Path) -> None:
        report = importer.import_rows([_row()])
        assert report.matched_count == 0
        assert report.unmatched_count == 1
        assert PurchaseRepository(db_path).find_all()[0].company_id is None

    def test_match_rate_is_reported(self, importer: PurchaseImporter, db_path: Path) -> None:
        """매칭률은 달성률 신뢰도 지표로 함께 보고됩니다."""
        _add_company(db_path)
        report = importer.import_rows([_row(), _row(business_no="9999999999")])
        assert report.stored_count == 2
        assert report.match_rate == Decimal("50.00")

    def test_duplicate_business_no_all_match(
        self, importer: PurchaseImporter, db_path: Path
    ) -> None:
        """같은 사업자의 여러 거래는 모두 정상 매칭됩니다(중복은 정상)."""
        _add_company(db_path)
        report = importer.import_rows([_row(), _row(), _row()])
        assert report.matched_count == 3


class TestRematch:
    """구매데이터가 먼저 들어온 경우(경우 B)의 재매칭을 검증합니다."""

    def test_rematch_links_after_company_arrives(
        self, importer: PurchaseImporter, db_path: Path
    ) -> None:
        """구매 → (기업 없음) → 기업정보 수집 → 재매칭 → 연결."""
        report = importer.import_rows([_row()])
        assert report.matched_count == 0

        _add_company(db_path)  # 이후 외부에서 기업정보가 들어온 상황
        assert importer.rematch() == 1

        assert PurchaseRepository(db_path).find_all()[0].company_id is not None

    def test_rematch_is_idempotent(self, importer: PurchaseImporter, db_path: Path) -> None:
        """이미 매칭된 건은 다시 처리하지 않습니다."""
        importer.import_rows([_row()])
        _add_company(db_path)
        importer.rematch()
        assert importer.rematch() == 0

    def test_rematch_without_company_changes_nothing(
        self, importer: PurchaseImporter, db_path: Path
    ) -> None:
        importer.import_rows([_row()])
        assert importer.rematch() == 0


class TestImportReport:
    """리포트 집계를 검증합니다."""

    def test_counts(self, importer: PurchaseImporter, db_path: Path) -> None:
        _add_company(db_path)
        report = importer.import_rows(
            [
                _row(),  # 정상(체크섬 경고) → WARNING
                _row(business_no="9999999999"),  # 미매칭 → WARNING
                _row(amount="오류"),  # 실패
            ]
        )
        assert report.total_count == 3
        assert report.failed_count == 1
        assert report.stored_count == 2

    def test_format_report_is_readable(self, importer: PurchaseImporter, db_path: Path) -> None:
        _add_company(db_path)
        text = importer.import_rows([_row()]).format_report()
        assert "전체 행" in text
        assert "매칭률" in text

    def test_empty_input(self, importer: PurchaseImporter) -> None:
        report = importer.import_rows([])
        assert report.total_count == 0
        assert report.match_rate == Decimal("0")


class TestImportToDashboardEndToEnd:
    """Import → 정규화 → 매칭 → 인증 → 정책 판정 → 달성률 → Dashboard."""

    def test_full_chain(self, importer: PurchaseImporter, db_path: Path) -> None:
        """하이픈 표기 구매데이터가 달성률까지 반영되는지 확인합니다.

        인증정보는 외부 API 가 없으므로 Fixture 로 직접 구성합니다.
        정책 구매 3,000,000 / 전체 10,000,000 = 30%, 목표 50% → 달성률 60%.
        """
        company_id = _add_company(db_path)
        policy = PolicyRepository(db_path).insert(
            Policy(
                policy_code="SMALL_BUSINESS",
                policy_name="중소기업",
                evaluation_basis="PAYMENT_DATE",
                target_rate=Decimal("50"),
            )
        )
        assert policy.policy_id is not None
        # ⚠️ STEP 93 — 목표비율의 정본은 **연도별** 값이다(DECISIONS §0.20).
        #    위 Policy.target_rate 는 하위호환으로 남아 있을 뿐 계산에 쓰이지
        #    않으므로, 이 시험이 조회하는 연도(2026)에 같은 값을 등록한다.
        #    ⛔ 기대값은 바뀌지 않았다 — 값을 **어디에 두는지**만 바뀌었다.
        PolicyTargetRepository(db_path).upsert(2026, policy.policy_id, Decimal("50"))
        CertificationRepository(db_path).insert(
            Certification(
                company_id=company_id,
                policy_id=policy.policy_id,
                valid_from=date(2026, 1, 1),
                valid_to=date(2026, 12, 31),
            )
        )

        report = importer.import_rows(
            [
                # 하이픈 표기 — 정규화되어 매칭·인정되어야 한다
                _row(business_no="101-81-16293", amount="3000000"),
                # 미등록 기업 — 전체 구매액에만 포함
                _row(business_no="9999999999", amount="7000000"),
            ]
        )
        assert report.stored_count == 2
        assert report.matched_count == 1

        payload = (
            TestClient(create_app(db_path, period_date_field="payment_date"))
            .get("/dashboard/summary?year=2026")
            .json()
        )
        assert payload["total_purchase_amount"] == "10000000"

        item = {p["policy_code"]: p for p in payload["policies"]}["SMALL_BUSINESS"]
        assert item["purchase_amount"] == "3000000"
        assert item["achievement_rate"] == "60.00"
        assert item["status"] == "SHORTAGE"

    def test_rematch_updates_dashboard(self, importer: PurchaseImporter, db_path: Path) -> None:
        """기업정보가 나중에 들어와도 재매칭 후 달성률에 반영됩니다."""
        policy = PolicyRepository(db_path).insert(
            Policy(
                policy_code="SMALL_BUSINESS",
                policy_name="중소기업",
                evaluation_basis="PAYMENT_DATE",
                target_rate=Decimal("50"),
            )
        )
        assert policy.policy_id is not None
        # ⚠️ STEP 93 — 목표비율의 정본은 연도별 값이다(DECISIONS §0.20).
        PolicyTargetRepository(db_path).upsert(2026, policy.policy_id, Decimal("50"))

        # ① 구매데이터 먼저 — 기업이 없어 미매칭
        importer.import_rows(
            [_row(amount="5000000"), _row(business_no="9999999999", amount="5000000")]
        )
        before = (
            TestClient(create_app(db_path, period_date_field="payment_date"))
            .get("/dashboard/summary?year=2026")
            .json()
        )
        assert {p["policy_code"]: p for p in before["policies"]}["SMALL_BUSINESS"][
            "purchase_amount"
        ] == "0"

        # ② 이후 기업·인증정보 수집
        company_id = _add_company(db_path)
        CertificationRepository(db_path).insert(
            Certification(
                company_id=company_id,
                policy_id=policy.policy_id,
                valid_from=date(2026, 1, 1),
                valid_to=date(2026, 12, 31),
            )
        )

        # ③ 재매칭 → 달성률 반영
        assert importer.rematch() == 1
        after = (
            TestClient(create_app(db_path, period_date_field="payment_date"))
            .get("/dashboard/summary?year=2026")
            .json()
        )
        item = {p["policy_code"]: p for p in after["policies"]}["SMALL_BUSINESS"]
        assert item["purchase_amount"] == "5000000"
        assert item["achievement_rate"] == "100.00"
        assert item["status"] == "NORMAL"
