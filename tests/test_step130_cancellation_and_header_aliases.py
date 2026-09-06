"""
STEP 129 — 취소된 인증과 **고객 원본 머리글**.

두 가지를 다룹니다.

1. 인증 명단의 ``취소일자``
   비어 있으면 유효한 인증, 값이 있으면 취소된 인증으로 **보관**합니다.
2. 고객 기관이 내려받은 원본 명단의 항목명
   담당자가 원본을 고치지 않아도 올릴 수 있어야 합니다.

⛔ 소급 인정을 구현하지 않았다
==============================
취소일과 거래일을 견주어 과거 실적을 다시 판정하는 일은 **하지 않습니다.**
정상 발급 후 사후 변동으로 취소된 것과 거짓·부정한 방법으로 발급되어 취소된
것도 구분하지 않습니다. 향후 고도화 과제입니다(지시서 §13).

⛔ 짐작해서 머리글을 짝지어 주지 않는다
=======================================
실제로 받아 본 파일에서 확인한 이름만 옮깁니다. 글자가 비슷하다고 자동으로
연결하지 않습니다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

from procurement.database.certification_repository import CertificationRepository
from procurement.models.certification import Certification
from procurement.uploads.company_header_aliases import (
    COMPANY_HEADER_ALIASES,
    canonical_headers,
)
from procurement.uploads.company_source_service import CompanySourceService

#: ⛔ 실제 고객 자료가 아닙니다. 검증자릿수를 맞춘 합성 사업자번호입니다.
SYNTHETIC_BUSINESS_NO = "1000000009"


def _certification(**overrides: object) -> Certification:
    values: dict[str, object] = {
        "company_id": 1,
        "policy_id": 1,
        "valid_from": date(2026, 1, 1),
        "valid_to": date(2026, 12, 31),
    }
    values.update(overrides)
    return Certification(**values)  # type: ignore[arg-type]


# ======================================================================
# §5  취소일자를 어떻게 읽는가
# ======================================================================
class TestWhetherACertificationCountsAsCancelled:
    def test_case_1_none_is_not_cancelled(self) -> None:
        """CASE 1 — 값이 없으면 유효한 인증이다."""
        assert _certification(cancelled_on=None).is_cancelled is False

    def test_case_4_a_date_means_cancelled(self) -> None:
        """CASE 4 — 날짜가 있으면 취소된 인증이다."""
        assert _certification(cancelled_on=date(2026, 6, 1)).is_cancelled is True

    @pytest.mark.parametrize("blank", [None, "", "   ", "\t"])
    def test_case_2_and_3_blank_cells_read_as_not_cancelled(self, blank: object) -> None:
        """CASE 2·3 — 빈 셀·빈 문자열·공백은 모두 「취소되지 않음」이다.

        엑셀은 같은 «빈칸»을 여러 모양으로 넘겨준다. 어느 모양이든 같게
        읽혀야 한다.
        """
        from procurement.uploads.company_format import (
            policy_scoped_columns,
            row_columns,
        )
        from procurement.uploads.validation import validate_rows

        report = validate_rows(
            [
                {
                    "사업자등록번호": SYNTHETIC_BUSINESS_NO,
                    "기업명": "합성기업",
                    "유효시작일": "2026-01-01",
                    "유효종료일": "2026-12-31",
                    "취소일자": blank,
                }
            ],
            columns=row_columns(policy_scoped_columns("WOMAN")),
        )

        assert report.rows, report.issues
        assert report.rows[0].values.get("cancelled_on") is None

    def test_a_filled_cancellation_date_is_parsed(self) -> None:
        """값이 있으면 날짜로 읽어 **보관**한다 — 버리지 않는다."""
        from procurement.uploads.company_format import (
            policy_scoped_columns,
            row_columns,
        )
        from procurement.uploads.validation import validate_rows

        report = validate_rows(
            [
                {
                    "사업자등록번호": SYNTHETIC_BUSINESS_NO,
                    "기업명": "합성기업",
                    "유효시작일": "2026-01-01",
                    "유효종료일": "2026-12-31",
                    "취소일자": "2026-06-01",
                }
            ],
            columns=row_columns(policy_scoped_columns("WOMAN")),
        )

        assert report.rows[0].values["cancelled_on"] == date(2026, 6, 1)


# ======================================================================
# §4  계산에 쓰는 「유효 인증」에서 빠지는가
# ======================================================================
class TestCancelledCertificationsAreNotValidOnes:
    def test_a_cancelled_one_is_left_out(self, tmp_path: Path) -> None:
        """⭐ 취소된 인증은 계산 대상에서 빠진다."""
        repository = CertificationRepository(str(tmp_path / "t.db"))
        repository.create_table()
        repository.insert(_certification(company_id=1, cancelled_on=None))
        repository.insert(_certification(company_id=2, cancelled_on=date(2026, 6, 1)))

        active = repository.find_active_by_policy(1)

        assert [item.company_id for item in active] == [1]

    def test_the_cancelled_one_is_still_kept(self, tmp_path: Path) -> None:
        """⛔ 빠지는 것이지 **지워지는 것이 아니다** — 이력으로 남는다."""
        repository = CertificationRepository(str(tmp_path / "t.db"))
        repository.create_table()
        repository.insert(_certification(company_id=2, cancelled_on=date(2026, 6, 1)))

        everything = repository.find_by_policy(1)

        assert len(everything) == 1
        assert everything[0].cancelled_on == date(2026, 6, 1)

    def test_case_5_no_date_comparison_anywhere(self) -> None:
        """CASE 5 — ⛔ 취소일과 거래일을 견주는 코드를 만들지 않았다.

        「취소일이 거래일 이후이므로 인정」 같은 소급 판정은 이번 단계의
        범위가 아니다(지시서 §2·§5).
        """
        import procurement.calculators.procurement_achievement as calculator
        import procurement.calculators.rules.date_rules as rules

        for module in (calculator, rules):
            source = Path(module.__file__ or "").read_text(encoding="utf-8")
            assert "cancelled_on" not in source, module.__name__


# ======================================================================
# §6  고객 원본 머리글
# ======================================================================
class TestTheCustomerFileIsReadAsItIs:
    def test_the_confirmed_mapping(self) -> None:
        """🟢 2026-09-06 PM 확정 — 실제 여성기업 명단에서 확인한 대응."""
        assert dict(COMPANY_HEADER_ALIASES) == {
            "업체명": "기업명",
            "사업자번호": "사업자등록번호",
            "시작일자": "유효시작일",
            "만료일자": "유효종료일",
        }

    def test_the_representative_name_needs_no_mapping(self) -> None:
        """``대표자명`` 은 이름이 같아서 대응이 없다."""
        assert "대표자명" not in COMPANY_HEADER_ALIASES

    def test_the_customer_headers_become_standard_ones(self) -> None:
        """⭐ 실제 명단의 머리글이 표준 이름으로 바뀐다."""
        customer = (
            "NO",
            "업체명",
            "사업자번호",
            "대표자명",
            "기업구분",
            "여성기업",
            "시작일자",
            "만료일자",
            "취소일자",
        )

        assert canonical_headers(customer, COMPANY_HEADER_ALIASES) == (
            "NO",
            "기업명",
            "사업자등록번호",
            "대표자명",
            "기업구분",
            "여성기업",
            "유효시작일",
            "유효종료일",
            "취소일자",
        )

    def test_a_standard_file_is_untouched(self) -> None:
        """⛔ 기존 표준 양식은 그대로 지나간다(§10)."""
        standard = ("사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일")

        assert canonical_headers(standard, COMPANY_HEADER_ALIASES) == standard

    def test_it_does_not_merge_two_columns_into_one(self) -> None:
        """⛔ 표준 이름이 이미 있으면 바꾸지 않는다.

        ``기업명`` 과 ``업체명`` 이 함께 있는 파일을 하나로 합치면 어느 값이
        남는지 사용자가 알 수 없다. 그런 파일은 그대로 두어 검증 계층이
        판단하게 한다.
        """
        both = ("기업명", "업체명")

        assert canonical_headers(both, COMPANY_HEADER_ALIASES) == both

    def test_nothing_is_matched_by_resemblance(self) -> None:
        """⛔ 비슷해 보인다고 짝지어 주지 않는다(§7)."""
        for lookalike in ("회사명", "업체 명", "사업자 번호", "개시일자", "종료일자"):
            assert canonical_headers((lookalike,), COMPANY_HEADER_ALIASES) == (lookalike,)


# ======================================================================
# §8 · §11  파일을 실제로 올려 본다
# ======================================================================
def _validation_service() -> CompanySourceService:
    """검증만 하는 서비스.

    ``validate_file`` 은 저장 계층을 건드리지 않지만, 그렇다고 반쯤 만든
    객체를 쓰지는 않는다 — 실제 생성 경로를 그대로 지난다.
    """
    from procurement.importers.company_importer import CompanyImporter
    from procurement.uploads.company_source_service import CompanySourceService

    return CompanySourceService(CompanyImporter.__new__(CompanyImporter))


def _write(path: Path, headers: list[str], rows: list[list[object]]) -> str:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return str(path)


class TestUploadingBothShapesOfFile:
    def test_the_customer_original_is_accepted_unchanged(self, tmp_path: Path) -> None:
        """⭐ 담당자가 원본을 고치지 않아도 올라간다(§8)."""
        path = _write(
            tmp_path / "woman.xlsx",
            [
                "NO",
                "업체명",
                "사업자번호",
                "대표자명",
                "기업구분",
                "여성기업",
                "시작일자",
                "만료일자",
                "취소일자",
            ],
            [
                [
                    1,
                    "합성기업",
                    SYNTHETIC_BUSINESS_NO,
                    "홍길동",
                    "중소",
                    "Y",
                    "2026-01-01",
                    "2026-12-31",
                    None,
                ]
            ],
        )

        report = _validation_service().validate_file(path, policy_code="WOMAN")

        assert report.file_errors == []
        assert report.rows, report.issues
        assert report.rows[0].values["company_name"] == "합성기업"
        assert report.rows[0].values["valid_from"] == date(2026, 1, 1)
        assert report.rows[0].values["valid_to"] == date(2026, 12, 31)
        assert report.rows[0].values.get("cancelled_on") is None

    def test_a_cancelled_row_keeps_its_date(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path / "woman2.xlsx",
            ["업체명", "사업자번호", "대표자명", "시작일자", "만료일자", "취소일자"],
            [
                [
                    "합성기업",
                    SYNTHETIC_BUSINESS_NO,
                    "홍길동",
                    "2026-01-01",
                    "2026-12-31",
                    "2026-06-01",
                ]
            ],
        )

        report = _validation_service().validate_file(path, policy_code="WOMAN")

        assert report.file_errors == []
        assert report.rows[0].values["cancelled_on"] == date(2026, 6, 1)

    def test_the_existing_standard_file_still_works(self, tmp_path: Path) -> None:
        """⛔ 기존 표준 파일이 계속 정상 처리된다(§10)."""
        path = _write(
            tmp_path / "standard.xlsx",
            ["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"],
            [[SYNTHETIC_BUSINESS_NO, "합성기업", "홍길동", "2026-01-01", "2026-12-31"]],
        )

        report = _validation_service().validate_file(path, policy_code="WOMAN")

        assert report.file_errors == []
        assert report.rows[0].values["business_no"] == SYNTHETIC_BUSINESS_NO
        assert report.rows[0].values.get("cancelled_on") is None


# ======================================================================
# §13  고도화 과제를 적어 두었는가
# ======================================================================
class TestTheDeferredWorkIsWrittenDown:
    def test_the_todo_is_recorded(self) -> None:
        """⛔ 하지 않은 일을 기록해 둔다 — 잊으면 조용히 사라진다."""
        decisions = (Path(__file__).resolve().parents[1] / "docs" / "DECISIONS.md").read_text(
            encoding="utf-8"
        )

        assert "소급" in decisions
        assert "취소 사유" in decisions
