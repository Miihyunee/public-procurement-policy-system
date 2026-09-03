"""
STEP 69 — 파일 하나를 **끝까지 흘려보냅니다**.

::

    엑셀 → 업로드 → 검증 → 적재 → 배치 → 기업 매칭
         → Calculator(분모·분자) → Dashboard API → 화면

단위 시험이 다 통과해도 **이음매**에서 끊기면 담당자가 보는 숫자가 틀립니다.
이 파일은 그 이음매만 봅니다.

기존 시험과 겹치지 않게
=======================

``tests/test_upload_replace_confirmation.py`` 가 이미 다음을 잠그고 있습니다.

- 최초 업로드 · 409 확인 요구 · ``replace_existing`` 흐름
- 재업로드 시 이전 배치 SUPERSEDED (**삭제되지 않음**)
- 잘못된 파일이 기존 데이터를 파괴하지 않음

⛔ **그것들을 다시 만들지 않았습니다.** 기존 시험은 모두 `find_for_calculation()`
(**저장소 계층**)에서 멈춥니다. 이 파일은 그 **다음 구간** — 기업 매칭 · 분자 ·
``/dashboard/summary`` · 화면 — 을 이어 붙입니다.

.. warning::
    ⛔ **고객 미확정 사항을 확정하지 않습니다.** W-1-2 · W-16 · W-17 · Q5-3 ·
    Q5-8 모두 🔴 미확정이며, 시험 데이터에 임차·교육 같은 적요가 들어가도
    그것으로 실적을 빼거나 구매유형을 정하지 않습니다.

.. note::
    **합성 데이터만 씁니다.** 실제 고객 데이터·실제 사업자등록번호를 쓰지
    않으며, 여기서 나온 숫자는 **실제 고객 실적이 아닙니다.**
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.app import create_app
from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.core.period import PAYMENT_DATE, PeriodFilter
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company
from procurement.models.import_batch import STATUS_ACTIVE, STATUS_SUPERSEDED
from procurement.uploads.format import header_row

IMPORT_URL = "/uploads/purchases"

# 합성 사업자등록번호 — 체크섬이 맞는 값을 씁니다(실제 업체가 아닙니다).
_A = "220-81-62517"
_B = "119-81-02316"

#: 배치 A — 3행 / 합계 6,000
ROWS_A: list[list[object]] = [
    [
        date(2026, 1, 10),
        date(2026, 1, 5),
        date(2026, 1, 20),
        "합성 A기업",
        _A,
        1000,
        date(2026, 1, 20),
        "일반 구매 A",
        "일반운영비",
    ],
    [
        date(2026, 2, 10),
        date(2026, 2, 5),
        date(2026, 2, 20),
        "합성 B기업",
        _B,
        2000,
        date(2026, 2, 20),
        "일반 구매 B",
        "일반운영비",
    ],
    [
        date(2026, 3, 10),
        date(2026, 3, 5),
        date(2026, 3, 20),
        "합성 C기업",
        _B,
        3000,
        date(2026, 3, 20),
        "일반 구매 C",
        "일반운영비",
    ],
]

#: 배치 B — 2행 / 합계 9,000. 같은 기간(2026)을 다시 올립니다.
ROWS_B: list[list[object]] = [
    [
        date(2026, 4, 10),
        date(2026, 4, 5),
        date(2026, 4, 20),
        "합성 D기업",
        _A,
        4000,
        date(2026, 4, 20),
        "재업로드 구매 D",
        "일반운영비",
    ],
    [
        date(2026, 5, 10),
        date(2026, 5, 5),
        date(2026, 5, 20),
        "합성 E기업",
        _B,
        5000,
        date(2026, 5, 20),
        "재업로드 구매 E",
        "일반운영비",
    ],
]

#: 날짜·사업자번호·금액이 모두 잘못된 행.
BAD_ROW: list[object] = [
    "2026.13.45",
    date(2026, 2, 20),
    date(2026, 4, 1),
    None,
    "999",
    "abc",
    date(2026, 3, 10),
    "",
    "",
]


def _excel(path: Path, rows: list[list[object]]) -> Path:
    """표준 머리글 + 주어진 행으로 엑셀을 만듭니다."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(list(header_row()))
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


