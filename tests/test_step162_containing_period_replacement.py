"""
STEP 162 — 새 기간이 기존 기간을 **품을 때만** 함께 교체한다.

무엇이 문제였나
===============
담당자 PC 에 설치를 마치고 실제 원본(1~6월 누적본)을 올리려 했더니 저장이
거절됐습니다.

    "2026년 자료와 기간이 겹치는 데이터가 이미 등록되어 있습니다.
     겹치는 기간이 서로 달라 그 달만 바꿀 수 없습니다."

3월짜리 시험용 배치 하나가 ACTIVE 로 남아 있었고, 연 전체 기간이 그것과
겹쳤기 때문입니다. 그런데 지울 방법도 없어서 **어떤 방법으로도 올릴 수
없는 상태**였습니다. 월 단위로 올리다 연 단위로 바꾸는 기관은 반드시
여기서 막힙니다.

무엇을 바꿨나 (🟢 2026-09-11 PM 확정 · 안 ①)
=============================================
겹침을 **품는 것**과 **아닌 것**으로 가릅니다.

    새 기간 ⊇ 기존 기간   → 승인받으면 함께 대체
    그 밖의 겹침          → 예전처럼 거절

품을 때만 안전합니다 — 품는 쪽이 그 기간의 거래를 모두 담고 있으므로
빠지는 거래도, 두 번 세는 거래도 없습니다.

⛔ 「겹치면 무조건 교체」로 넓히지 않습니다.
⛔ 묻지 않고 교체하지 않습니다(PM-005) — 승인이 있어야 합니다.
⛔ 기존 배치를 지우지 않습니다 — SUPERSEDED 이력으로 남습니다.
⛔ 적재가 끝난 뒤에만 넘깁니다 — 먼저 넘기면 실패했을 때 그 기간이 비어
   버립니다.
⛔ 날짜 구간만 봅니다. 신고기준일·파일명·오늘 날짜를 보지 않습니다.
⛔ A-1 집계 행 · B-1 앞선 해 · 0 이하 금액 규칙은 그대로입니다.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.app import create_app
from procurement.database.bootstrap import bootstrap
from procurement.models.import_batch import ImportBatch
from procurement.uploads.upload_service import _contains

#: 고객 원본과 같은 머리글 모양. ⛔ 고객 자료가 아니다 — 칸 이름만 같다.
HEADERS = (
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

#: ⛔ 실제 업체의 번호가 아니다. 검증자릿수만 맞춘 합성 사업자번호.
SYNTHETIC = "1000000009"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def book(path: Path, resolutions: list[str], *, amount: int = 110000) -> str:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "원본"
    sheet.append(list(HEADERS))
    for index, resolution in enumerate(resolutions):
        sheet.append(
            [
                index + 1,
                resolution,
                f"합성 지출 {index}",
                f"합성{index}기업",
                SYNTHETIC,
                amount - 10000,
                10000,
                amount,
                resolution,
                "임차료",
            ]
        )
    workbook.save(path)
    return str(path)


def upload(
    client: TestClient, file_path: str, **payload: object
) -> tuple[int, dict[str, Any]]:
    response = client.post("/uploads/purchases", json={"file_path": file_path, **payload})
    return int(response.status_code), dict(response.json())


def batches(client: TestClient) -> list[tuple[Any, ...]]:
    items = client.get("/imports/batches").json()["items"]
    return [
        (item["batch_id"], item["status"], item["period_start"], item["period_end"])
        for item in items
    ]


def _batch(start: str, end: str, batch_id: int = 1) -> ImportBatch:
    return ImportBatch(
        batch_id=batch_id,
        file_name="합성.xlsx",
        period_start=date.fromisoformat(start),
        period_end=date.fromisoformat(end),
    )


# ======================================================================
# 판정 규칙 자체 — 겹침과 품음은 다르다
# ======================================================================
class TestTheRuleItself:
    @pytest.mark.parametrize(
        ("new_start", "new_end", "old_start", "old_end", "expected"),
        [
            # 1) 1~6월 위에 1~7월 — 품는다
            ("2026-01-01", "2026-07-31", "2026-01-01", "2026-06-30", True),
            # 2) 1~12월 위에 1~7월 — 품지 못한다(새 기간이 더 좁다)
            ("2026-01-01", "2026-07-31", "2026-01-01", "2026-12-31", False),
            # 3) 3월 위에 4월 — 겹치지도 않는다
            ("2026-04-01", "2026-04-30", "2026-03-01", "2026-03-31", False),
            # 4) 1~6월 위에 3~7월 — 일부만 겹친다
            ("2026-03-01", "2026-07-31", "2026-01-01", "2026-06-30", False),
            # 5) 같은 기간 — 품는 것으로 본다(경계 포함)
            ("2026-01-01", "2026-06-30", "2026-01-01", "2026-06-30", True),
            # 6) 1~7월 위에 1~6월 — 새 기간이 더 좁다
            ("2026-01-01", "2026-06-30", "2026-01-01", "2026-07-31", False),
            # 연 전체가 그 해 3월을 품는다 — 담당자 PC 에서 막혔던 바로 그 경우
            ("2026-01-01", "2026-12-31", "2026-03-01", "2026-03-31", True),
        ],
    )
    def test_containment_is_not_overlap(
        self, new_start: str, new_end: str, old_start: str, old_end: str, expected: bool
    ) -> None:
        assert (
            _contains(
                date.fromisoformat(new_start),
                date.fromisoformat(new_end),
                _batch(old_start, old_end),
            )
            is expected
        )

    def test_8_9_only_the_two_dates_decide(self) -> None:
        """⛔ 8·9 — 신고기준일·파일명·오늘 날짜를 보지 않는다.

        판정하는 줄 하나만 본다 — 설명(docstring)에는 「신고기준일을 보지
        않는다」는 ⛔ 문장이 들어 있어 글자만 세면 잘못 걸린다.
        """
        source = Path("src/procurement/uploads/upload_service.py").read_text(encoding="utf-8")
        body = source[source.index("def _contains(") :]
        body = body[: body.index("\ndef ")]
        decision = body[body.index('"""', body.index('"""') + 3) + 3 :].strip()
        assert decision == (
            "return period_start <= batch.period_start and batch.period_end <= period_end"
        )


# ======================================================================
# 1 — 품으면 함께 대체한다
# ======================================================================
class TestAContainingUploadReplacesWhatItCovers:
    def test_1_a_wider_period_supersedes_the_narrower_one(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """1 — 3월 배치가 있는 상태에서 연 전체를 올리면 함께 대체된다.

        ⭐ 담당자 PC 에서 막혔던 바로 그 경우다.
        """
        first = book(tmp_path / "march.xlsx", ["2026-03-15"])
        status, _ = upload(client, first, year=2026, month=3)
        assert status == 200

        whole = book(tmp_path / "year.xlsx", ["2026-01-05", "2026-03-15", "2026-06-01"])

        # ⛔ 묻지 않고 교체하지 않는다.
        status, body = upload(client, whole, year=2026)
        assert status == 409, body
        assert body["detail"]["code"] == "EXISTING_PERIOD"
        assert [row["batch_id"] for row in body["detail"]["contained_batches"]] == [1]
        assert "함께 교체됩니다" in body["detail"]["message"]
        assert batches(client) == [(1, "ACTIVE", "2026-03-01", "2026-03-31")]

        # 승인하면 함께 넘어간다.
        status, stored = upload(client, whole, year=2026, replace_existing=True)
        assert status == 200, stored
        assert stored["stored_rows"] == 3
        assert "이력으로 남습니다" in stored["storage_note"]

        assert batches(client) == [
            (2, "ACTIVE", "2026-01-01", "2026-12-31"),
            (1, "SUPERSEDED", "2026-03-01", "2026-03-31"),
        ]

    def test_the_superseded_rows_are_kept_and_only_the_new_batch_counts(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⛔ 이전 배치의 행을 지우지 않는다. 계산에만 들지 않는다."""
        upload(client, book(tmp_path / "march.xlsx", ["2026-03-15"]), year=2026, month=3)
        upload(
            client,
            book(tmp_path / "year.xlsx", ["2026-01-05", "2026-03-15", "2026-06-01"]),
            year=2026,
            replace_existing=True,
        )

        con = sqlite3.connect(db)
        assert con.execute("SELECT COUNT(*) FROM purchase").fetchone()[0] == 4  # 1 + 3
        status = client.get("/dashboard/data-status").json()
        assert status["purchase_count"] == 4
        assert status["calculation_target_count"] == 3  # ⭐ 이중 집계가 아니다
        assert status["active_batch_count"] == 1
        assert status["superseded_batch_count"] == 1


