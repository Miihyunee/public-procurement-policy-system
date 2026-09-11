"""
STEP 158 — 고객이 매달 받는 구매실적 원본을 고치지 않고 올릴 수 있다.

무엇이 문제였나
===============
실제 고객 PC 의 구매실적 원본(2,305행)을 올리려다 통째로 거절됐다
(STEP 157-㉠).

    필수 항목이 없습니다: 계약일자, 지급일, 기업명, 사업자등록번호.

두 가지가 겹쳐 있었다.

① **칸 이름이 다르다** — 원본은 ``거래처명`` · ``사업자번호`` 다.
   기업정보(명단) 업로드에는 이름을 옮겨 읽는 장치가 이미 있는데
   구매실적에만 없었다.

② **없어도 되는 칸을 칸 유무로 막았다** — ``계약일자`` · ``지급일`` 은
   🟢 2026-09-02 PM 확정으로 **값이 비어 있어도 되는** 항목인데, 칸 이름이
   없다는 이유로 파일 전체를 거절했다. 값이 없어도 되는 항목을 칸 유무로
   막는 것은 앞뒤가 맞지 않는다.

무엇을 바꿨나
=============
- 구매실적 업로드에도 머리글 별칭을 둔다. **실측한 두 가지만** 넣는다.
- 머리글 검증은 ``required`` 인 항목만 요구한다.

⛔ 원본 파일을 고치지 않는다 — 읽을 때 칸 이름만 옮긴다.
⛔ 없는 값을 만들지 않는다 — 계약일자·지급일은 없으면 ``None`` 이다.
⛔ 필수 항목은 그대로 필수다.
⛔ 값 검증·금액·배치 교체·상계·계산 규칙은 하나도 바꾸지 않았다.
⛔ 구매유형을 자동 판정하지 않는다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.app import create_app
from procurement.database.bootstrap import bootstrap
from procurement.uploads.format import STANDARD_COLUMNS
from procurement.uploads.purchase_header_aliases import PURCHASE_HEADER_ALIASES
from procurement.uploads.validation import validate_headers

#: 실제 고객 원본의 머리글 **모양**. ⛔ 고객 자료가 아니다 — 칸 이름만 같다.
CUSTOMER_HEADERS = (
    "번호",
    "신고기준일",
    "적요",
    "거래처명",
    "사업자번호",
    "공급가액",
    "세액",
    "계",
    "결의일자",
    "예산과목",
)

#: 내려받는 표준 양식의 머리글.
TEMPLATE_HEADERS = tuple(column.header for column in STANDARD_COLUMNS)

#: ⛔ 실제 업체의 번호가 아니다. 검증자릿수만 맞춘 합성 사업자번호.
SYNTHETIC = ("1000000009", "1000000014")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _book(path: Path, headers: tuple[str, ...], rows: list[list[object]]) -> str:
    book = Workbook()
    sheet = book.active
    sheet.title = "원본"
    sheet.append(list(headers))
    for row in rows:
        sheet.append(row)
    book.save(path)
    return str(path)


def _customer_shaped(path: Path, *, count: int = 2) -> str:
    """고객 원본과 **같은 머리글 모양**의 합성 파일."""
    rows: list[list[object]] = []
    for index in range(count):
        rows.append(
            [
                index + 1,
                "2026/01/01",
                f"합성 지출 {index}",
                f"합성{index}기업",
                SYNTHETIC[index % len(SYNTHETIC)],
                100000,
                10000,
                110000,
                "2026/01/16",
                "임차료",
            ]
        )
    return _book(path, CUSTOMER_HEADERS, rows)


def _template_shaped(path: Path) -> str:
    """내려받은 표준 양식 그대로 채운 파일."""
    return _book(
        path,
        TEMPLATE_HEADERS,
        [["2026-01-16", "2026-01-05", "2026-02-01", "합성기업", SYNTHETIC[0], 110000,
          "2026-01-01", "합성 지출", "임차료"]],
    )


def _validate(client: TestClient, file_path: str) -> dict[str, Any]:
    response = client.post("/uploads/purchases/validate", json={"file_path": file_path})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _headers_of(columns: tuple[str, ...], drop: str) -> tuple[str, ...]:
    return tuple(header for header in columns if header != drop)


# ======================================================================
# ① · ② 두 모양 모두 통과하는가
# ======================================================================
class TestBothTheTemplateAndTheCustomerOriginalAreAccepted:
    def test_1_the_standard_template_still_passes(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """① 기존 표준 양식 — ⛔ 깨뜨리지 않았다."""
        result = _validate(client, _template_shaped(tmp_path / "template.xlsx"))
        assert result["ok"] is True
        assert result["file_errors"] == []
        assert result["valid_rows"] == 1

    def test_2_the_customer_original_shape_passes(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """② 거래처명 · 사업자번호 · 계약일자 없음 · 지급일 없음 → 통과."""
        result = _validate(client, _customer_shaped(tmp_path / "raw.xlsx"))
        assert result["file_errors"] == []
        assert result["ok"] is True
        assert result["valid_rows"] == 2

    def test_3_a_missing_contract_date_column_is_fine(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """③ 계약일자 칸이 없어도 거절하지 않는다."""
        path = _book(
            tmp_path / "no_contract.xlsx",
            _headers_of(TEMPLATE_HEADERS, "계약일자"),
            [["2026-01-16", "2026-02-01", "합성기업", SYNTHETIC[0], 110000,
              "2026-01-01", "합성 지출", "임차료"]],
        )
        assert _validate(client, path)["ok"] is True

    def test_4_a_missing_payment_date_column_is_fine(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """④ 지급일 칸이 없어도 거절하지 않는다."""
        path = _book(
            tmp_path / "no_payment.xlsx",
            _headers_of(TEMPLATE_HEADERS, "지급일"),
            [["2026-01-16", "2026-01-05", "합성기업", SYNTHETIC[0], 110000,
              "2026-01-01", "합성 지출", "임차료"]],
        )
        assert _validate(client, path)["ok"] is True

    def test_5_the_dates_are_still_read_when_the_columns_exist(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⑤ 칸이 있으면 예전 그대로 읽는다 — 느슨해진 것은 칸 유무뿐이다."""
        path = _template_shaped(tmp_path / "template.xlsx")
        response = client.post(
            "/uploads/purchases", json={"file_path": path, "year": 2026, "month": 1}
        )
        assert response.status_code == 200, response.text
        assert response.json()["stored"] is True

        stored = sqlite3.connect(db).execute(
            "SELECT contract_date, payment_date FROM purchase"
        ).fetchall()
        assert stored == [("2026-01-05", "2026-02-01")]


