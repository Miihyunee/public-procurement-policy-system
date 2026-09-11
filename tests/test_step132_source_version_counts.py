"""
STEP 132 — 등록 버전의 집계는 **실제로 매인 것**을 센다.

무엇이 문제였나
===============
등록 버전에 적히는 건수가 그 업로드의 **처리 결과**였다.

```
company_count       = created + already_exists
certification_count = 새로 만든 인증 수
```

같은 파일을 다시 올리면 새로 만드는 것이 하나도 없다. 그래서
``certification_count`` 가 **0** 이 되고, 화면에는 「인증 0건」으로 나온다.
데이터는 멀쩡한데 담당자에게는 **등록이 사라진 것처럼 보인다.**

실제 고객 자료에서 그대로 재현됐다(STEP 131).

```
재업로드  created 0 · already_exists 98,664
버전      company_count 98,664 · certification_count 0   ← 화면에 「인증 0건」
매칭      145건 / 1,525,413,644원 (정상)
```

무엇을 바꿨나
=============
집계를 **그 버전에 실제로 매여 있는 레코드**에서 센다.

⛔ ``created + already_exists`` 로 어림하지 않는다 — 그것도 처리 결과이지
저장된 실체가 아니다(지시서 §3).

⛔ 업무규칙·계산·versioning 을 바꾸지 않았다. 세는 기준만 바꿨다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.app import create_app
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.models.certification import Certification

#: ⛔ 실제 고객 자료가 아니다. 검증자릿수를 맞춘 합성 사업자번호.
SYNTHETIC = ("1000000009", "1000000014", "1000000028")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


@pytest.fixture
def listing(tmp_path: Path) -> str:
    """합성 인증 명단 세 곳."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["사업자등록번호", "기업명", "대표자명", "유효시작일", "유효종료일"])
    for index, business_no in enumerate(SYNTHETIC, start=1):
        sheet.append([business_no, f"합성{index}기업", "", "2026-01-01", "2026-12-31"])
    path = tmp_path / "listing.xlsx"
    workbook.save(path)
    return str(path)


def _upload(client: TestClient, path: str) -> dict[str, object]:
    response = client.post("/companies/upload", json={"file_path": path, "policy_code": "WOMAN"})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _woman_source(client: TestClient) -> dict[str, object]:
    body = client.get("/companies/registration").json()
    for item in body["items"]:
        if item["policy_code"] == "WOMAN":
            return dict(item)
    raise AssertionError("여성기업 등록 정보가 없다")


# ======================================================================
# CASE 1 · 2 — 최초 업로드와 동일 파일 재업로드
# ======================================================================
class TestTheVersionCountsWhatIsActuallyAttached:
    def test_case_1_the_first_upload_records_real_counts(
        self, client: TestClient, listing: str
    ) -> None:
        """CASE 1 — 최초 업로드."""
        result = _upload(client, listing)

        assert result["created"] == 3
        assert result["already_exists"] == 0

        source = _woman_source(client)

        assert source["company_count"] == 3
        assert source["certification_count"] == 3

    def test_case_2_the_same_file_again_keeps_the_counts(
        self, client: TestClient, listing: str
    ) -> None:
        """CASE 2 — ⭐ 같은 파일을 다시 올려도 **0 이 되지 않는다.**"""
        _upload(client, listing)
        before = _woman_source(client)

        again = _upload(client, listing)

        assert again["created"] == 0
        assert again["already_exists"] == 3

        after = _woman_source(client)

        assert after["certification_count"] == before["certification_count"] == 3
        assert after["company_count"] == before["company_count"] == 3

    def test_the_counts_are_never_the_processing_result(
        self, client: TestClient, listing: str
    ) -> None:
        """⛔ 처리 결과를 집계로 쓰지 않는다(지시서 §3·§5).

        재업로드에서 ``created`` 는 0 이다. 집계가 처리 결과를 따라갔다면
        여기서 0 이 나온다.
        """
        _upload(client, listing)
        again = _upload(client, listing)
        source = _woman_source(client)

        assert again["created"] == 0
        assert source["certification_count"] != again["created"]
        assert source["certification_count"] == 3


