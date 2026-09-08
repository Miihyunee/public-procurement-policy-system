"""
STEP 154 — 적재가 끝나지 않았는데 「등록완료」라고 말하지 않는다.

무엇이 문제였나
===============
기업정보 업로드는 이런 순서였다.

    ① 버전 행을 만든다 — **그 순간 활성**이 되고 이전 버전은 비활성
    ② 행을 적재한다 (98,832행이면 10분 이상 걸린다)
    ③ 건수를 채운다

②가 끝까지 가지 못하면 ③에 닿지 못한다. 그러면

    · 새 버전은 **비어 있는데 활성**이고
    · 멀쩡하던 이전 버전은 **이미 비활성**이며
    · 화면은 「등록완료 · 인증 0건」이라고 말한다

담당자 PC 에서 실제로 그렇게 됐다(STEP 153). DB 에 인증 29,366건이 남아
있는데 활성 버전에는 3건뿐이었고, 나머지는 계산에서 조용히 빠져 있었다.

무엇을 바꿨나
=============
«활성» 과 «완료» 를 나눴다.

    begin()     진행 중 · 비활성으로 연다 — ⛔ 이전 버전을 내리지 않는다
    complete()  끝났을 때만 활성으로 올리고, 그때 이전 버전을 내린다

적재가 끊기면 complete() 에 닿지 못하므로 **이전 활성 버전이 그대로 남는다.**

⛔ 기존 기업·인증 데이터를 지우지 않았다.
⛔ 계산 공식·매칭·구매유형·목표율을 건드리지 않았다.
⛔ 대량 적재 성능은 이번 범위가 아니다(STEP 155 에서 판단).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.admin import IN_PROGRESS, NOT_REGISTERED, REGISTERED
from procurement.app import create_app
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.policy_company_source_repository import (
    PolicyCompanySourceRepository,
)
from procurement.importers.company_importer import (
    CompanyImporter,
    CompanyImportReport,
    CompanyRecord,
)
from procurement.models.policy_company_source import IMPORT_COMPLETED, IMPORT_IN_PROGRESS

#: ⛔ 실제 고객 자료가 아니다. 검증자릿수를 맞춘 합성 사업자번호.
NUMBERS = ("1000000009", "1000000014", "1000000028", "1000000033")

#: 고객 원본과 같은 머리글 구성. ⛔ 값은 전부 합성이다.
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


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db))


def _listing(path: Path, numbers: tuple[str, ...]) -> str:
    book = Workbook()
    sheet = book.active
    sheet.append(HEADERS)
    for index, number in enumerate(numbers):
        sheet.append(
            [
                index + 1,
                f"합성{index}기업",
                number,
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
        int(p["policy_id"])
        for p in client.get("/policies").json()["policies"]
        if p["policy_code"] == "WOMAN"
    )


def _versions(db: Path) -> list[tuple[Any, ...]]:
    con = sqlite3.connect(db)
    return list(
        con.execute(
            "SELECT s.version, s.is_active, s.import_status, s.company_count, "
            "s.certification_count, s.source_label FROM policy_company_source s "
            "JOIN policy p ON p.policy_id = s.policy_id WHERE p.policy_code = 'WOMAN' "
            "ORDER BY s.version"
        )
    )


def _break_import_halfway(monkeypatch: pytest.MonkeyPatch) -> None:
    """절반만 저장하고 예외를 냅니다 — 담당자 PC 에서 일어난 일과 같은 모양."""
    original = CompanyImporter.import_records

    def half_then_fail(
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
            rows[: len(rows) // 2],
            source=source,
            policy_company_source_id=policy_company_source_id,
            on_progress=on_progress,
        )
        raise RuntimeError("적재 도중 끊겼다")

    monkeypatch.setattr(CompanyImporter, "import_records", half_then_fail)


# ======================================================================
# TEST 1 — 등록을 열기만 하고 끝내지 않은 경우
# ======================================================================
class TestAnUnfinishedImportIsNotCalledComplete:
    def test_1_beginning_an_import_does_not_say_registered(
        self, client: TestClient, db: Path
    ) -> None:
        """버전 행이 생겼다는 것만으로 「등록완료」가 되지 않는다."""
        repository = PolicyCompanySourceRepository(db)
        repository.begin(
            _policy_id(client),
            source="FILE",
            source_label="큰명단.xlsx",
            file_checksum="checksum-a",
        )

        woman = _woman(client)
        assert woman["status"] == IN_PROGRESS
        assert woman["status_label"] == "등록 진행 중"
        assert woman["registered"] is False
        # ⛔ 끝나지 않은 등록에 건수를 적지 않는다.
        assert woman["certification_count"] is None
        assert woman["company_count"] is None
        # 어느 파일이 걸려 있는지는 알려 준다.
        assert woman["source_label"] == "큰명단.xlsx"

    def test_1b_it_is_still_not_registered_for_calculation(
        self, client: TestClient, db: Path
    ) -> None:
        """조회불가 그대로다 — ⛔ 「전부 미해당」으로 바뀌지 않는다."""
        repository = PolicyCompanySourceRepository(db)
        repository.begin(_policy_id(client), source="FILE", file_checksum="checksum-a")
        assert repository.registered_policy_ids() == set()
        assert repository.get(_policy_id(client)) is None


# ======================================================================
# TEST 2 — 적재 도중 예외가 난 경우
# ======================================================================
class TestAPartialImportIsNotCalledComplete:
    def test_2_partial_rows_do_not_make_it_registered(
        self, client: TestClient, db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """일부 행이 실제로 저장된 뒤 예외가 나도 「등록완료」가 아니다.

        담당자 PC 에서 일어난 일과 같은 모양이다 — 98,832행 중 29,366건이
        들어간 채로 멈췄다.
        """
        _break_import_halfway(monkeypatch)

        with pytest.raises(RuntimeError):
            client.post(
                "/companies/upload",
                json={
                    "file_path": _listing(tmp_path / "big.xlsx", NUMBERS),
                    "policy_code": "WOMAN",
                },
            )

        # 절반은 실제로 DB 에 들어갔다 — ⛔ 지우지 않는다.
        con = sqlite3.connect(db)
        stored = con.execute(
            "SELECT COUNT(*) FROM certification c JOIN policy p ON p.policy_id = c.policy_id "
            "WHERE p.policy_code = 'WOMAN'"
        ).fetchone()[0]
        assert stored > 0

        # 그래도 「등록완료」가 아니다.
        woman = _woman(client)
        assert woman["status"] == IN_PROGRESS
        assert woman["registered"] is False

        versions = _versions(db)
        assert len(versions) == 1
        assert versions[0][1] == 0  # is_active
        assert versions[0][2] == IMPORT_IN_PROGRESS

    def test_2b_the_partial_rows_do_not_enter_the_calculation(
        self, client: TestClient, db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⛔ 끊긴 자료로 계산하지 않는다 — 실적이 실제보다 적게 나온다."""
        _break_import_halfway(monkeypatch)
        with pytest.raises(RuntimeError):
            client.post(
                "/companies/upload",
                json={
                    "file_path": _listing(tmp_path / "big.xlsx", NUMBERS),
                    "policy_code": "WOMAN",
                },
            )

        assert CertificationRepository(db).find_active_by_policy(_policy_id(client)) == []