def _upload(client: TestClient, path: Path, year: int = 2026, **extra: object) -> httpx.Response:
    payload: dict[str, object] = {"file_path": str(path), "year": year}
    payload.update(extra)
    response: httpx.Response = client.post(IMPORT_URL, json=payload)
    return response


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """정책 seed 까지 끝난 빈 DB."""
    path = tmp_path / "e2e.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    """기간 기준일을 **지급일**로 준 앱.

    ⚠️ 이는 시험을 돌리기 위한 주입이며, **D-24 를 확정한 것이 아닙니다.**
    기본값은 여전히 없습니다.
    """
    return TestClient(create_app(db_path, period_date_field=PAYMENT_DATE))


@pytest.fixture
def calculator(db_path: Path) -> ProcurementAchievementCalculator:
    return ProcurementAchievementCalculator(
        PurchaseRepository(db_path),
        CertificationRepository(db_path),
        PolicyRepository(db_path),
    )


def _register_certified_company(db_path: Path, business_no: str, name: str) -> int:
    """중소기업 인증을 가진 기업을 등록합니다(합성)."""
    company = CompanyRepository(db_path).insert(
        Company(business_no=business_no, company_name=name, representative_name="홍길동")
    )
    assert company.company_id is not None
    policy = PolicyRepository(db_path).find_by_policy_code("SMALL_BUSINESS")
    assert policy is not None and policy.policy_id is not None
    CertificationRepository(db_path).insert(
        Certification(
            company_id=company.company_id,
            policy_id=policy.policy_id,
            valid_from=date(2020, 1, 1),
            valid_to=date(2030, 12, 31),
        )
    )
    return company.company_id


def _register_target(db_path: Path, rate: Decimal, year: int = 2026) -> None:
    """중소기업 목표비율을 **그 연도에** 등록합니다.

    ⚠️ STEP 93 — 목표비율의 정본은 연도별 값이다(DECISIONS §0.20).
    ``Policy.target_rate`` 는 하위호환으로 남아 있을 뿐 계산에 쓰이지 않는다.
    ⛔ 기대값은 바뀌지 않았다 — 값을 **어디에 두는지**만 바뀌었다.
    """
    policy = PolicyRepository(db_path).find_by_policy_code("SMALL_BUSINESS")
    assert policy is not None
    assert policy.policy_id is not None
    PolicyTargetRepository(db_path).upsert(year, policy.policy_id, rate)


def _register_company_data(db_path: Path, policy_code: str = "SMALL_BUSINESS") -> None:
    """정책의 기업 목록을 **받았다는 사실**만 기록합니다(STEP 96 §8).

    ⛔ 기업·인증을 만들지 않습니다 — 목록은 받았지만 우리 거래처가 한 곳도
    없는 상태이며, 그것은 "모른다" 가 아니라 **"전부 미해당"** 입니다.
    """
    from procurement.database.policy_company_source_repository import (
        PolicyCompanySourceRepository,
    )

    policy = PolicyRepository(db_path).find_by_policy_code(policy_code)
    assert policy is not None
    assert policy.policy_id is not None
    PolicyCompanySourceRepository(db_path).record(
        policy.policy_id, source="FILE", company_count=0, certification_count=0
    )


def _summary(client: TestClient, year: int = 2026) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"/dashboard/summary?year={year}").json()
    return body


def _active_total(db_path: Path) -> Decimal:
    """ACTIVE 배치(+배치 없음)의 금액 합계 — 저장소가 보는 진실."""
    return sum(
        (row.amount for row in PurchaseRepository(db_path).find_for_calculation()),
        Decimal("0"),
    )


# ======================================================================
# 1. 정상 업로드 → 적재 → 매칭 → 계산 → API
# ======================================================================
class TestUploadToCalculation:
    """⭐ 파일 한 개가 화면 숫자가 되기까지 끊기지 않는다."""

    @pytest.fixture
    def uploaded(self, client: TestClient, tmp_path: Path) -> dict[str, Any]:
        body: dict[str, Any] = _upload(client, _excel(tmp_path / "a.xlsx", ROWS_A)).json()
        return body

    def test_upload_succeeds(self, uploaded: dict[str, Any]) -> None:
        assert uploaded["stored"] is True

    def test_every_source_row_is_stored(self, uploaded: dict[str, Any]) -> None:
        assert uploaded["total_rows"] == 3
        assert uploaded["stored_rows"] == 3
        assert uploaded["rejected_rows"] == 0

    def test_a_batch_was_created_and_is_active(
        self, uploaded: dict[str, Any], db_path: Path
    ) -> None:
        batches = ImportBatchRepository(db_path).find_all()
        assert len(batches) == 1
        assert batches[0].status == STATUS_ACTIVE
        assert batches[0].batch_id == uploaded["batch_id"]

    def test_rows_landed_in_purchase(self, uploaded: dict[str, Any], db_path: Path) -> None:
        assert PurchaseRepository(db_path).count() == 3

    def test_calculation_target_is_everything(
        self, uploaded: dict[str, Any], db_path: Path
    ) -> None:
        assert _active_total(db_path) == Decimal("6000")

    def test_calculator_denominator_matches_the_repository(
        self, uploaded: dict[str, Any], db_path: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """이음매 ① — 저장소가 세는 것과 계산기가 세는 것이 같다."""
        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        assert calculator.calculate_total_purchase(period) == _active_total(db_path)

    def test_api_denominator_matches_the_calculator(
        self,
        uploaded: dict[str, Any],
        client: TestClient,
        calculator: ProcurementAchievementCalculator,
    ) -> None:
        """⭐ **불변식 4** — 계산기 분모 == API 가 내려주는 분모."""
        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        body = _summary(client)
        assert Decimal(body["total_purchase_amount"]) == calculator.calculate_total_purchase(period)
        assert Decimal(body["total_purchase_amount"]) == Decimal("6000")


class TestMatchingReachesTheNumerator:
    """업로드된 행이 **기업 매칭을 거쳐 분자**에 닿는다."""

    def test_unmatched_rows_are_in_the_denominator_only(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """기업정보가 없으면 **분모에만** 들어간다 — 정상 흐름이다.

        ⚠️ **STEP 96 — 설정 보완.** 기업정보를 받지 못한 정책은 이제 **조회불가**
        이며 금액이 ``null`` 이다(STEP 96 §8). 이 시험이 보려는 것은 "분자에
        없다" 이므로, 목록을 **받았다는 사실**을 먼저 등록해 둔다. 그래야 0 이
        "모른다" 가 아니라 **"미해당"** 을 뜻한다.
        ⛔ 기대값은 바뀌지 않았다.
        """
        _upload(client, _excel(tmp_path / "a.xlsx", ROWS_A))
        _register_target(db_path, Decimal("30"))
        _register_company_data(db_path)

        small = next(
            p for p in _summary(client)["policies"] if p["policy_code"] == "SMALL_BUSINESS"
        )
        assert Decimal(small["total_purchase_amount"]) == Decimal("6000")  # 분모에는 있다
        assert Decimal(small["purchase_amount"]) == Decimal("0")  # 분자에는 없다
        assert all(row.company_id is None for row in PurchaseRepository(db_path).find_all())

    def test_registering_a_company_moves_amounts_into_the_numerator(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⭐ 이음매 ② — 기업 등록 → 재매칭 → 분자에 반영된다.

        구매가 먼저 들어오고 기업정보가 나중에 확보되는 것이 이 시스템의
        정상 흐름이다(경우 B).

        ⚠️ **STEP 96 — 설정 보완.** 목록을 받았다는 사실을 먼저 기록해, 처음의
        0 이 "모른다"(조회불가) 가 아니라 **"미해당"** 을 뜻하게 한다(§8).
        ⛔ 기대값은 바뀌지 않았다.
        """
        _upload(client, _excel(tmp_path / "a.xlsx", ROWS_A))
        _register_target(db_path, Decimal("30"))
        _register_company_data(db_path)
        before = next(
            p for p in _summary(client)["policies"] if p["policy_code"] == "SMALL_BUSINESS"
        )
        assert Decimal(before["purchase_amount"]) == Decimal("0")

        # 사업자번호 _B 로 들어온 두 건(2,000 + 3,000)의 기업을 등록한다.
        _register_certified_company(db_path, "1198102316", "합성 B기업")
        assert client.post("/purchases/rematch").status_code == 200

        body = _summary(client)
        small = next(p for p in body["policies"] if p["policy_code"] == "SMALL_BUSINESS")
        assert Decimal(small["purchase_amount"]) == Decimal("5000")
        assert Decimal(small["total_purchase_amount"]) == Decimal("6000")

    def test_company_name_differences_do_not_split_the_match(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⭐ 사업자번호가 같으면 거래처명이 달라도 한 기업으로 연결된다.

        업로드 파일의 `합성 B기업` · `합성 C기업` 은 **이름이 다르지만 같은
        사업자번호**다. STEP 64 에서 고객이 확정한 기준 그대로다.
        """
        _upload(client, _excel(tmp_path / "a.xlsx", ROWS_A))
        company_id = _register_certified_company(db_path, "1198102316", "전혀 다른 이름")
        client.post("/purchases/rematch")

        matched = [
            row for row in PurchaseRepository(db_path).find_all() if row.company_id == company_id
        ]
        assert sorted(row.company_name for row in matched) == ["합성 B기업", "합성 C기업"]
        assert sum(row.amount for row in matched) == Decimal("5000")

    def test_a_different_business_no_is_a_different_company(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """사업자번호가 다르면 연결되지 않는다."""
        _upload(client, _excel(tmp_path / "a.xlsx", ROWS_A))
        company_id = _register_certified_company(db_path, "1198102316", "합성 B기업")
        client.post("/purchases/rematch")

        unmatched = [
            row for row in PurchaseRepository(db_path).find_all() if row.business_no == "2208162517"
        ]
        assert all(row.company_id != company_id for row in unmatched)
        assert all(row.company_id is None for row in unmatched)


# ======================================================================
# 2. 동일 기간 재업로드 — API 까지
# ======================================================================
class TestReplacementReachesTheApi:
    """⭐ **이 STEP 의 핵심.** 재업로드해도 합산되지 않는다 — 화면까지.

    .. note::
        저장소 계층까지는 ``test_upload_replace_confirmation.py`` 가 이미
        잠그고 있습니다. 여기서는 그 결과가 **API 숫자**에 그대로 도달하는지만
        확인합니다.
    """

    @pytest.fixture
    def replaced(self, client: TestClient, db_path: Path, tmp_path: Path) -> None:
        _upload(client, _excel(tmp_path / "a.xlsx", ROWS_A))
        _register_certified_company(db_path, "1198102316", "합성 B기업")
        _register_target(db_path, Decimal("30"))
        client.post("/purchases/rematch")
        # 같은 기간을 다시 올린다 — 확인 플래그를 붙여야 교체된다.
        assert (
            _upload(client, _excel(tmp_path / "b.xlsx", ROWS_B), replace_existing=True).status_code
            == 200
        )
        client.post("/purchases/rematch")

    def test_before_replacement_the_api_shows_the_first_batch(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _upload(client, _excel(tmp_path / "a.xlsx", ROWS_A))
        assert Decimal(_summary(client)["total_purchase_amount"]) == Decimal("6000")

    def test_api_shows_only_the_new_batch(self, replaced: None, client: TestClient) -> None:
        """⭐ **불변식 1** — 6,000 + 9,000 = 15,000 이 되지 않는다."""
        total = Decimal(_summary(client)["total_purchase_amount"])
        assert total == Decimal("9000")
        assert total != Decimal("15000")

    def test_old_rows_are_still_in_the_table(self, replaced: None, db_path: Path) -> None:
        """⭐ **불변식 2** — 이전 배치의 행은 지워지지 않는다."""
        assert PurchaseRepository(db_path).count() == 5  # 3 + 2

    def test_batch_statuses(self, replaced: None, db_path: Path) -> None:
        statuses = sorted(b.status for b in ImportBatchRepository(db_path).find_all())
        assert statuses == [STATUS_ACTIVE, STATUS_SUPERSEDED]

    def test_the_three_layers_agree_after_replacement(
        self,
        replaced: None,
        db_path: Path,
        client: TestClient,
        calculator: ProcurementAchievementCalculator,
    ) -> None:
        """⭐ **불변식 4** — 저장소 · 계산기 · API 가 같은 숫자를 말한다."""
        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        assert _active_total(db_path) == Decimal("9000")
        assert calculator.calculate_total_purchase(period) == Decimal("9000")
        assert Decimal(_summary(client)["total_purchase_amount"]) == Decimal("9000")

    def test_the_numerator_follows_the_new_batch_too(
        self, replaced: None, client: TestClient
    ) -> None:
        """분자도 새 배치만 본다 — 배치 B 에서 _B 사업자번호는 5,000."""
        small = next(
            p for p in _summary(client)["policies"] if p["policy_code"] == "SMALL_BUSINESS"
        )
        assert Decimal(small["purchase_amount"]) == Decimal("5000")
        assert Decimal(small["total_purchase_amount"]) == Decimal("9000")

    def test_loaded_total_still_counts_everything(self, replaced: None, client: TestClient) -> None:
        """적재 합계는 **전부**를 센다 — 분모와 다른 숫자다(STEP 68).

        화면이 둘을 다른 이름으로 부르는 이유가 여기서 실제로 확인된다.
        """
        status: Any = client.get("/dashboard/data-status?year=2026").json()
        assert Decimal(status["purchase_total_amount"]) == Decimal("15000")
        assert Decimal(_summary(client)["total_purchase_amount"]) == Decimal("9000")


# ======================================================================
# 3. 실패한 업로드가 기존 숫자를 건드리지 않는다 — API 까지
# ======================================================================
class TestFailedUploadKeepsTheApiNumbers:
    """⭐ **불변식 3** — 잘못된 재업로드 뒤에도 화면 숫자가 그대로다.

    .. note::
        저장소 계층은 ``test_upload_replace_confirmation.py`` 가 잠급니다.
        여기서는 **API 응답이 통째로 같은지**만 봅니다.
    """

    @pytest.fixture
    def seeded(self, client: TestClient, db_path: Path, tmp_path: Path) -> None:
        _upload(client, _excel(tmp_path / "a.xlsx", ROWS_A))
        _register_certified_company(db_path, "1198102316", "합성 B기업")
        _register_target(db_path, Decimal("30"))
        client.post("/purchases/rematch")

    def test_bad_upload_is_rejected(self, seeded: None, client: TestClient, tmp_path: Path) -> None:
        response = _upload(client, _excel(tmp_path / "bad.xlsx", [BAD_ROW]), replace_existing=True)
        assert response.json()["stored"] is False

    def test_summary_is_byte_for_byte_the_same(
        self, seeded: None, client: TestClient, tmp_path: Path
    ) -> None:
        """⭐ 응답 전체가 같다 — 분모·분자·달성률 어느 것도 흔들리지 않는다."""
        before = _summary(client)
        _upload(client, _excel(tmp_path / "bad.xlsx", [BAD_ROW]), replace_existing=True)
        assert _summary(client) == before

    def test_nothing_was_partially_stored(
        self, seeded: None, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⛔ "일부 행만 들어가는" 일이 없다 — 전부 검증 → 전부 저장."""
        before = PurchaseRepository(db_path).count()
        _upload(
            client,
            _excel(tmp_path / "mixed.xlsx", [ROWS_B[0], BAD_ROW]),
            replace_existing=True,
        )
        assert PurchaseRepository(db_path).count() == before

    def test_the_old_batch_is_still_active(
        self, seeded: None, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        _upload(client, _excel(tmp_path / "bad.xlsx", [BAD_ROW]), replace_existing=True)
        batches = ImportBatchRepository(db_path).find_all()
        assert [b.status for b in batches] == [STATUS_ACTIVE]

    def test_a_missing_column_is_also_rejected(
        self, seeded: None, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """필수 항목이 없는 파일도 저장되지 않는다(다른 오류 유형)."""
        path = tmp_path / "no-column.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None
        headers = [h for h in header_row() if h != "결의일자"]
        sheet.append(headers)
        sheet.append(
            [date(2026, 1, 5), date(2026, 1, 20), "합성", _A, 1000, date(2026, 1, 20), "", ""]
        )
        workbook.save(path)
        workbook.close()

        before = PurchaseRepository(db_path).count()
        response = _upload(client, path, replace_existing=True)
        assert response.json()["stored"] is False
        assert PurchaseRepository(db_path).count() == before


# ======================================================================
# 4. 미적재 행이 추적된다
# ======================================================================
class TestRejectionTraceEndToEnd:
    """0 이하 금액 행이 **적재되지 않고 사유와 함께 남는다.**

    .. warning::
        ⛔ 이것을 "실적 제외 규칙이 확정되었다" 로 읽지 않습니다. Q5-8 은
        🔴 미확정이며, 여기서 확인하는 것은 **현재 적재 거부 동작**뿐입니다.
    """

    @pytest.fixture
    def uploaded(self, client: TestClient, tmp_path: Path) -> dict[str, Any]:
        rows: list[list[object]] = [
            ROWS_A[0],  # 정상 1,000
            [
                date(2026, 2, 10),
                date(2026, 2, 5),
                date(2026, 2, 20),
                "합성 Z기업",
                _B,
                0,
                date(2026, 2, 20),
                "0원 행",
                "일반운영비",
            ],
            [
                date(2026, 3, 10),
                date(2026, 3, 5),
                date(2026, 3, 20),
                "합성 Y기업",
                _B,
                -500,
                date(2026, 3, 20),
                "음수 행",
                "일반운영비",
            ],
        ]
        body: dict[str, Any] = _upload(client, _excel(tmp_path / "mixed.xlsx", rows)).json()
        return body

    def test_only_the_positive_row_is_stored(self, uploaded: dict[str, Any], db_path: Path) -> None:
        assert uploaded["stored"] is True
        assert uploaded["total_rows"] == 3
        assert uploaded["stored_rows"] == 1
        assert uploaded["rejected_rows"] == 2
        assert PurchaseRepository(db_path).count() == 1

    def test_every_source_row_is_accounted_for(self, uploaded: dict[str, Any]) -> None:
        """⭐ 원본 = 적재 + 미적재. 설명되지 않는 행이 없다."""
        assert uploaded["stored_rows"] + uploaded["rejected_rows"] == uploaded["total_rows"]
        assert uploaded["unexplained_rows"] == 0

    def test_the_trace_api_shows_them(self, uploaded: dict[str, Any], client: TestClient) -> None:
        body: Any = client.get("/imports/trace").json()
        assert body["stored"] == 1
        assert body["rejected"] == 2

    def test_rejections_carry_the_source_row_number(
        self, uploaded: dict[str, Any], client: TestClient
    ) -> None:
        """원본 몇 번째 행인지 남는다 — 담당자가 원본과 대조할 수 있다."""
        body: Any = client.get("/imports/rejections").json()
        rows = body["items"]
        assert len(rows) == 2
        # 원본 행 번호가 그대로 남는다(0원 행 · 음수 행이 몇 번째였는지).
        assert sorted(row["row_number"] for row in rows) == [2, 3]

    def test_rejections_carry_a_reason(self, uploaded: dict[str, Any], client: TestClient) -> None:
        body: Any = client.get("/imports/rejections").json()
        for row in body["items"]:
            assert row["reason"]
            assert row["reason_label"]

    def test_the_csv_carries_the_same_rows(
        self, uploaded: dict[str, Any], client: TestClient
    ) -> None:
        text = client.get("/imports/trace.csv").text
        assert text.startswith("﻿")
        assert "0원 행" in text
        assert "음수 행" in text
        assert "일반 구매 A" not in text  # 적재된 행은 미적재 CSV 에 없다

    def test_the_denominator_only_has_the_stored_row(
        self, uploaded: dict[str, Any], client: TestClient
    ) -> None:
        assert Decimal(_summary(client)["total_purchase_amount"]) == Decimal("1000")


# ======================================================================
# 5. 기간 필터가 업로드된 데이터에 그대로 걸린다
# ======================================================================
class TestPeriodEndToEnd:
    """업로드한 데이터로 기간 경계를 확인한다(계산 규칙은 STEP 67 과 동일)."""

    @pytest.fixture
    def uploaded(self, client: TestClient, db_path: Path, tmp_path: Path) -> None:
        _upload(client, _excel(tmp_path / "b.xlsx", ROWS_B))
        _register_certified_company(db_path, "1198102316", "합성 E기업")
        _register_target(db_path, Decimal("30"))
        client.post("/purchases/rematch")

    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            (date(2026, 4, 1), date(2026, 4, 30), "4000"),  # 4월만
            (date(2026, 5, 1), date(2026, 5, 31), "5000"),  # 5월만
            (date(2026, 4, 20), date(2026, 5, 20), "9000"),  # 양 끝 당일 포함
            (date(2026, 4, 21), date(2026, 5, 19), "0"),  # 하루씩 좁히면 둘 다 빠짐
            (date(2026, 1, 1), date(2026, 3, 31), "0"),  # 기간 밖
        ],
    )
    def test_period_boundaries(
        self,
        uploaded: None,
        calculator: ProcurementAchievementCalculator,
        start: date,
        end: date,
        expected: str,
    ) -> None:
        period = PeriodFilter(start=start, end=end, date_field=PAYMENT_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal(expected)

    def test_same_period_applies_to_the_numerator(
        self, uploaded: None, calculator: ProcurementAchievementCalculator, db_path: Path
    ) -> None:
        """⭐ **불변식 5** — 분모와 분자에 같은 기간이 걸린다."""
        policy = PolicyRepository(db_path).find_by_policy_code("SMALL_BUSINESS")
        assert policy is not None and policy.policy_id is not None
        period = PeriodFilter(
            start=date(2026, 5, 1), end=date(2026, 5, 31), date_field=PAYMENT_DATE
        )

        assert calculator.calculate_total_purchase(period) == Decimal("5000")
        assert calculator.calculate_policy_purchase(policy.policy_id, period) == Decimal("5000")

    def test_no_period_sums_the_active_batch(
        self, uploaded: None, calculator: ProcurementAchievementCalculator
    ) -> None:
        assert calculator.calculate_total_purchase(None) == Decimal("9000")

    def test_api_year_scoping(self, uploaded: None, client: TestClient) -> None:
        assert Decimal(_summary(client, 2026)["total_purchase_amount"]) == Decimal("9000")
        assert Decimal(_summary(client, 2025)["total_purchase_amount"]) == Decimal("0")


# ======================================================================
# 6. 화면까지
# ======================================================================
class TestScreenReceivesTheNumbers:
    """화면이 이 숫자들을 받아 그릴 수 있는 형태로 내려온다."""

    @pytest.fixture
    def uploaded(self, client: TestClient, db_path: Path, tmp_path: Path) -> None:
        _upload(client, _excel(tmp_path / "a.xlsx", ROWS_A))
        _register_certified_company(db_path, "1198102316", "합성 B기업")
        _register_target(db_path, Decimal("30"))
        client.post("/purchases/rematch")

    def test_the_page_is_served(self, uploaded: None, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert "우선구매 정책 달성률 대시보드" in response.text

    def test_status_endpoint_carries_both_totals(self, uploaded: None, client: TestClient) -> None:
        """화면이 "적재된 구매금액 합계" 와 "계산 대상 구매" 를 함께 받는다."""
        status: Any = client.get("/dashboard/data-status?year=2026").json()
        assert Decimal(status["purchase_total_amount"]) == Decimal("6000")
        assert status["purchase_count"] == 3
        assert status["calculation_target_count"] == 3

    def test_summary_carries_what_the_policy_card_needs(
        self, uploaded: None, client: TestClient
    ) -> None:
        """정책 카드가 그리는 네 값이 모두 응답에 있다(STEP 68)."""
        small = next(
            p for p in _summary(client)["policies"] if p["policy_code"] == "SMALL_BUSINESS"
        )
        for field in (
            "target_rate",
            "purchase_amount",
            "total_purchase_amount",
            "achievement_rate",
        ):
            assert small[field] is not None, field

    def test_the_ratio_is_readable_on_the_card(self, uploaded: None, client: TestClient) -> None:
        """⭐ 분자 ÷ 분모 = 구매비율, ÷ 목표율 = 달성률 — 카드 안에서 읽힌다."""
        small = next(
            p for p in _summary(client)["policies"] if p["policy_code"] == "SMALL_BUSINESS"
        )
        numerator = Decimal(small["purchase_amount"])
        denominator = Decimal(small["total_purchase_amount"])
        assert numerator == Decimal("5000")
        assert denominator == Decimal("6000")
        # 5,000 / 6,000 = 83.33% ÷ 목표 30% → 277.7…%
        assert Decimal(small["achievement_rate"]) > Decimal("100")

    def test_period_notice_is_present(self, uploaded: None, client: TestClient) -> None:
        status: Any = client.get("/dashboard/data-status?year=2026").json()
        assert status["period_date_field"] == PAYMENT_DATE
        assert status["requested_year"] == 2026


# ======================================================================
# 7. 미확정 업무규칙은 이 흐름 어디에서도 적용되지 않는다
# ======================================================================
class TestUnconfirmedRulesStayOut:
    """⛔ 업로드 경로가 고객 미확정 규칙을 몰래 적용하지 않는다."""

    def test_lease_and_education_rows_are_stored_and_counted(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """W-16 · W-17 미확정 — 그런 적요라도 그대로 적재되고 분모에 든다.

        ⛔ 이것이 "실적에 넣기로 확정했다" 는 뜻이 아니다. 아직 **아무 규칙도
        없다**는 사실을 적을 뿐이며, 고객이 답하면 이 시험이 바뀐다.
        """
        rows: list[list[object]] = [
            [
                date(2026, 1, 10),
                date(2026, 1, 5),
                date(2026, 1, 20),
                "합성 렌터카",
                _A,
                1000,
                date(2026, 1, 20),
                "출장 차량 1일 렌트",
                "임차료",
            ],
            [
                date(2026, 2, 10),
                date(2026, 2, 5),
                date(2026, 2, 20),
                "합성 교육원",
                _B,
                2000,
                date(2026, 2, 20),
                "민원 담당자 교육(교육비, 임차료, 다과비)",
                "행사운영비",
            ],
        ]
        body = _upload(client, _excel(tmp_path / "unconfirmed.xlsx", rows)).json()

        assert body["stored_rows"] == 2
        assert body["rejected_rows"] == 0
        assert Decimal(_summary(client)["total_purchase_amount"]) == Decimal("3000")

    def test_purchase_type_is_not_decided_on_import(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⛔ 적재가 구매유형을 정하지 않는다 — 담당자가 정한다."""
        _upload(client, _excel(tmp_path / "a.xlsx", ROWS_A))
        listing: Any = client.get("/reviews").json()
        for item in listing["items"]:
            assert item["review"]["final_purchase_type"] is None
            assert item["review"]["status"] == "PENDING"