# ======================================================================
# 세는 방법 자체
# ======================================================================
class TestCountingIsDoneAgainstTheStoredRecords:
    def test_it_counts_rows_attached_to_that_version(self, tmp_path: Path) -> None:
        """버전에 매인 인증만 센다 — 다른 버전 것은 세지 않는다."""
        repository = CertificationRepository(str(tmp_path / "t.db"))
        repository.create_table()
        for company_id, source_id in ((1, 7), (2, 7), (3, 8)):
            repository.insert(
                Certification(
                    company_id=company_id,
                    policy_id=1,
                    valid_from=date(2026, 1, 1),
                    valid_to=date(2026, 12, 31),
                    policy_company_source_id=source_id,
                )
            )

        assert repository.count_by_source(7) == (2, 2)
        assert repository.count_by_source(8) == (1, 1)

    def test_one_company_with_two_certifications_counts_once(self, tmp_path: Path) -> None:
        """기업 수는 **서로 다른** 기업의 수다."""
        repository = CertificationRepository(str(tmp_path / "t.db"))
        repository.create_table()
        for valid_from in (date(2025, 1, 1), date(2026, 1, 1)):
            repository.insert(
                Certification(
                    company_id=1,
                    policy_id=1,
                    valid_from=valid_from,
                    valid_to=date(2026, 12, 31),
                    policy_company_source_id=7,
                )
            )

        assert repository.count_by_source(7) == (2, 1)

    def test_an_empty_version_counts_zero(self, tmp_path: Path) -> None:
        """아무것도 매이지 않은 버전은 0 이다 — 그때는 0 이 사실이다."""
        repository = CertificationRepository(str(tmp_path / "t.db"))
        repository.create_table()

        assert repository.count_by_source(99) == (0, 0)


# ======================================================================
# CASE 3 — 재업로드 전후로 계산이 달라지지 않는다
# ======================================================================
class TestNothingAboutTheCalculationChanged:
    def test_case_3_matching_is_the_same_before_and_after(
        self, client: TestClient, db: Path, listing: str, tmp_path: Path
    ) -> None:
        """CASE 3 — ⭐ 재업로드 전후로 매칭 결과가 같다."""
        purchases = Workbook()
        sheet = purchases.active
        sheet.append(
            [
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
        )
        sheet.append(
            ["2026-03-01", None, None, "합성1기업", SYNTHETIC[0], 1000000, "2026-03-01", "", ""]
        )
        path = tmp_path / "purchases.xlsx"
        purchases.save(path)
        upload = client.post("/uploads/purchases", json={"file_path": str(path), "year": 2026})
        assert upload.status_code == 200, upload.text

        _upload(client, listing)
        client.post("/purchases/rematch")
        before = _matched(db)

        _upload(client, listing)
        after = _matched(db)

        assert after == before
        assert before, "합성 자료가 하나도 연결되지 않았다 — 시험이 아무것도 보지 못한다"


def _matched(db: Path) -> set[int]:
    """실적 합산이 쓰는 **그 판정**을 그대로 부른다."""
    from procurement.calculators.procurement_achievement import (
        ProcurementAchievementCalculator,
    )
    from procurement.core.period import PeriodFilter
    from procurement.database.policy_repository import PolicyRepository
    from procurement.database.purchase_repository import PurchaseRepository

    policies = PolicyRepository(db)
    calculator = ProcurementAchievementCalculator(
        PurchaseRepository(db), CertificationRepository(db), policies
    )
    woman = policies.find_by_policy_code("WOMAN")
    assert woman is not None and woman.policy_id is not None
    return calculator.find_matching_purchase_ids(
        woman.policy_id, PeriodFilter.for_year(2026, "resolution_date")
    )