# ======================================================================
# TEST 3 — 멀쩡하던 이전 버전을 지키는가
# ======================================================================
class TestTheGoodVersionSurvivesAFailedOne:
    def test_3_a_stays_active_when_b_is_interrupted(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """A 가 정상 등록된 상태에서 B 가 끊겨도 A 가 계산에 남는다."""
        _upload(client, _listing(tmp_path / "a.xlsx", NUMBERS[:3]))
        assert _woman(client)["certification_count"] == 3

        PolicyCompanySourceRepository(db).begin(
            _policy_id(client),
            source="FILE",
            source_label="b.xlsx",
            file_checksum="checksum-b",
        )

        woman = _woman(client)
        assert woman["status"] == REGISTERED, "이전 등록이 살아 있어야 한다"
        assert woman["certification_count"] == 3
        assert woman["source_label"] == "a.xlsx"
        assert len(CertificationRepository(db).find_active_by_policy(_policy_id(client))) == 3

        versions = _versions(db)
        assert [(row[0], row[1], row[2]) for row in versions] == [
            (1, 1, IMPORT_COMPLETED),
            (2, 0, IMPORT_IN_PROGRESS),
        ]


# ======================================================================
# TEST 4 — 정상적으로 끝난 버전이 활성이 되는가
# ======================================================================
class TestAFinishedVersionTakesOver:
    def test_4_b_becomes_active_and_a_becomes_history(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _upload(client, _listing(tmp_path / "a.xlsx", NUMBERS[:3]))
        _upload(client, _listing(tmp_path / "b.xlsx", NUMBERS[3:]))

        woman = _woman(client)
        assert woman["status"] == REGISTERED
        assert woman["status_label"] == "등록완료"
        assert woman["source_label"] == "b.xlsx"
        assert woman["certification_count"] == 1

        versions = _versions(db)
        assert [(row[0], row[1], row[2]) for row in versions] == [
            (1, 0, IMPORT_COMPLETED),
            (2, 1, IMPORT_COMPLETED),
        ]
        # ⛔ 예전 버전을 지우지 않는다. 계산에서만 빠진다.
        con = sqlite3.connect(db)
        assert con.execute("SELECT COUNT(*) FROM certification").fetchone()[0] == 4
        assert len(CertificationRepository(db).find_active_by_policy(_policy_id(client))) == 1

    def test_4b_completing_records_when_it_finished(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        _upload(client, _listing(tmp_path / "a.xlsx", NUMBERS[:2]))
        con = sqlite3.connect(db)
        completed_at = con.execute(
            "SELECT completed_at FROM policy_company_source WHERE is_active = 1"
        ).fetchone()[0]
        assert completed_at is not None


# ======================================================================
# TEST 5 — 인증 0건인데 정상 완료인 경우
# ======================================================================
class TestZeroCertificationsIsNotTheSameAsUnfinished:
    def test_5_a_finished_import_with_no_certifications_is_still_complete(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⛔ 건수 0 을 미완료로 읽지 않는다.

        목록을 받았는데 우리 거래처가 하나도 없을 수 있고, 그것은
        「판단할 수 없다」가 아니라 **「전부 미해당」** 이다.
        """
        repository = PolicyCompanySourceRepository(db)
        started = repository.begin(
            _policy_id(client),
            source="FILE",
            source_label="빈명단.xlsx",
            file_checksum="checksum-empty",
        )
        assert started.policy_company_source_id is not None
        repository.complete(
            started.policy_company_source_id, company_count=0, certification_count=0
        )

        woman = _woman(client)
        assert woman["status"] == REGISTERED
        assert woman["status_label"] == "등록완료"
        assert woman["registered"] is True
        assert woman["certification_count"] == 0
        assert repository.registered_policy_ids() == {_policy_id(client)}


# ======================================================================
# TEST 6 — 기존 동작이 그대로인가
# ======================================================================
class TestTheNormalPathIsUnchanged:
    def test_6_a_plain_upload_still_registers(self, client: TestClient, tmp_path: Path) -> None:
        body = _upload(client, _listing(tmp_path / "a.xlsx", NUMBERS))
        assert body["stored"] is True
        assert body["created"] == 4
        assert body["certifications"] == 4

        woman = _woman(client)
        assert woman["status"] == REGISTERED
        assert woman["company_count"] == 4
        assert woman["certification_count"] == 4

    def test_6b_the_same_file_twice_does_not_add_a_version(
        self, client: TestClient, db: Path, tmp_path: Path
    ) -> None:
        """⛔ 같은 자료를 다시 올려도 버전을 늘리지 않는다(멱등)."""
        path = _listing(tmp_path / "a.xlsx", NUMBERS[:3])
        _upload(client, path)
        _upload(client, path)

        versions = _versions(db)
        assert len(versions) == 1
        assert versions[0][1] == 1
        assert versions[0][2] == IMPORT_COMPLETED
        assert _woman(client)["certification_count"] == 3

    def test_6c_a_policy_with_no_upload_is_still_not_registered(self, client: TestClient) -> None:
        items = client.get("/companies/registration").json()["items"]
        startup = next(item for item in items if item["policy_code"] == "STARTUP")
        assert startup["status"] == NOT_REGISTERED
        assert startup["status_label"] == "미등록"
        assert startup["registered"] is False


# ======================================================================
# TEST 7 — 응답 형식이 깨지지 않았는가
# ======================================================================
class TestTheResponseShapeIsUnchanged:
    def test_7_a_completed_version_keeps_every_existing_field(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _upload(client, _listing(tmp_path / "a.xlsx", NUMBERS[:2]))
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
        ):
            assert field in woman, field
        assert woman["source"] == "FILE"
        assert woman["available_methods"] == ["FILE", "API"]


# ======================================================================
# 기존 DB 호환 — 예전 행을 갑자기 미등록으로 만들지 않는다
# ======================================================================
class TestOlderDatabasesKeepWorking:
    def test_rows_without_the_new_column_are_treated_as_finished(self, tmp_path: Path) -> None:
        """⛔ 되짚을 근거가 없으므로 지금까지의 동작을 그대로 둔다.

        전부 미완료로 두면 잘 쓰던 정책이 하루아침에 조회불가가 되고 달성률이
        사라진다(DECISIONS §0.58).
        """
        path = tmp_path / "old.db"
        bootstrap(path)
        con = sqlite3.connect(path)
        policy_id = con.execute(
            "SELECT policy_id FROM policy WHERE policy_code = 'WOMAN'"
        ).fetchone()[0]
        # 새 칸을 모르는 예전 코드가 넣은 것처럼 만든다.
        con.execute(
            "INSERT INTO policy_company_source "
            "(policy_id, source, company_count, certification_count, source_label, "
            " version, file_checksum, is_active, registered_at, updated_at) "
            "VALUES (?, 'FILE', 5, 5, '예전명단.xlsx', 1, 'old', 1, "
            " '2026-09-01 10:00:00', '2026-09-01 10:00:00')",
            (policy_id,),
        )
        con.commit()
        con.close()

        repository = PolicyCompanySourceRepository(path)
        saved = repository.get(policy_id)
        assert saved is not None
        assert saved.import_status == IMPORT_COMPLETED
        assert saved.certification_count == 5
        assert repository.registered_policy_ids() == {policy_id}


# ======================================================================
# 화면 — 진행 중을 어떻게 적는가
# ======================================================================
class TestThePageSaysWhatToDo:
    def test_the_page_does_not_print_a_count_for_an_unfinished_import(self) -> None:
        page = Path("src/procurement/web/static/index.html").read_text(encoding="utf-8")
        assert 'item.status === "IN_PROGRESS"' in page
        # ② 요구사항 변경(STEP 156) — 「등록이 끝나지 않았습니다」 대신
        #    진행률과 함께 「끝날 때까지 기다려 주세요」로 바뀌었다.
        assert "등록이 끝날 때까지 기다려 주세요" in page
        assert "아직 계산에 쓰이지 않습니다" in page
