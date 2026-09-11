"""
결의일자 미기재 구매 목록 **CSV 내려받기**를 고정합니다(STEP 61).

무엇을 지키는 시험인가
======================

STEP 59 는 숫자를, STEP 60 은 행 목록을 화면에 올렸습니다. STEP 61 은 그
**같은 목록**을 파일로 내려받게 합니다. 담당자가 업무 확인·고객 확인에 쓰기
위해서입니다.

가장 중요한 두 가지는 다음과 같습니다.

1. **화면 목록과 파일이 같은 대상**이어야 합니다. 행 수는 목록 API 의
   ``count`` 와, 금액 합계는 ``amount`` 와 정확히 같아야 합니다. 어긋나면
   담당자는 화면에서 보던 것과 다른 파일을 들고 나가게 되는데, 그 어긋남은
   눈에 보이지 않습니다.
2. **빈 결의일자가 빈 칸으로 남아야** 합니다. 비어 있다는 사실이 이 파일의
   존재 이유이므로, 다른 날짜로 채우면 파일 자체가 거짓이 됩니다.

.. warning::
    ⛔ **처리 규칙을 정하지 않습니다.** 이 행들은 "오류"·"무효"·"실적 불인정"
    이 아니라 **결의일자가 입력되지 않은 구매**일 뿐입니다.

.. warning::
    ⛔ **달성률을 바꾸지 않습니다.** :class:`TestAchievementUnchangedByCsv` 가
    내려받기 전후의 요약이 완전히 같음을 잠급니다.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.core.period import PAYMENT_DATE, RESOLUTION_DATE
from procurement.dashboard.missing_resolution_export import EXPORT_COLUMNS, export_lines
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Purchase
from procurement.models.import_batch import STATUS_ACTIVE, STATUS_SUPERSEDED, ImportBatch

# 합성 사업자번호입니다 — STEP 59~60 과 같은 값을 씁니다(실제 업체가 아닙니다).
CERTIFIED_NO = "1000000001"
PLAIN_NO = "1000000002"

CSV_URL = "/dashboard/missing-resolution-date.csv?year=2026"
LIST_URL = "/dashboard/missing-resolution-date?year=2026"
SUMMARY_URL = "/dashboard/summary?year=2026"


def _purchase(
    business_no: str,
    amount: str,
    *,
    resolution: date | None,
    company_id: int | None = None,
    batch_id: int | None = None,
    description: str | None = "사무용품 구매",
    budget_account: str | None = "일반운영비",
) -> Purchase:
    return Purchase(
        business_no=business_no,
        company_name="테스트업체",
        contract_date=date(2026, 1, 10),
        payment_date=date(2026, 2, 10),
        resolution_date=resolution,
        issue_date=date(2026, 2, 5),
        description=description,
        budget_account=budget_account,
        amount=Decimal(amount),
        company_id=company_id,
        batch_id=batch_id,
    )


def _batch(status: str) -> ImportBatch:
    return ImportBatch(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        file_name="synthetic.xlsx",
        row_count=1,
        status=status,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """정책·인증기업만 준비된 빈 DB. 구매 행은 각 시험이 직접 넣습니다."""
    path = tmp_path / "missing_resolution_csv.db"
    init_db(path)
    seed_policies(path)

    policies = PolicyRepository(path)
    policy = policies.find_by_policy_code("SMALL_BUSINESS")
    assert policy is not None and policy.policy_id is not None
    policies.update_target_rate("SMALL_BUSINESS", Decimal("30"))

    company = CompanyRepository(path).insert(
        Company(business_no=CERTIFIED_NO, company_name="가나상사", representative_name="홍길동")
    )
    assert company.company_id is not None
    CertificationRepository(path).insert(
        Certification(
            company_id=company.company_id,
            policy_id=policy.policy_id,
            valid_from=date(2020, 1, 1),
            valid_to=date(2030, 12, 31),
        )
    )
    return path


@pytest.fixture
def certified_company_id(db_path: Path) -> int:
    company = CompanyRepository(db_path).find_by_business_no(CERTIFIED_NO)
    assert company is not None and company.company_id is not None
    return company.company_id


def _client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path, period_date_field=RESOLUTION_DATE))


def _rows(body: str) -> list[list[str]]:
    """CSV 본문을 표로 읽습니다(BOM 제거). 첫 줄은 머리글입니다."""
    return list(csv.reader(io.StringIO(body.lstrip("﻿"))))


# ----------------------------------------------------------------------
# 내보내기 계층 — 대상과 값
# ----------------------------------------------------------------------
class TestExportContent:
    """무엇이 실리고 무엇이 실리지 않는가."""

    def test_header_only_when_empty(self) -> None:
        rows = _rows("".join(export_lines([])))
        assert rows == [list(EXPORT_COLUMNS)]

    def test_header_columns(self) -> None:
        assert EXPORT_COLUMNS == (
            "구매ID",
            "적요",
            "거래처명",
            "사업자번호",
            "계",
            "결의일자",
            "예산과목",
        )

    def test_absent_columns_are_not_invented(self) -> None:
        """⛔ DB 에 없는 값을 지어내지 않는다(DECISIONS §0.7.5)."""
        for absent in ("원본 행 번호", "공급가액", "세액"):
            assert absent not in EXPORT_COLUMNS

    def test_resolution_date_stays_blank(self, db_path: Path) -> None:
        """⭐ 빈 결의일자는 **빈 칸**이다 — 다른 날짜로 채우지 않는다."""
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "500000", resolution=None))
        rows = _rows("".join(export_lines(purchases.find_missing_resolution_date())))
        assert rows[1][EXPORT_COLUMNS.index("결의일자")] == ""

    def test_no_other_date_substituted(self, db_path: Path) -> None:
        """⛔ 신고기준일·지급일·계약일 값이 파일 어디에도 새어 나오지 않는다."""
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "500000", resolution=None))
        body = "".join(export_lines(purchases.find_missing_resolution_date()))
        for leaked in ("2026-02-05", "2026-02-10", "2026-01-10"):
            assert leaked not in body

    def test_values_preserved(self, db_path: Path) -> None:
        purchases = PurchaseRepository(db_path)
        stored = purchases.insert(_purchase(PLAIN_NO, "500000", resolution=None))
        row = _rows("".join(export_lines(purchases.find_missing_resolution_date())))[1]
        assert row == [
            str(stored.purchase_id),
            "사무용품 구매",
            "테스트업체",
            PLAIN_NO,
            "500000",
            "",
            "일반운영비",
        ]

    def test_amount_keeps_decimals(self, db_path: Path) -> None:
        """금액은 지수 표기 없이 소수점까지 그대로 나간다."""
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "1234567.89", resolution=None))
        row = _rows("".join(export_lines(purchases.find_missing_resolution_date())))[1]
        assert row[EXPORT_COLUMNS.index("계")] == "1234567.89"

    def test_formula_injection_guarded(self, db_path: Path) -> None:
        """적요는 사람이 입력한 자유 문자열이라 수식으로 읽히면 안 된다."""
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "100", resolution=None, description="=1+1"))
        row = _rows("".join(export_lines(purchases.find_missing_resolution_date())))[1]
        assert row[EXPORT_COLUMNS.index("적요")] == "'=1+1"

    def test_empty_optional_fields(self, db_path: Path) -> None:
        """적요·예산과목이 없어도 빈 칸으로 나간다(다른 값으로 채우지 않음)."""
        purchases = PurchaseRepository(db_path)
        purchases.insert(
            _purchase(PLAIN_NO, "100", resolution=None, description=None, budget_account=None)
        )
        row = _rows("".join(export_lines(purchases.find_missing_resolution_date())))[1]
        assert row[EXPORT_COLUMNS.index("적요")] == ""
        assert row[EXPORT_COLUMNS.index("예산과목")] == ""


class TestExportFormat:
    """규약은 기존 CSV 두 개와 **같다** — 새 규칙을 만들지 않았다."""

    def test_utf8_bom(self) -> None:
        assert "".join(export_lines([])).startswith("﻿")

    def test_crlf(self) -> None:
        assert "".join(export_lines([])).endswith("\r\n")

    def test_streams_line_by_line(self, db_path: Path) -> None:
        """전체를 메모리에 쌓지 않는다 — 머리글 + 행마다 한 조각."""
        purchases = PurchaseRepository(db_path)
        for amount in ("10", "20", "30"):
            purchases.insert(_purchase(PLAIN_NO, amount, resolution=None))
        chunks = list(export_lines(purchases.find_missing_resolution_date()))
        assert len(chunks) == 4


# ----------------------------------------------------------------------
# 대상 선정 — STEP 60 과 같은 조건
# ----------------------------------------------------------------------
class TestTargetRows:
    """CSV 대상은 목록과 **같은 조회**를 쓴다(별도 SQL 을 복사하지 않았다)."""

    def test_only_null_resolution_date(self, db_path: Path, certified_company_id: int) -> None:
        purchases = PurchaseRepository(db_path)
        purchases.insert(
            _purchase(
                CERTIFIED_NO, "300", resolution=date(2026, 1, 5), company_id=certified_company_id
            )
        )
        purchases.insert(_purchase(PLAIN_NO, "5000", resolution=None))
        rows = _rows(_client(db_path).get(CSV_URL).text)
        assert len(rows) == 2  # 머리글 + 1행
        assert rows[1][EXPORT_COLUMNS.index("계")] == "5000"

    def test_normal_rows_never_mixed_in(self, db_path: Path) -> None:
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "999", resolution=date(2026, 3, 1)))
        body = _client(db_path).get(CSV_URL).text
        assert "999" not in body

    def test_superseded_batch_rows_excluded(self, db_path: Path) -> None:
        """대체된 배치의 행은 빠진다 — 계산 대상과 같은 배치 조건이다."""
        batches = ImportBatchRepository(db_path)
        active = batches.insert(_batch(STATUS_ACTIVE))
        superseded = batches.insert(_batch(STATUS_SUPERSEDED))
        assert active.batch_id is not None and superseded.batch_id is not None

        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "100", resolution=None, batch_id=active.batch_id))
        purchases.insert(_purchase(PLAIN_NO, "900", resolution=None, batch_id=superseded.batch_id))

        rows = _rows(_client(db_path).get(CSV_URL).text)
        assert [row[EXPORT_COLUMNS.index("계")] for row in rows[1:]] == ["100"]

    def test_batchless_rows_included(self, db_path: Path) -> None:
        """배치 이전에 적재된 행(batch_id NULL)은 기존 규칙대로 포함된다."""
        PurchaseRepository(db_path).insert(
            _purchase(PLAIN_NO, "100", resolution=None, batch_id=None)
        )
        assert len(_rows(_client(db_path).get(CSV_URL).text)) == 2

    def test_year_does_not_filter_rows(self, db_path: Path) -> None:
        """⛔ 어느 연도로 내려받아도 대상이 달라지지 않는다."""
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "5000", resolution=None))
        client = _client(db_path)
        for year in (2020, 2026, 2030):
            body = client.get(f"/dashboard/missing-resolution-date.csv?year={year}").text
            assert len(_rows(body)) == 2, f"{year} 년 내려받기에서 대상이 달라졌습니다"

    def test_does_not_modify_rows(self, db_path: Path) -> None:
        """⛔ 내려받기가 원본을 바꾸지 않는다(UPDATE·INSERT·DELETE 없음)."""
        purchases = PurchaseRepository(db_path)
        stored = purchases.insert(_purchase(PLAIN_NO, "5000", resolution=None))
        assert stored.purchase_id is not None
        before = purchases.find_all()

        _client(db_path).get(CSV_URL)

        after = purchases.find_all()
        assert len(after) == len(before)
        again = purchases.find_by_id(stored.purchase_id)
        assert again is not None
        assert again.resolution_date is None
        assert again.payment_date == stored.payment_date
        assert again.amount == stored.amount
        assert again.updated_at == stored.updated_at


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------
class TestCsvApi:
    """``GET /dashboard/missing-resolution-date.csv``."""

    @pytest.fixture
    def client(self, db_path: Path, certified_company_id: int) -> TestClient:
        purchases = PurchaseRepository(db_path)
        purchases.insert(
            _purchase(
                CERTIFIED_NO, "300", resolution=date(2026, 1, 5), company_id=certified_company_id
            )
        )
        purchases.insert(_purchase(PLAIN_NO, "700", resolution=date(2026, 3, 1)))
        purchases.insert(_purchase(PLAIN_NO, "5000", resolution=None))
        purchases.insert(_purchase(PLAIN_NO, "150.50", resolution=None))
        return _client(db_path)

    def test_ok(self, client: TestClient) -> None:
        assert client.get(CSV_URL).status_code == 200

    def test_content_type(self, client: TestClient) -> None:
        """기존 CSV 응답과 **같은** Content-Type 이다."""
        assert client.get(CSV_URL).headers["content-type"].startswith("text/csv; charset=utf-8")

    def test_file_name(self, client: TestClient) -> None:
        disposition = client.get(CSV_URL).headers["content-disposition"]
        assert 'filename="missing-resolution-date.csv"' in disposition
        assert disposition.startswith("attachment")

    def test_header_row(self, client: TestClient) -> None:
        assert _rows(client.get(CSV_URL).text)[0] == list(EXPORT_COLUMNS)

    def test_row_count_matches_list_api(self, client: TestClient) -> None:
        """⭐ CSV 행 수 = 목록 API 의 ``count``."""
        rows = _rows(client.get(CSV_URL).text)
        assert len(rows) - 1 == client.get(LIST_URL).json()["count"]

    def test_amount_sum_matches_list_api(self, client: TestClient) -> None:
        """⭐ CSV 의 ``계`` 합계 = 목록 API 의 ``amount``."""
        rows = _rows(client.get(CSV_URL).text)[1:]
        total = sum((Decimal(row[EXPORT_COLUMNS.index("계")]) for row in rows), Decimal("0"))
        assert total == Decimal(client.get(LIST_URL).json()["amount"])

    def test_row_count_matches_summary(self, client: TestClient) -> None:
        """화면 안내(요약)와도 어긋나지 않는다."""
        rows = _rows(client.get(CSV_URL).text)
        summary = client.get(SUMMARY_URL).json()["missing_resolution_date"]
        assert len(rows) - 1 == summary["count"]

    def test_same_purchase_ids_as_list_api(self, client: TestClient) -> None:
        """같은 행이 같은 순서로 나온다."""
        csv_ids = [row[0] for row in _rows(client.get(CSV_URL).text)[1:]]
        list_ids = [str(item["purchase_id"]) for item in client.get(LIST_URL).json()["items"]]
        assert csv_ids == list_ids

    def test_resolution_date_blank(self, client: TestClient) -> None:
        rows = _rows(client.get(CSV_URL).text)[1:]
        assert all(row[EXPORT_COLUMNS.index("결의일자")] == "" for row in rows)

    def test_empty_gives_header_only(self, db_path: Path) -> None:
        """0건이면 머리글만 있는 CSV — 오류가 아니다."""
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "700", resolution=date(2026, 3, 1)))
        rows = _rows(_client(db_path).get(CSV_URL).text)
        assert rows == [list(EXPORT_COLUMNS)]

    def test_empty_on_payment_date_basis(self, db_path: Path) -> None:
        """지급일 기준 조회에서는 대상이 없다(목록 API 와 동일)."""
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "5000", resolution=None))
        client = TestClient(create_app(db_path, period_date_field=PAYMENT_DATE))
        assert _rows(client.get(CSV_URL).text) == [list(EXPORT_COLUMNS)]

    def test_year_required(self, client: TestClient) -> None:
        """연도를 생략하면 400 — 목록·요약과 **같은 규칙**(D-27)."""
        assert client.get("/dashboard/missing-resolution-date.csv").status_code == 400

    def test_503_without_date_field(self, db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """기준일 미설정은 503 — ⛔ 빈 CSV 를 성공으로 돌려주지 않는다."""
        # 🟢 STEP 86 — 기본값이 결의일자이므로 '비어 있는 상태' 를 명시적으로 만든다.
        from procurement.core.config import settings

        monkeypatch.setattr(settings, "PURCHASE_PERIOD_DATE_FIELD", None)
        client = TestClient(create_app(db_path, period_date_field=None))
        response = client.get(CSV_URL)
        assert response.status_code == 503
        assert "구매ID" not in response.text

    def test_write_methods_rejected(self, client: TestClient) -> None:
        """⛔ 이 경로에 쓰기 메서드를 만들지 않았다."""
        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)("/dashboard/missing-resolution-date.csv")
            assert response.status_code == 405, f"{method.upper()} 가 열려 있습니다"


# ----------------------------------------------------------------------
# 달성률 불변
# ----------------------------------------------------------------------
class TestAchievementUnchangedByCsv:
    """⭐ 내려받기 전후로 달성률이 달라지지 않는다."""

    @pytest.fixture
    def client(self, db_path: Path, certified_company_id: int) -> TestClient:
        purchases = PurchaseRepository(db_path)
        purchases.insert(
            _purchase(
                CERTIFIED_NO, "300", resolution=date(2026, 1, 5), company_id=certified_company_id
            )
        )
        purchases.insert(_purchase(PLAIN_NO, "700", resolution=date(2026, 3, 1)))
        purchases.insert(_purchase(PLAIN_NO, "5000", resolution=None))
        return _client(db_path)

    def test_summary_identical_before_and_after(self, client: TestClient) -> None:
        before = client.get(SUMMARY_URL).json()
        client.get(CSV_URL)
        after = client.get(SUMMARY_URL).json()
        assert after == before

    def test_csv_amount_not_in_denominator(self, client: TestClient) -> None:
        """CSV 대상 금액(5,000)이 분모(1,000)에 섞이지 않는다."""
        rows = _rows(client.get(CSV_URL).text)[1:]
        total = sum((Decimal(row[EXPORT_COLUMNS.index("계")]) for row in rows), Decimal("0"))
        assert total == Decimal("5000")
        assert Decimal(client.get(SUMMARY_URL).json()["total_purchase_amount"]) == Decimal("1000")

    def test_achievement_rates_unchanged(self, client: TestClient) -> None:
        before = [p["achievement_rate"] for p in client.get(SUMMARY_URL).json()["policies"]]
        client.get(CSV_URL)
        after = [p["achievement_rate"] for p in client.get(SUMMARY_URL).json()["policies"]]
        assert after == before

    def test_repeated_downloads_are_stable(self, client: TestClient) -> None:
        """여러 번 내려받아도 같은 파일이다(부작용 없음)."""
        first = client.get(CSV_URL).text
        second = client.get(CSV_URL).text
        assert first == second


# ----------------------------------------------------------------------
# 화면
# ----------------------------------------------------------------------
class TestScreen:
    """목록이 보일 때만 내려받기 링크가 보인다."""

    @pytest.fixture
    def page(self) -> str:
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "procurement"
            / "web"
            / "static"
            / "index.html"
        )
        return path.read_text(encoding="utf-8")

    def test_link_exists(self, page: str) -> None:
        assert 'id="missing-resolution-csv"' in page

    def test_inside_fold(self, page: str) -> None:
        """목록이 표시되지 않으면 링크도 보이지 않는다.

        접기(``<details id="missing-resolution-fold">``) **안에** 있으므로,
        0건이거나 결의일자 기준 조회가 아니면 접기 자체가 숨겨지면서 링크도
        함께 사라집니다 — 표시 조건을 두 곳에 두지 않기 위해서입니다.
        """
        fold = page.index('id="missing-resolution-fold"')
        link = page.index('id="missing-resolution-csv"')
        assert fold < link < page.index("</details>", fold)

    def test_calls_csv_endpoint(self, page: str) -> None:
        assert "/dashboard/missing-resolution-date.csv?year=" in page

    def test_file_name_attribute(self, page: str) -> None:
        assert 'download="missing-resolution-date.csv"' in page

    def test_uses_download_link_not_form(self, page: str) -> None:
        """⛔ 데이터를 바꾸는 조작이 아니다 — 기존 CSV 와 같은 내려받기 링크다."""
        start = page.index('id="missing-resolution-csv"')
        markup = page[start : page.index("</a>", start)]
        assert "<button" not in markup
        assert "onclick" not in markup

    def test_no_confirmation_modal(self, page: str) -> None:
        """⛔ 확인창을 두지 않는다(수정 조작이 아니므로)."""
        start = page.index('id="missing-resolution-csv"')
        markup = page[start : page.index("</a>", start)]
        assert "modal" not in markup

    def test_hidden_when_zero(self, page: str) -> None:
        """0건이면 STEP 59 원칙대로 접기 전체가 숨겨진다."""
        start = page.index("function renderMissingResolutionDate")
        body = page[start : page.index("function loadMissingResolutionRows", start)]
        assert "fold.hidden = true" in body