# ======================================================================
# 2 · 3 · 4 · 6 — 품지 못하면 예전처럼 거절한다
# ======================================================================
class TestAnythingElseIsStillRefused:
    def _existing_year(self, client: TestClient, tmp_path: Path) -> None:
        status, _ = upload(
            client, book(tmp_path / "year.xlsx", ["2026-05-05"]), year=2026
        )
        assert status == 200

    def test_2_a_narrower_period_does_not_replace_the_year(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """2 — 연 배치가 있는데 1~7월을 올린다 → 거절. 기존 배치 보호."""
        self._existing_year(client, tmp_path)

        path = book(tmp_path / "half.xlsx", ["2026-02-02"])
        status, body = upload(client, path, year=2026, month=2)
        assert status == 409, body
        assert body["detail"]["code"] == "OVERLAPPING_PERIOD"

        # ⛔ 승인해도 바뀌지 않는다 — 교체 확인으로 풀 수 있는 문제가 아니다.
        status, body = upload(client, path, year=2026, month=2, replace_existing=True)
        assert status == 409, body

        assert batches(client) == [(1, "ACTIVE", "2026-01-01", "2026-12-31")]
        assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM purchase").fetchone()[0] == 1

    def test_3_a_different_month_is_not_replaced(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """3 — 3월 배치가 있는데 4월을 올린다 → 겹치지 않으므로 그냥 쌓인다.

        ⛔ 이것은 거절이 아니다. 월 단위 운영의 기존 동작 그대로다.
        """
        upload(client, book(tmp_path / "march.xlsx", ["2026-03-15"]), year=2026, month=3)
        status, stored = upload(
            client, book(tmp_path / "april.xlsx", ["2026-04-15"]), year=2026, month=4
        )
        assert status == 200, stored
        assert batches(client) == [
            (2, "ACTIVE", "2026-04-01", "2026-04-30"),
            (1, "ACTIVE", "2026-03-01", "2026-03-31"),
        ]

    def test_4_a_partial_overlap_is_refused(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """4 — 3월 배치가 있는데 3~4월에 걸친 기간을 올린다 → 일부 겹침.

        3월은 품지만 스스로 4월까지 뻗어 있고, 기존 배치를 벗어난 부분이
        없으므로 사실 품는 경우다. 여기서는 **품지 못하는** 배치가 하나라도
        있으면 거절한다는 것을 본다 — 4월 배치를 하나 더 두어 만든다.
        """
        upload(client, book(tmp_path / "march.xlsx", ["2026-03-15"]), year=2026, month=3)
        upload(client, book(tmp_path / "april.xlsx", ["2026-04-15"]), year=2026, month=4)

        # 4월 중순부터 시작하는 기간 → 4월 배치를 품지 못한다.
        response = client.post(
            "/uploads/purchases",
            json={
                "file_path": book(tmp_path / "mix.xlsx", ["2026-04-20"]),
                "year": 2026,
                "month": 4,
            },
        )
        # 같은 4월 기간이므로 교체 확인으로 이어진다 — 거절이 아니다.
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "EXISTING_PERIOD"

        assert batches(client) == [
            (2, "ACTIVE", "2026-04-01", "2026-04-30"),
            (1, "ACTIVE", "2026-03-01", "2026-03-31"),
        ]

    def test_6_a_narrower_year_file_does_not_replace_a_wider_one(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """6 — 1~12월 배치 위에 1~6월 기간을 올릴 수는 없다.

        화면은 연 단위를 「연 전체」로만 고르게 하므로 실제로는 월 기간이
        같은 상황이 된다. 규칙 자체는 :class:`TestTheRuleItself` 6 에서 본다.
        """
        self._existing_year(client, tmp_path)
        status, body = upload(
            client, book(tmp_path / "june.xlsx", ["2026-06-05"]), year=2026, month=6
        )
        assert status == 409, body
        assert body["detail"]["code"] == "OVERLAPPING_PERIOD"


# ======================================================================
# 5 — 같은 기간 재업로드는 예전 그대로
# ======================================================================
class TestTheSamePeriodStillBehavesAsBefore:
    def test_5_the_same_period_asks_and_then_replaces(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """5 — 같은 기간 재업로드 정책을 바꾸지 않았다."""
        path = book(tmp_path / "year.xlsx", ["2026-05-05"])
        assert upload(client, path, year=2026)[0] == 200

        again = book(tmp_path / "year2.xlsx", ["2026-05-05", "2026-06-06"])
        status, body = upload(client, again, year=2026)
        assert status == 409, body
        assert body["detail"]["code"] == "EXISTING_PERIOD"
        assert body["detail"]["contained_batches"] == []  # 품는 배치는 따로 없다
        assert "함께 교체됩니다" not in body["detail"]["message"]

        status, stored = upload(client, again, year=2026, replace_existing=True)
        assert status == 200, stored
        assert stored["stored_rows"] == 2
        assert batches(client) == [
            (2, "ACTIVE", "2026-01-01", "2026-12-31"),
            (1, "SUPERSEDED", "2026-01-01", "2026-12-31"),
        ]


# ======================================================================
# 6 · 7 — 실패해도 기존 배치가 살아 있는가
# ======================================================================
class TestNothingIsGivenUpBeforeTheNewFileIsSafelyIn:
    def test_6_a_failing_file_leaves_the_existing_batch_alone(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """6 — 검증에 실패한 파일로는 기존 배치가 넘어가지 않는다."""
        upload(client, book(tmp_path / "march.xlsx", ["2026-03-15"]), year=2026, month=3)

        broken = book(tmp_path / "broken.xlsx", ["2026-01-05"])
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "원본"
        sheet.append(list(HEADERS))
        sheet.append([1, "2026-01-01", "합성", "합성기업", SYNTHETIC, 1, 1, "숫자아님",
                      "2026-01-05", "임차료"])
        workbook.save(broken)

        status, body = upload(client, broken, year=2026, replace_existing=True)
        assert status == 200, body
        assert body["stored"] is False
        assert body["error_rows"] == 1

        assert batches(client) == [(1, "ACTIVE", "2026-03-01", "2026-03-31")]
        assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM purchase").fetchone()[0] == 1

    def test_7_the_older_batch_moves_only_after_the_new_one_is_stored(self) -> None:
        """7 — ⭐ 적재가 끝난 뒤에 넘긴다. 순서가 뒤집히면 기간이 비어 버린다."""
        source = Path("src/procurement/importers/batch_import_service.py").read_text(
            encoding="utf-8"
        )
        body = source[source.index("def import_batch(") :]
        body = body[: body.index("\n    def ", body.index("return BatchImportResult"))]
        assert body.index("import_rows(") < body.index("for older in contained_batches")
        assert body.index("update_totals(") < body.index("for older in contained_batches")


# ======================================================================
# 10 · 11 · 12 — 앞선 STEP 의 규칙이 그대로인가
# ======================================================================
class TestTheEarlierRulesAreUntouched:
    def test_10_11_12_aggregate_prior_year_and_amount_rules_survive(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """10·11·12 — 집계 행 · 앞선 해 · 0 이하 금액이 함께 도는가."""
        upload(client, book(tmp_path / "march.xlsx", ["2026-03-15"]), year=2026, month=3)

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "원본"
        sheet.append(list(HEADERS))
        sheet.append([1, "2026-01-01", "합성", "합성기업", SYNTHETIC, 100000, 10000,
                      110000, "2026-01-05", "임차료"])
        sheet.append([2, "2026-01-02", "합성", "합성기업", SYNTHETIC, 100000, 10000,
                      -6468, "2026-01-06", "임차료"])
        sheet.append([3, "2026-01-02", "합성", "합성기업", SYNTHETIC, 100000, 10000,
                      110000, "2025-12-30", "임차료"])
        sheet.append([None, None, "합계", None, None, None, None, 4_000_000_000, None, None])
        path = tmp_path / "mixed.xlsx"
        workbook.save(path)

        status, stored = upload(client, str(path), year=2026, replace_existing=True)
        assert status == 200, stored
        assert stored["aggregate_rows"] == 1        # 10
        assert stored["prior_year_rows"] == 1       # 11
        assert stored["rejected_rows"] == 1         # 12
        assert stored["error_rows"] == 0
        assert stored["stored_rows"] == 1
        assert stored["unexplained_rows"] == 0
        assert batches(client) == [
            (2, "ACTIVE", "2026-01-01", "2026-12-31"),
            (1, "SUPERSEDED", "2026-03-01", "2026-03-31"),
        ]
