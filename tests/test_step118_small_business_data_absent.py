"""
STEP 116 — 중소기업: **자료가 없다**. 그래서 조회불가다.

무엇을 지키는가
===============
중소기업 정책은 지금 「조회불가」다. 이 파일은 그 상태가 **결함이 아니라
올바른 결과**임을 고정하고, 자료가 오면 그대로 계산이 되는지를 합성 데이터로
확인한다.

🟢 2026-09-02 · PM 확정 (``DECISIONS.md`` §0.19)

    "중소기업 데이터에서 인증유효일자 값이 있는데, 거기가 빈값이 아니면
    중소기업인 거야."

즉 판정 근거는 **중소기업 자료의 유효일자**이며, ⛔ **업체규모 칸이 아니다.**

⛔ 업체규모로 중소기업을 판정하지 않는다
========================================
「대기업 591 · 중기업 200 · 소기업 136 · `N(소상공인)` 23 …」 같은 규모 값을
중소기업 인증으로 바꾸지 않는다. 규모 칸과 「중소기업 기간」 칸이 서로 다른
이야기를 한 전례도 있다(``CERTIFICATION_SOURCE_ANALYSIS.md`` §3.3 — 기간이 있는
733행 중 80행은 규모가 대기업·중견기업이었다).

그래서 업로드 표준 양식에는 ``업체규모`` 컬럼 자체가 **없다**
(``uploads/company_format.py`` 의 ``COMPANY_PENDING_COLUMNS``). 아래
:class:`TestSizeIsNeverTheBasis` 가 그것을 고정한다.

실제 데이터에서는 어떠했는가 (§2 계측 · 2026-09-05)
===================================================

============================================  ==========================
고객이 주신 파일에서 「중소」·「규모」 머리글    **0건** (9개 파일 전 시트)
중소기업 ``policy_company_source``             **0행**
중소기업 ``certification``                     **0행**
현재 지출 원본의 컬럼                          10개 — ``업체규모`` **없음**
============================================  ==========================

→ 중소기업 실제 데이터 미등록 → **조회불가**. ⛔ 0% 로도 미해당으로도 바꾸지
않는다.

.. note::
    아래 「자료가 오면」 시험들은 **합성 데이터**다. 실제 고객 결과가 아니며,
    실제 중소기업 실적을 대신하지 않는다(지시서 §12).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from procurement.__main__ import main
from procurement.app import create_app
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.policy_company_source_repository import (
    PolicyCompanySourceRepository,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.uploads.company_format import COMPANY_PENDING_COLUMNS
from procurement.uploads.format import header_row

#: 합성 사업자등록번호 — ⛔ 실제 고객 값이 아니다. 체크섬만 맞춘 값이다.
_LISTED = "1000000009"  # 중소기업 목록에 든 합성 업체
_UNLISTED = "1000000014"  # 목록에 없는 합성 업체


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "step116.db"
    init_db(path)
    seed_policies(path)
    assert main(["targets", "--year", "2026", "--db", str(path)]) == 0
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _policy_id(db: Path, code: str) -> int:
    policy = PolicyRepository(db).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _won(value: object) -> Decimal:
    """금액은 API 가 **문자열**로 준다 — 자릿수를 잃지 않으려고 그렇게 둔 값이다."""
    return Decimal(str(value))


def _company_file(
    path: Path,
    rows: list[list[object]],
    *,
    headers: tuple[str, ...] = (
        "사업자등록번호",
        "기업명",
        "대표자명",
        "유효시작일",
        "유효종료일",
    ),
) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(list(headers))
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def _purchase_file(path: Path, rows: list[list[object]]) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(list(header_row()))
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def _purchase_row(*, day: str, amount: int, business_no: str) -> list[object]:
    values: dict[str, object] = {
        "결의일자": day,
        "계약일자": day,
        "지급일": day,
        "기업명": "합성업체",
        "사업자등록번호": business_no,
        "계": amount,
        "신고기준일": day,
        "적요": "합성 거래",
        "예산과목": "일반수용비",
    }
    return [values[header] for header in header_row()]


def _upload_purchases(
    client: TestClient, path: Path, *, year: int = 2026, month: int | None = None
) -> httpx.Response:
    response: httpx.Response = client.post(
        "/uploads/purchases",
        json={"file_path": str(path), "year": year, "month": month},
    )
    return response


def _upload_companies(client: TestClient, path: Path, *, policy_code: str) -> httpx.Response:
    response: httpx.Response = client.post(
        "/companies/upload", json={"file_path": str(path), "policy_code": policy_code}
    )
    return response


def _policy_row(client: TestClient, code: str, year: int = 2026) -> dict[str, Any]:
    payload = client.get("/dashboard/summary", params={"year": year}).json()
    return dict(next(row for row in payload["policies"] if row["policy_code"] == code))


# ======================================================================
# §3 · §13-1  업체규모는 판정 근거가 아니다
# ======================================================================
class TestSizeIsNeverTheBasis:
    """⛔ 규모 칸으로 중소기업을 만들지 않는다."""

    def test_1_the_upload_form_has_no_size_column(self) -> None:
        """표준 양식에 ``업체규모`` 칸이 **없다** — 있으면 채워지고, 채워지면 해석된다."""
        assert "업체규모" in COMPANY_PENDING_COLUMNS
        assert COMPANY_PENDING_COLUMNS["업체규모"] == "중소기업 여부를 규모로 판정하는 규칙이 없다"

        from procurement.uploads.company_format import COMPANY_REQUIRED_HEADERS

        assert "업체규모" not in COMPANY_REQUIRED_HEADERS

    def test_2_no_code_turns_a_size_into_a_certification(self) -> None:
        """소스에 규모 값을 **다루는 문자열**이 없다.

        ⛔ 「대기업」·「중기업」·「소상공인」 같은 값이 **코드가 쓰는 문자열**로
        등장하면, 그 순간 규모를 읽는 규칙이 생긴다.

        .. note::
            설명글(docstring)·주석은 세지 않는다 — 「⛔ 규모는 보지 않는다」 라고
            **적어 둔 것**까지 위반으로 잡으면 금지 표기를 지워야 하게 된다.
            그래서 :mod:`ast` 로 파싱해 **docstring 이 아닌 문자열 상수**만 본다.

            ``업체규모`` 하나는 예외다 — ``COMPANY_PENDING_COLUMNS`` 의 키로,
            「이 칸은 양식에 넣지 않는다」 는 **금지 목록**이기 때문이다
            (:meth:`test_1_the_upload_form_has_no_size_column` 이 그 뜻을 고정한다).
        """
        import ast

        #: 규모를 나타내는 값. ⛔ "중소기업" 은 정책 이름이므로 넣지 않는다
        #: (그리고 "소기업" 은 그 부분문자열이라 단독으로 쓸 수 없다).
        sizes = ("대기업", "중견기업", "소상공인", "중기업(간주기업)")
        allowed = {("uploads/company_format.py", "업체규모")}

        offenders: list[str] = []
        for path in (Path("src") / "procurement").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = {
                ast.get_docstring(node, clean=False)
                for node in ast.walk(tree)
                if isinstance(
                    node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
                )
            }
            relative = path.relative_to(Path("src") / "procurement").as_posix()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if node.value in docstrings:
                    continue
                for word in (*sizes, "업체규모"):
                    if word in node.value and (relative, word) not in allowed:
                        offenders.append(f"{relative}:{node.lineno}: {node.value[:60]!r}")
        assert offenders == []

    def test_3_a_size_column_in_the_file_is_not_read(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """파일에 규모 칸이 섞여 와도 인증이 되지 않는다 — 유효일자만 본다."""
        path = _company_file(
            tmp_path / "with_size.xlsx",
            [[_LISTED, "합성업체", "가나다", "2026-01-01", "2026-12-31", "중소기업"]],
            headers=(
                "사업자등록번호",
                "기업명",
                "대표자명",
                "유효시작일",
                "유효종료일",
                "업체규모",
            ),
        )
        response = _upload_companies(client, path, policy_code="SMALL_BUSINESS")
        assert response.status_code == 200, response.text

        certifications = CertificationRepository(db).find_by_policy(
            _policy_id(db, "SMALL_BUSINESS")
        )
        # 인증이 생겼다면 그 근거는 **유효일자**이지 규모 칸이 아니다.
        for row in certifications:
            assert row.valid_from == date(2026, 1, 1)
            assert row.valid_to == date(2026, 12, 31)


# ======================================================================
# §7 · §13-5 · §13-12  자료가 없으면 조회불가다
# ======================================================================
class TestNoDataMeansUnknown:
    def test_4_an_unregistered_policy_is_unknown_not_zero(self, client: TestClient) -> None:
        """⭐ 실제 데이터가 놓인 자리 — 등록 기록이 없으면 「조회불가」다."""
        row = _policy_row(client, "SMALL_BUSINESS")

        assert row["status"] == "COMPANY_DATA_NOT_REGISTERED"
        assert row["purchase_amount"] is None
        assert row["achievement_rate"] is None

    def test_5_spending_alone_does_not_create_a_numerator(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """지출만 올려도 분자가 생기지 않는다 — ⛔ 없는 자료를 만들어 계산하지 않는다."""
        path = _purchase_file(
            tmp_path / "spend.xlsx",
            [_purchase_row(day="2026-03-05", amount=1_000_000, business_no=_LISTED)],
        )
        assert _upload_purchases(client, path).status_code == 200

        row = _policy_row(client, "SMALL_BUSINESS")
        assert row["status"] == "COMPANY_DATA_NOT_REGISTERED"
        assert row["purchase_amount"] is None

    def test_6_the_source_and_certification_tables_stay_empty(self, db: Path) -> None:
        """⛔ 자료가 없는데 소스·인증 행을 만들어 두지 않는다."""
        policy_id = _policy_id(db, "SMALL_BUSINESS")

        assert PolicyCompanySourceRepository(db).find_versions(policy_id) == []
        assert CertificationRepository(db).find_by_policy(policy_id) == []


# ======================================================================
# §5 · §6 · §13-2·3·4·6  자료가 오면 그대로 계산된다 (⚠️ 합성 데이터)
# ======================================================================
class TestWhenTheDataArrives:
    """⚠️ 여기부터는 **합성 데이터**다 — 실제 중소기업 실적이 아니다."""

    @pytest.fixture
    def registered(self, client: TestClient, tmp_path: Path) -> TestClient:
        companies = _company_file(
            tmp_path / "sme.xlsx",
            [[_LISTED, "합성업체", "가나다", "2026-01-01", "2026-12-31"]],
        )
        assert _upload_companies(client, companies, policy_code="SMALL_BUSINESS").status_code == 200
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [
                _purchase_row(day="2026-03-05", amount=6_000_000, business_no=_LISTED),
                _purchase_row(day="2026-03-06", amount=4_000_000, business_no=_UNLISTED),
            ],
        )
        assert _upload_purchases(client, spend).status_code == 200
        client.post("/purchases/rematch")
        return client

    def test_7_the_exact_business_number_is_the_only_key(self, registered: TestClient) -> None:
        """사업자등록번호 exact match 만 센다 — 목록에 없는 업체는 미해당(0원)이다."""
        row = _policy_row(registered, "SMALL_BUSINESS")

        assert _won(row["purchase_amount"]) == 6_000_000
        assert _won(row["total_purchase_amount"]) == 10_000_000

    def test_8_the_target_is_fifty_percent(self, registered: TestClient) -> None:
        """목표 50% · 구매비율 60% → 달성률 120%."""
        row = _policy_row(registered, "SMALL_BUSINESS")

        assert _won(row["target_rate"]) == 50
        assert _won(row["achievement_rate"]) == Decimal("120.00")

    def test_9_the_resolution_date_decides_the_year(
        self, db: Path, client: TestClient, tmp_path: Path
    ) -> None:
        """결의일자 기준이다 — ⛔ 계약일자·지급일·신고기준일이 아니다(STEP 86).

        같은 거래에 두 날짜를 서로 다른 해로 넣고, **결의일자가 가리키는 해**로만
        잡히는지 본다.
        """
        assert main(["targets", "--year", "2027", "--db", str(db)]) == 0
        companies = _company_file(
            tmp_path / "sme.xlsx",
            [[_LISTED, "합성업체", "가나다", "2026-01-01", "2027-12-31"]],
        )
        assert _upload_companies(client, companies, policy_code="SMALL_BUSINESS").status_code == 200

        row = _purchase_row(day="2026-11-20", amount=5_000_000, business_no=_LISTED)
        headers = list(header_row())
        row[headers.index("결의일자")] = "2027-01-10"  # ⭐ 결의일자만 다음 해
        spend = _purchase_file(tmp_path / "spend.xlsx", [row])
        assert _upload_purchases(client, spend, year=2027).status_code == 200
        client.post("/purchases/rematch")

        assert (
            _won(_policy_row(client, "SMALL_BUSINESS", year=2027)["purchase_amount"]) == 5_000_000
        )
        assert _policy_row(client, "SMALL_BUSINESS", year=2026)["purchase_amount"] in ("0", 0)

    def test_10_an_out_of_window_purchase_is_excluded(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """유효기간 밖 거래는 실적에서 빠진다 — 그리고 분모에는 남는다."""
        companies = _company_file(
            tmp_path / "sme.xlsx",
            [[_LISTED, "합성업체", "가나다", "2026-06-01", "2026-12-31"]],
        )
        assert _upload_companies(client, companies, policy_code="SMALL_BUSINESS").status_code == 200
        spend = _purchase_file(
            tmp_path / "spend.xlsx",
            [
                _purchase_row(day="2026-03-05", amount=1_000_000, business_no=_LISTED),
                _purchase_row(day="2026-07-05", amount=3_000_000, business_no=_LISTED),
            ],
        )
        assert _upload_purchases(client, spend).status_code == 200
        client.post("/purchases/rematch")

        row = _policy_row(client, "SMALL_BUSINESS")
        assert _won(row["purchase_amount"]) == 3_000_000  # 3월분은 기간 외
        assert _won(row["total_purchase_amount"]) == 4_000_000


# ======================================================================
# §10 · §11 · §12 · §13-7·8·9·10·11  다른 것을 건드리지 않는가
# ======================================================================
class TestItTouchesNothingElse:
    @pytest.fixture
    def registered(self, client: TestClient, tmp_path: Path) -> TestClient:
        companies = _company_file(
            tmp_path / "sme.xlsx",
            [[_LISTED, "합성업체", "가나다", "2026-01-01", "2026-12-31"]],
        )
        assert _upload_companies(client, companies, policy_code="SMALL_BUSINESS").status_code == 200
        return client

    def test_11_fifty_percent_belongs_to_small_business_alone(self, db: Path) -> None:
        """목표 50% 는 중소기업 한 곳뿐이다 — ⛔ 다른 정책으로 번지지 않는다."""
        code_of = {
            policy.policy_id: policy.policy_code for policy in PolicyRepository(db).find_all()
        }
        targets = PolicyTargetRepository(db).list_by_year(2026)

        fifty = sorted(
            code_of[row.policy_id] for row in targets if row.target_rate == Decimal("50")
        )
        assert fifty == ["SMALL_BUSINESS"]

    def test_12_registering_small_business_leaves_other_policies_alone(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """중소기업 등록이 다른 정책의 상태를 바꾸지 않는다."""
        before = client.get("/dashboard/summary", params={"year": 2026}).json()["policies"]

        companies = _company_file(
            tmp_path / "sme.xlsx",
            [[_LISTED, "합성업체", "가나다", "2026-01-01", "2026-12-31"]],
        )
        assert _upload_companies(client, companies, policy_code="SMALL_BUSINESS").status_code == 200

        after = client.get("/dashboard/summary", params={"year": 2026}).json()["policies"]
        for was in before:
            if was["policy_code"] == "SMALL_BUSINESS":
                continue
            now = next(row for row in after if row["policy_code"] == was["policy_code"])
            assert now == was

    def test_13_the_same_file_does_not_bump_the_version(
        self, db: Path, registered: TestClient, tmp_path: Path
    ) -> None:
        """STEP 114 멱등 — 같은 파일을 다시 올려도 버전이 늘지 않는다.

        .. note::
            같음의 기준은 **파일 내용의 SHA-256** 이다. 그래서 엑셀에서 같은
            목록을 **다시 내보낸** 파일은 행이 같아도 바이트가 달라 새 버전이
            된다(엑셀이 저장 시각 등을 파일에 넣기 때문이다). 목록이 그대로면
            새 버전이 활성이 되어도 계산 결과는 같다.
        """
        policy_id = _policy_id(db, "SMALL_BUSINESS")
        sources = PolicyCompanySourceRepository(db)
        assert len(sources.find_versions(policy_id)) == 1

        same = tmp_path / "sme.xlsx"  # ⭐ registered 픽스처가 올린 **바로 그 파일**
        assert same.exists()
        assert _upload_companies(registered, same, policy_code="SMALL_BUSINESS").status_code == 200

        versions = sources.find_versions(policy_id)
        assert len(versions) == 1
        assert [row.is_active for row in versions] == [True]

    def test_14_different_content_makes_a_new_active_version(
        self, db: Path, registered: TestClient, tmp_path: Path
    ) -> None:
        """내용이 다르면 새 버전이 활성이 되고 이전 버전은 남는다."""
        policy_id = _policy_id(db, "SMALL_BUSINESS")
        changed = _company_file(
            tmp_path / "sme_v2.xlsx",
            [
                [_LISTED, "합성업체", "가나다", "2026-01-01", "2026-12-31"],
                [_UNLISTED, "합성상사", "라마바", "2026-01-01", "2026-12-31"],
            ],
        )
        assert (
            _upload_companies(registered, changed, policy_code="SMALL_BUSINESS").status_code == 200
        )

        versions = PolicyCompanySourceRepository(db).find_versions(policy_id)
        assert [row.version for row in versions] == [1, 2]
        assert [row.is_active for row in versions] == [False, True]

    def test_15_the_monthly_accumulation_survives(
        self, registered: TestClient, tmp_path: Path
    ) -> None:
        """STEP 113 — 인증 등록이 월별 지출 누적을 깨지 않는다."""
        january = _purchase_file(
            tmp_path / "jan.xlsx",
            [_purchase_row(day="2026-01-15", amount=1_000_000, business_no=_LISTED)],
        )
        assert _upload_purchases(registered, january, month=1).status_code == 200

        # 달 사이에 인증 자료를 한 번 더 올린다 — 지출과 수명이 다른 자료다.
        companies = _company_file(
            tmp_path / "sme_v2.xlsx",
            [
                [_LISTED, "합성업체", "가나다", "2026-01-01", "2026-12-31"],
                [_UNLISTED, "합성상사", "라마바", "2026-01-01", "2026-12-31"],
            ],
        )
        assert (
            _upload_companies(registered, companies, policy_code="SMALL_BUSINESS").status_code
            == 200
        )

        february = _purchase_file(
            tmp_path / "feb.xlsx",
            [_purchase_row(day="2026-02-15", amount=2_000_000, business_no=_LISTED)],
        )
        assert _upload_purchases(registered, february, month=2).status_code == 200

        payload = registered.get("/dashboard/summary", params={"year": 2026}).json()
        assert _won(payload["total_purchase_amount"]) == 3_000_000  # 1월이 사라지지 않았다