# ======================================================================
# ⑥ ~ ⑩ 필수는 그대로 필수인가
# ======================================================================
class TestTheRequiredColumnsAreStillRequired:
    @pytest.mark.parametrize(
        ("dropped", "shown"),
        [
            ("결의일자", "결의일자"),
            ("신고기준일", "신고기준일"),
            ("계", "계"),
        ],
    )
    def test_6_7_8_a_missing_required_column_is_refused(
        self, client: TestClient, tmp_path: Path, dropped: str, shown: str
    ) -> None:
        """⑥⑦⑧ 결의일자 · 신고기준일 · 계 가 없으면 거절한다."""
        path = _book(tmp_path / f"no_{shown}.xlsx", _headers_of(TEMPLATE_HEADERS, dropped), [])
        result = _validate(client, path)
        assert result["ok"] is False
        assert any(shown in error for error in result["file_errors"]), result["file_errors"]

    def test_9_no_company_name_column_at_all_is_refused(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⑨ 기업명도 거래처명도 없으면 거절한다."""
        headers = tuple(h for h in CUSTOMER_HEADERS if h != "거래처명")
        path = _book(tmp_path / "no_name.xlsx", headers, [])
        result = _validate(client, path)
        assert result["ok"] is False
        assert any("기업명" in error for error in result["file_errors"]), result["file_errors"]

    def test_10_no_business_no_column_at_all_is_refused(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⑩ 사업자등록번호도 사업자번호도 없으면 거절한다."""
        headers = tuple(h for h in CUSTOMER_HEADERS if h != "사업자번호")
        path = _book(tmp_path / "no_bizno.xlsx", headers, [])
        result = _validate(client, path)
        assert result["ok"] is False
        assert any(
            "사업자등록번호" in error for error in result["file_errors"]
        ), result["file_errors"]

    def test_a_blank_required_value_is_still_a_row_error(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⛔ 칸이 있어도 값이 비면 그 행은 여전히 오류다 — 값 규칙 그대로."""
        path = _book(
            tmp_path / "blank.xlsx",
            CUSTOMER_HEADERS,
            [[1, "2026/01/01", "합성 지출", "", SYNTHETIC[0], 100000, 10000, 110000,
              "2026/01/16", "임차료"]],
        )
        result = _validate(client, path)
        assert result["ok"] is False
        assert result["error_rows"] == 1
        assert result["file_errors"] == []

    def test_the_required_headers_are_exactly_the_five(self) -> None:
        """머리글로 요구하는 것은 업무상 핵심 다섯 가지뿐이다."""
        missing = validate_headers(())
        assert missing
        for header in ("결의일자", "기업명", "사업자등록번호", "계", "신고기준일"):
            assert header in missing[0], header
        for optional in ("계약일자", "지급일", "적요", "예산과목"):
            assert optional not in missing[0], optional


# ======================================================================
# ⑪ 같은 칸이 두 이름으로 함께 있을 때
# ======================================================================
class TestAStandardHeaderWinsOverItsAlias:
    def test_11_the_alias_does_not_overwrite_the_standard_column(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⑪ 기업명과 거래처명이 **둘 다** 있으면 표준 칸의 값을 쓴다.

        ⛔ 먼저 나온 칸을 고르지 않는다 — 표준 이름이 있으면 별칭은 옮기지
        않는다는 기존 정책(:func:`canonical_headers`)을 그대로 따른다.
        여기서는 거래처명이 **앞에** 있는데도 기업명이 이긴다.
        """
        path = _book(
            tmp_path / "both.xlsx",
            ("신고기준일", "거래처명", "기업명", "사업자번호", "사업자등록번호", "계", "결의일자"),
            [["2026-01-01", "별칭쪽기업", "표준쪽기업", SYNTHETIC[1], SYNTHETIC[0], 110000,
              "2026-01-16"]],
        )
        assert _validate(client, path)["ok"] is True

        response = client.post(
            "/uploads/purchases", json={"file_path": path, "year": 2026, "month": 1}
        )
        assert response.status_code == 200, response.text
        stored = sqlite3.connect(db).execute(
            "SELECT company_name, business_no FROM purchase"
        ).fetchall()
        assert stored == [("표준쪽기업", SYNTHETIC[0])]


# ======================================================================
# ⑫ 원본은 그대로 두고 **읽을 때만** 옮기는가
# ======================================================================
class TestOnlyTheReadingIsTranslated:
    def test_12_the_workbook_on_disk_is_not_modified(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """⑫ 검증·저장을 거쳐도 파일 내용은 한 바이트도 바뀌지 않는다."""
        path = Path(_customer_shaped(tmp_path / "raw.xlsx"))
        before = path.read_bytes()

        _validate(client, str(path))
        client.post("/uploads/purchases", json={"file_path": str(path), "year": 2026, "month": 1})

        assert path.read_bytes() == before

    def test_the_values_land_in_the_canonical_fields(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """거래처명 → company_name · 사업자번호 → business_no 로 들어간다."""
        path = _customer_shaped(tmp_path / "raw.xlsx", count=1)
        response = client.post(
            "/uploads/purchases", json={"file_path": path, "year": 2026, "month": 1}
        )
        assert response.status_code == 200, response.text

        stored = sqlite3.connect(db).execute(
            "SELECT company_name, business_no, resolution_date, issue_date, amount, "
            "contract_date, payment_date, description, budget_account FROM purchase"
        ).fetchall()
        assert stored == [
            ("합성0기업", SYNTHETIC[0], "2026-01-16", "2026-01-01", 110000,
             None, None, "합성 지출 0", "임차료"),
        ]

    def test_the_amount_is_the_total_not_the_supply_price(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⛔ 공급가액을 금액으로 바꿔치기하지 않는다 — 실적 금액은 「계」다."""
        path = _customer_shaped(tmp_path / "raw.xlsx", count=1)
        client.post("/uploads/purchases", json={"file_path": path, "year": 2026, "month": 1})
        amount = sqlite3.connect(db).execute("SELECT amount FROM purchase").fetchone()[0]
        assert amount == 110000  # 공급가액 100,000 이 아니다


# ======================================================================
# 별칭 목록 자체 — 짐작해서 늘리지 않았는가
# ======================================================================
class TestTheAliasTableStaysAtWhatWasActuallySeen:
    def test_only_the_two_measured_aliases_exist(self) -> None:
        assert dict(PURCHASE_HEADER_ALIASES) == {
            "거래처명": "기업명",
            "사업자번호": "사업자등록번호",
        }

    def test_no_guessed_alias_was_added(self) -> None:
        """⛔ 글자가 비슷하다고 짝지어 주지 않는다."""
        for guessed in ("업체명", "상호", "거래처", "업체", "사업자", "공급가액", "세액"):
            assert guessed not in PURCHASE_HEADER_ALIASES, guessed

    def test_a_guessed_header_still_fails(self, client: TestClient, tmp_path: Path) -> None:
        """실제로 확인하지 않은 이름(업체명)은 통과시키지 않는다."""
        headers = tuple("업체명" if h == "거래처명" else h for h in CUSTOMER_HEADERS)
        path = _book(tmp_path / "guessed.xlsx", headers, [])
        assert _validate(client, path)["ok"] is False
