"""
STEP 156 — 대량 적재가 어디까지 갔는지 화면에서 보인다.

무엇이 문제였나
===============
여성기업 원본 98,832행 적재는 10분 이상 걸린다(실측 행당 약 6ms). 그런데
화면에는 아무 표시가 없어서 담당자는 «도는 중인지 멈췄는지» 알 수 없었다.
실제로 「여성기업등록이 너무 오래걸려」라며 중단됐고, 그 결과 29,366건만
들어간 채 계산에서 빠졌다(STEP 152 · 153).

무엇을 바꿨나
=============
적재 루프가 **실제로 처리한 행 수**를 세어 등록 버전에 적고, 화면이 그것을
읽어 진행률을 보여 준다.

⛔ 시간으로 어림한 막대를 그리지 않는다 — 실제 건수만 쓴다.
⛔ 행마다 기록하지 않는다 — 200건마다, 그리고 마지막에 한 번.
⛔ SSE·WebSocket 을 쓰지 않는다 — 기존 조회를 다시 부를 뿐이다.
⛔ 적재 성능 자체는 이번 범위가 아니다.
⛔ STEP 154 원칙은 그대로다 — 진행 중 버전은 계산에도 「등록완료」에도 들지
   않는다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.admin import IN_PROGRESS, REGISTERED, progress_percent_of
from procurement.app import create_app
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.policy_company_source_repository import (
    PolicyCompanySourceRepository,
)
from procurement.importers.company_importer import (
    PROGRESS_EVERY,
    CompanyImporter,
    CompanyImportReport,
    CompanyRecord,
)
from procurement.models.policy_company_source import IMPORT_COMPLETED, IMPORT_IN_PROGRESS

#: ⛔ 실제 고객 자료가 아니다. 검증자릿수만 맞춘 합성 사업자번호를 만든다.
_WEIGHTS = (1, 3, 7, 1, 3, 7, 1, 3, 5)

HEADERS = [
    "NO",
    "업체명",
    "사업자번호",
    "대표자명",
    "기업구분",
    "여성기업",
    "시작일자",
    "만료일자",
    "취소일자",
]


def _synthetic_business_no(index: int) -> str:
    """합성 사업자등록번호. ⛔ 실제 업체의 번호가 아니다."""
    head = f"{1000000 + index:09d}"
    digits = [int(char) for char in head]
    total = sum(a * b for a, b in zip(digits, _WEIGHTS, strict=True)) + (digits[8] * 5) // 10
    return head + str((10 - total % 10) % 10)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _listing(path: Path, count: int, *, offset: int = 0) -> str:
    book = Workbook()
    sheet = book.active
    sheet.append(HEADERS)
    for index in range(count):
        sheet.append(
            [
                index + 1,
                f"합성{offset + index}기업",
                _synthetic_business_no(offset + index),
                "가",
                "중기업",
                "여성기업",
                "2026-01-01",
                "2026-12-31",
                None,
            ]
        )
    book.save(path)
    return str(path)


def _upload(client: TestClient, file_path: str) -> dict[str, Any]:
    response = client.post(
        "/companies/upload", json={"file_path": file_path, "policy_code": "WOMAN"}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _woman(client: TestClient) -> dict[str, Any]:
    items = client.get("/companies/registration").json()["items"]
    return next(dict(item) for item in items if item["policy_code"] == "WOMAN")


def _policy_id(client: TestClient) -> int:
    return next(
        int(policy["policy_id"])
        for policy in client.get("/policies").json()["policies"]
        if policy["policy_code"] == "WOMAN"
    )


def _versions(db: Path) -> list[tuple[Any, ...]]:
    con = sqlite3.connect(db)
    return list(
        con.execute(
            "SELECT s.version, s.is_active, s.import_status, s.processed_count, s.total_count "
            "FROM policy_company_source s JOIN policy p ON p.policy_id = s.policy_id "
            "WHERE p.policy_code = 'WOMAN' ORDER BY s.version"
        )
    )


def _stop_after(rows_done: int) -> Callable[..., CompanyImportReport]:
    """``rows_done`` 건까지 실제로 적재한 뒤 멈춥니다(예외).

    담당자 PC 에서 일어난 일과 같은 모양입니다 — 일부만 들어간 채 끊김.
    """
    original = CompanyImporter.import_records

    def partial(
        self: CompanyImporter,
        records: Iterable[CompanyRecord],
        *,
        source: str,
        policy_company_source_id: int | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> CompanyImportReport:
        rows = list(records)
        original(
            self,
            rows[:rows_done],
            source=source,
            policy_company_source_id=policy_company_source_id,
            on_progress=(
                None
                if on_progress is None
                # 전체 행 수는 **원본 파일 기준**으로 알린다. 절반만 넣었다고
                # 전체가 절반인 것은 아니다.
                else lambda done, _total: on_progress(done, len(rows))
            ),
        )
        raise RuntimeError("적재 도중 끊겼다")

    return partial


# ======================================================================
# TEST 1 · 2 · 3 — 진행률 숫자가 실제 처리 건수와 맞는가
# ======================================================================
class TestTheProgressIsCountedNotGuessed:
    def test_1_nothing_processed_is_zero_percent(self, client: TestClient, db: Path) -> None:
        """등록을 열기만 한 시점 — 0 / 0 · 0%."""
        PolicyCompanySourceRepository(db).begin(
            _policy_id(client),
            source="FILE",
            source_label="큰명단.xlsx",
            file_checksum="checksum-a",
        )
        woman = _woman(client)
        assert woman["status"] == IN_PROGRESS
        assert woman["processed_count"] == 0
        assert woman["progress_percent"] == 0.0

    def test_2_half_way_is_fifty_percent(
        self, client: TestClient, db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """100건 중 50건 → 50 / 100 · 50.0%."""
        monkeypatch.setattr(CompanyImporter, "import_records", _stop_after(50))
        with pytest.raises(RuntimeError):
            client.post(
                "/companies/upload",
                json={"file_path": _listing(tmp_path / "big.xlsx", 100), "policy_code": "WOMAN"},
            )

        woman = _woman(client)
        assert woman["status"] == IN_PROGRESS
        assert woman["processed_count"] == 50
        assert woman["total_count"] == 100
        assert woman["progress_percent"] == 50.0

    def test_3_finishing_is_one_hundred_percent_and_active(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _upload(client, _listing(tmp_path / "a.xlsx", 100))

        woman = _woman(client)
        assert woman["status"] == REGISTERED
        assert woman["processed_count"] == 100
        assert woman["total_count"] == 100
        assert woman["progress_percent"] == 100.0

        assert _versions(db) == [(1, 1, IMPORT_COMPLETED, 100, 100)]

    def test_the_percent_matches_the_two_numbers(self) -> None:
        """⛔ 화면 숫자와 퍼센트가 어긋나지 않는다."""
        assert progress_percent_of(0, 100) == 0.0
        assert progress_percent_of(50, 100) == 50.0
        assert progress_percent_of(43250, 98832) == 43.8
        assert progress_percent_of(98832, 98832) == 100.0


# ======================================================================
# TEST 10 — 전체 행 수를 모를 때
# ======================================================================
class TestZeroTotalDoesNotDivideByZero:
    def test_10_no_total_is_zero_percent(self) -> None:
        assert progress_percent_of(0, 0) == 0.0
        assert progress_percent_of(5, 0) == 0.0

    def test_a_progress_beyond_the_total_never_passes_one_hundred(self) -> None:
        assert progress_percent_of(120, 100) == 100.0


# ======================================================================
# TEST 4 — 진행 중이어도 기존 자료가 계산에 남는가
# ======================================================================
class TestTheRunningImportDoesNotDisturbTheCalculation:
    def test_4_the_finished_version_keeps_serving_the_calculation(
        self, client: TestClient, db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 완료 → B 를 절반 적재 → 계산은 여전히 A 기준."""
        _upload(client, _listing(tmp_path / "a.xlsx", 3))
        assert _woman(client)["certification_count"] == 3

        monkeypatch.setattr(CompanyImporter, "import_records", _stop_after(50))
        with pytest.raises(RuntimeError):
            client.post(
                "/companies/upload",
                json={
                    "file_path": _listing(tmp_path / "b.xlsx", 100, offset=500),
                    "policy_code": "WOMAN",
                },
            )

        # 화면은 A 를 그대로 보여 준다 — ⛔ 계산 결과가 B 로 바뀌지 않는다.
        woman = _woman(client)
        assert woman["status"] == REGISTERED
        assert woman["source_label"] == "a.xlsx"
        assert woman["certification_count"] == 3
        assert woman["progress_percent"] == 100.0
        assert len(CertificationRepository(db).find_active_by_policy(_policy_id(client))) == 3

        # B 는 진행 중 · 비활성으로 남아 있다.
        versions = _versions(db)
        assert [(row[0], row[1], row[2]) for row in versions] == [
            (1, 1, IMPORT_COMPLETED),
            (2, 0, IMPORT_IN_PROGRESS),
        ]
        assert versions[1][3] == 50  # processed_count
        assert versions[1][4] == 100  # total_count


# ======================================================================
# TEST 5 — 끊긴 적재가 활성이 되지 않는가
# ======================================================================
class TestAnInterruptedImportNeverGoesActive:
    def test_5_no_completion_no_activation(
        self, client: TestClient, db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(CompanyImporter, "import_records", _stop_after(30))
        with pytest.raises(RuntimeError):
            client.post(
                "/companies/upload",
                json={"file_path": _listing(tmp_path / "b.xlsx", 100), "policy_code": "WOMAN"},
            )

        woman = _woman(client)
        assert woman["status"] == IN_PROGRESS
        assert woman["registered"] is False
        # ⛔ 끝나지 않은 등록에 인증 건수를 적지 않는다(STEP 154 유지).
        assert woman["certification_count"] is None
        assert _versions(db)[0][1] == 0


# ======================================================================
# TEST 6 — 정상 완료인데 인증 0건
# ======================================================================
class TestZeroCertificationsStillCompletes:
    def test_6_it_is_registered_and_one_hundred_percent(self, client: TestClient, db: Path) -> None:
        repository = PolicyCompanySourceRepository(db)
        started = repository.begin(
            _policy_id(client),
            source="FILE",
            source_label="빈명단.xlsx",
            file_checksum="checksum-empty",
        )
        assert started.policy_company_source_id is not None
        repository.complete(
            started.policy_company_source_id,
            company_count=0,
            certification_count=0,
            total_count=0,
        )

        woman = _woman(client)
        assert woman["status"] == REGISTERED
        assert woman["certification_count"] == 0
        # ⛔ 건수가 0 이라고 미완료로 읽지 않는다. 끝났으면 100% 다.
        assert woman["progress_percent"] == 100.0


# ======================================================================
# TEST 8 · 9 — 응답 형식
# ======================================================================
class TestTheResponseCarriesTheProgress:
    def test_8_in_progress_has_the_three_numbers(
        self, client: TestClient, db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(CompanyImporter, "import_records", _stop_after(20))
        with pytest.raises(RuntimeError):
            client.post(
                "/companies/upload",
                json={"file_path": _listing(tmp_path / "b.xlsx", 100), "policy_code": "WOMAN"},
            )
        woman = _woman(client)
        assert woman["processed_count"] == 20
        assert woman["total_count"] == 100
        assert woman["progress_percent"] == 20.0

    def test_9_a_finished_version_keeps_every_existing_field(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _upload(client, _listing(tmp_path / "a.xlsx", 2))
        woman = _woman(client)
        for field in (
            "policy_id",
            "policy_code",
            "policy_name",
            "registered",
            "status",
            "status_label",
            "source",
            "source_label",
            "company_count",
            "certification_count",
            "updated_at",
            "available_methods",
            "processed_count",
            "total_count",
            "progress_percent",
        ):
            assert field in woman, field
        assert woman["status_label"] == "등록완료"

    def test_a_policy_never_registered_has_no_progress(self, client: TestClient) -> None:
        items = client.get("/companies/registration").json()["items"]
        startup = next(item for item in items if item["policy_code"] == "STARTUP")
        assert startup["processed_count"] is None
        assert startup["total_count"] is None
        assert startup["progress_percent"] is None


# ======================================================================
# 멱등성 — 같은 파일 재등록이 진행률 때문에 깨지지 않는가
# ======================================================================
class TestUploadingTheSameFileTwiceIsStillIdempotent:
    def test_no_new_version_and_no_deactivation(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        path = _listing(tmp_path / "a.xlsx", 5)
        _upload(client, path)
        _upload(client, path)

        assert _versions(db) == [(1, 1, IMPORT_COMPLETED, 5, 5)]
        woman = _woman(client)
        assert woman["status"] == REGISTERED
        assert woman["progress_percent"] == 100.0


# ======================================================================
# 기록 빈도 — 성능을 해치지 않는가
# ======================================================================
class TestTheProgressIsNotWrittenForEveryRow:
    def test_the_interval_is_a_real_interval(self) -> None:
        """⛔ 행마다 기록하지 않는다."""
        assert PROGRESS_EVERY > 1

    def test_the_callback_fires_on_the_interval_and_at_the_end(self) -> None:
        """98,832행이면 UPDATE 는 수백 번이지 98,832번이 아니다."""
        rows = 1000
        calls = 2 + rows // PROGRESS_EVERY  # 시작 · 구간마다 · 마지막
        assert calls < rows / 10

    def test_the_importer_reports_start_middle_and_end(
        self, db: Path, tmp_path: Path, client: TestClient
    ) -> None:
        seen: list[tuple[int, int]] = []
        repository = PolicyCompanySourceRepository(db)
        started = repository.begin(_policy_id(client), source="FILE", file_checksum="c1")
        assert started.policy_company_source_id is not None

        from procurement.app import build_company_source_service

        service = build_company_source_service(db)
        service.import_file(
            _listing(tmp_path / "a.xlsx", PROGRESS_EVERY + 5),
            policy_code="WOMAN",
            begin_version=lambda _checksum: started.policy_company_source_id,
            on_progress=lambda done, total: seen.append((done, total)),
        )

        assert seen[0] == (0, PROGRESS_EVERY + 5)
        assert (PROGRESS_EVERY, PROGRESS_EVERY + 5) in seen
        assert seen[-1] == (PROGRESS_EVERY + 5, PROGRESS_EVERY + 5)


# ======================================================================
# 화면 — 무엇을 그리는가
# ======================================================================
class TestThePageShowsRealNumbers:
    @pytest.fixture
    def page(self) -> str:
        return Path("src/procurement/web/static/index.html").read_text(encoding="utf-8")

    def test_it_uses_the_server_numbers(self, page: str) -> None:
        """⛔ 화면이 숫자를 만들지 않는다 — 서버가 준 것만 읽는다.

        ② 요구사항 변경(STEP 157): 진행 블록이 «지금 도는 등록» 하나를 받도록
        바뀌었다. 등록 줄 본체(``item``)일 수도 있고, 이미 등록완료인 정책에
        새로 올리는 중이면 ``item.in_progress`` 일 수도 있다. 그래서 읽는
        이름이 ``item.*`` 에서 ``run.*`` 이 되었다 — 출처는 그대로 서버다.
        """
        body = page[page.index("function crProgress(") :]
        body = body[: body.index("\n  }")]
        assert "run.processed_count" in body
        assert "run.total_count" in body
        assert "run.progress_percent" in body
        # 그 하나를 등록 줄에서 넘겨 준다 — 둘 중 어느 쪽이든 서버 값이다.
        assert "crProgress(item.in_progress || item)" in page
        assert "crProgress(item.in_progress)" in page

    def test_it_does_not_invent_a_percentage(self, page: str) -> None:
        """⛔ 화면이 퍼센트를 다시 계산하지 않는다 — 서버 값을 그대로 쓴다."""
        start = page.index("function crProgress(")
        body = page[start : page.index("\n  }", start)]
        assert "/ total" not in body
        assert "* 100" not in body
        assert "setInterval" not in body

    def test_it_stops_polling_when_nothing_is_running(self, page: str) -> None:
        start = page.index("function scheduleProgressPoll(")
        body = page[start : page.index("\n  }", start)]
        assert 'item.status === "IN_PROGRESS"' in body
        assert "if (!running) { return; }" in body

    def test_no_websocket_or_sse_was_added(self, page: str) -> None:
        """⛔ 실시간 기술을 새로 들이지 않는다.

        ⚠️ 주석에 이름이 적혀 있는 것은 괜찮다 — **실제로 쓰는 코드**만 본다.
        """
        for banned in ("new EventSource(", "new WebSocket(", "new SharedWorker("):
            assert banned not in page, banned
