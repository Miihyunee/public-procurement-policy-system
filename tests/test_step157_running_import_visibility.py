"""
STEP 157 — 이미 등록된 정책에 새로 올리는 중일 때도 보이게 한다 (P1-B),
그리고 검토 상태를 내부 값 그대로 보여 주지 않는다 (P1-C).

무엇이 문제였나
===============
**P1-B** — STEP 156 의 진행률은 «미등록 → 첫 등록» 일 때만 보였다. 그런데
담당자가 실제로 하는 일은 «이미 들어 있는 명단을 새 파일로 갈아 끼우는 것»
이다. 그때 화면은 예전 자료의 「등록완료 · 인증 29,366건」만 적고 있었고,
두 시간짜리 적재가 도는 동안 **아무 표시도 없었다.** 담당자는 멈춘 줄 알고
또 중단했다 — STEP 152 · 153 에서 실제로 일어난 일이다.

**P1-C** — 구매유형 검토 화면이 「검토 상태 CONFIRMED」처럼 내부 값을 그대로
적고 있었다. 목록의 거르개는 이미 한국어인데(STEP 156.6 P1-3) 상세는 아니었다.

무엇을 바꿨나
=============
등록 현황 응답에 **돌고 있는 등록**을 담는 ``in_progress`` 블록을 따로 두었다.
본체는 여전히 **끝난 자료**를 그대로 말한다 — 계산이 거기서 나오기 때문이다.
검토 상태는 화면에서 읽을 말로 바꿔 적는다.

⛔ 본체 숫자를 진행 중 값으로 덮어쓰지 않는다 — 그러면 계산 근거와 화면이
   어긋난다.
⛔ 진행 중 자료로 계산하지 않는다 (STEP 154 그대로).
⛔ 서버가 준 상태값 자체를 바꾸지 않는다 — 화면 글자만 고른다.
⛔ SSE·WebSocket 을 쓰지 않는다 — 기존 조회를 다시 부를 뿐이다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.admin import IN_PROGRESS, NOT_REGISTERED, REGISTERED
from procurement.app import create_app
from procurement.database.bootstrap import bootstrap
from procurement.importers.company_importer import (
    CompanyImporter,
    CompanyImportReport,
    CompanyRecord,
)

PAGE = Path("src/procurement/web/static/index.html").read_text(encoding="utf-8")

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


def _upload(client: TestClient, file_path: str) -> None:
    response = client.post(
        "/companies/upload", json={"file_path": file_path, "policy_code": "WOMAN"}
    )
    assert response.status_code == 200, response.text


def _woman(client: TestClient) -> dict[str, Any]:
    items = client.get("/companies/registration").json()["items"]
    return next(dict(item) for item in items if item["policy_code"] == "WOMAN")


def _stop_after(rows_done: int) -> Callable[..., CompanyImportReport]:
    """``rows_done`` 건까지 실제로 적재한 뒤 멈춥니다(예외)."""
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
                else lambda done, _total: on_progress(done, len(rows))
            ),
        )
        raise RuntimeError("적재 도중 끊겼다")

    return partial


# ======================================================================
# P1-B 서버 — 돌고 있는 등록을 본체와 **따로** 알리는가
# ======================================================================
class TestARunningImportIsReportedNextToTheFinishedOne:
    def test_1_nothing_running_means_no_block(self, client: TestClient, tmp_path: Path) -> None:
        """TEST 1 — 도는 것이 없으면 ``in_progress`` 는 없다."""
        assert _woman(client)["in_progress"] is None

        _upload(client, _listing(tmp_path / "a.xlsx", 10))

        woman = _woman(client)
        assert woman["status"] == REGISTERED
        assert woman["in_progress"] is None

    def test_2_a_second_upload_shows_up_without_touching_the_body(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TEST 2 — ⭐ P1-B 의 핵심. 본체는 끝난 자료, 진행은 따로."""
        _upload(client, _listing(tmp_path / "a.xlsx", 10))

        monkeypatch.setattr(CompanyImporter, "import_records", _stop_after(30))
        with pytest.raises(RuntimeError):
            client.post(
                "/companies/upload",
                json={
                    "file_path": _listing(tmp_path / "b.xlsx", 100, offset=500),
                    "policy_code": "WOMAN",
                },
            )

        woman = _woman(client)

        # 본체 — 끝난 자료 그대로. ⛔ 진행 중 값으로 덮이지 않았다.
        assert woman["status"] == REGISTERED
        assert woman["source_label"] == "a.xlsx"
        assert woman["certification_count"] == 10
        assert woman["progress_percent"] == 100.0

        # 진행 중 — 새 파일이 어디까지 갔는지 여기서 말한다.
        running = woman["in_progress"]
        assert running is not None
        assert running["source_label"] == "b.xlsx"
        assert running["processed_count"] == 30
        assert running["total_count"] == 100
        assert running["progress_percent"] == 30.0

    def test_3_the_first_ever_import_reports_the_same_run_in_both_places(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TEST 3 — 첫 등록이면 본체가 곧 그 등록이다. 두 값이 어긋나지 않는다."""
        monkeypatch.setattr(CompanyImporter, "import_records", _stop_after(40))
        with pytest.raises(RuntimeError):
            client.post(
                "/companies/upload",
                json={"file_path": _listing(tmp_path / "b.xlsx", 100), "policy_code": "WOMAN"},
            )

        woman = _woman(client)
        assert woman["status"] == IN_PROGRESS
        running = woman["in_progress"]
        assert running is not None
        assert running["source_label"] == woman["source_label"] == "b.xlsx"
        assert running["processed_count"] == woman["processed_count"] == 40
        assert running["progress_percent"] == woman["progress_percent"] == 40.0

    def test_4_finishing_clears_the_running_block(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TEST 4 — 새 등록이 끝나면 본체가 새 자료가 되고 진행 블록은 사라진다."""
        _upload(client, _listing(tmp_path / "a.xlsx", 10))
        monkeypatch.setattr(CompanyImporter, "import_records", _stop_after(30))
        with pytest.raises(RuntimeError):
            client.post(
                "/companies/upload",
                json={
                    "file_path": _listing(tmp_path / "b.xlsx", 100, offset=500),
                    "policy_code": "WOMAN"
                },
            )
        assert _woman(client)["in_progress"] is not None

        monkeypatch.undo()
        _upload(client, _listing(tmp_path / "c.xlsx", 20, offset=900))

        woman = _woman(client)
        assert woman["status"] == REGISTERED
        assert woman["source_label"] == "c.xlsx"
        assert woman["certification_count"] == 20
        assert woman["in_progress"] is None

    def test_a_policy_never_registered_stays_not_registered(self, client: TestClient) -> None:
        """⛔ 진행 블록을 붙였다고 미등록이 등록으로 바뀌지 않는다."""
        items = client.get("/companies/registration").json()["items"]
        disabled = next(item for item in items if item["policy_code"] == "DISABLED")
        assert disabled["status"] == NOT_REGISTERED
        assert disabled["registered"] is False
        assert disabled["in_progress"] is None


# ======================================================================
# P1-B 화면 — 진행 중이면 **언제나** 그 줄에 적히는가
# ======================================================================
def _function_body(name: str) -> str:
    start = PAGE.index("function " + name + "(")
    depth = 0
    opened = False
    for index in range(start, len(PAGE)):
        char = PAGE[index]
        if char == "{":
            depth += 1
            opened = True
        elif char == "}":
            depth -= 1
            if opened and depth == 0:
                return PAGE[start : index + 1]
    raise AssertionError(f"{name} 의 끝을 찾지 못했다")


class TestThePageDrawsTheRunningImportOnAnyRow:
    def test_5_a_registered_row_also_draws_the_progress(self) -> None:
        """TEST 5 — 「등록완료」 줄에도 진행 블록을 그린다."""
        body = _function_body("crRow")
        assert 'item.status !== "IN_PROGRESS" && item.in_progress' in body
        assert "crProgress(item.in_progress)" in body

    def test_6_the_progress_block_says_which_file_is_running(self) -> None:
        """TEST 6 — 어느 파일이 도는지 적는다. 두 자료가 한 줄에 있기 때문이다."""
        body = _function_body("crProgress")
        assert '"등록 중 · " + run.source_label' in body

    def test_7_the_screen_keeps_asking_while_anything_runs(self) -> None:
        """TEST 7 — 상태가 「등록완료」여도 도는 것이 있으면 계속 다시 읽는다."""
        assert 'item.status === "IN_PROGRESS" || !!item.in_progress' in PAGE

    def test_the_file_name_is_not_printed_twice(self) -> None:
        """⛔ 진행 중 줄에 파일명을 두 번 적지 않는다."""
        body = _function_body("crRow")
        assert '" · " + item.source_label))' not in body

    def test_the_running_block_still_says_it_is_not_counted_yet(self) -> None:
        """⛔ STEP 154 원칙 그대로 — 이 자료는 아직 계산에 쓰이지 않는다."""
        assert "이 자료는 아직 계산에 쓰이지 않습니다." in _function_body("crProgress")

    def test_no_streaming_was_introduced(self) -> None:
        """⛔ SSE·WebSocket 을 쓰지 않았다."""
        assert "new WebSocket(" not in PAGE
        assert "new EventSource(" not in PAGE


# ======================================================================
# P1-C — 검토 상태를 내부 값 그대로 보여 주지 않는다
# ======================================================================
class TestTheReviewStatusIsWrittenInKorean:
    def test_8_each_status_has_a_korean_word(self) -> None:
        """TEST 8 — PENDING · CONFIRMED · REOPENED 에 각각 우리말이 있다."""
        body = _function_body("reviewStatusLabel")
        for value, label in (
            ("PENDING", "검토 전"),
            ("CONFIRMED", "확정됨"),
            ("REOPENED", "확정 취소됨"),
        ):
            assert f'status === "{value}"' in body, value
            assert f'return "{label}";' in body, label

    def test_9_the_detail_line_uses_it(self) -> None:
        """TEST 9 — 상세 줄이 그 함수를 쓴다."""
        body = _function_body("reviewStateLine")
        assert 'reviewField("검토 상태", reviewStatusLabel(review.status))' in body
        assert 'reviewField("검토 상태", review.status)' not in body

    def test_an_unknown_value_is_shown_as_is(self) -> None:
        """⛔ 모르는 값을 지어내지 않는다 — 그대로 적고 넘긴다."""
        assert "return status;" in _function_body("reviewStatusLabel")

    def test_the_server_status_values_did_not_change(self, client: TestClient) -> None:
        """⛔ 서버가 주는 상태값 자체는 그대로다 — 화면 글자만 고쳤다."""
        response = client.get("/reviews", params={"status": "PENDING"})
        assert response.status_code == 200, response.text

    def test_no_internal_status_word_is_rendered_as_text(self) -> None:
        """화면 본문(주석·script 제외)에 내부 상태값이 글자로 남지 않았다."""
        body = PAGE[PAGE.index("<body") :]
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
        text = re.sub(r"<[^>]+>", "\n", body)
        for banned in ("PENDING", "CONFIRMED", "REOPENED"):
            assert banned not in text, banned


# ======================================================================
# 매뉴얼 — 인증키를 어떻게 발급받는지 적혀 있는가 (A-1)
# ======================================================================
class TestTheManualExplainsHowToGetTheApiKey:
    @pytest.fixture
    def manual(self) -> str:
        return Path("docs/USER_MANUAL.md").read_text(encoding="utf-8")

    def test_10_the_section_exists(self, manual: str) -> None:
        """TEST 10 — 조회 절 바로 뒤에 발급 절이 있다."""
        assert "### [9] 조회용 인증키 발급받기" in manual
        assert manual.index("### [8] 「조회」 방식은 무엇인가요") < manual.index(
            "### [9] 조회용 인증키 발급받기"
        )

    def test_11_both_application_pages_are_given(self, manual: str) -> None:
        """TEST 11 — 어디에 신청하는지 주소가 있다."""
        assert "https://www.data.go.kr/data/15062581/openapi.do" in manual
        assert "https://www.data.go.kr/data/15125362/openapi.do" in manual

    def test_12_the_decoding_key_is_the_one_to_copy(self, manual: str) -> None:
        """TEST 12 — 가장 많이 틀리는 곳. Decoding 을 쓰라고 적혀 있다."""
        assert "일반 인증키 (Decoding)" in manual
        assert "Encoding 키를 넣으면" in manual

    def test_13_the_admin_note_says_which_key_serves_which_lookup(self, manual: str) -> None:
        """TEST 13 — 어느 키가 어느 조회에 쓰이는지 적혀 있다."""
        note = manual[manual.index("### 조회 방식의 인증키 설정") :]
        assert "`SMPP_API_KEY`" in note
        assert "`STARTUP_API_KEY`" in note
        assert "창업기업(창업진흥원)" in note

    def test_the_manual_does_not_carry_a_key(self, manual: str) -> None:
        """⛔ 매뉴얼에 실제 인증키를 적지 않는다."""
        assert "SMPP_API_KEY=발급받은키" in manual
        assert not re.search(r"SMPP_API_KEY=(?!발급받은키)\S", manual)
        assert not re.search(r"STARTUP_API_KEY=(?!발급받은키)\S", manual)

    def test_the_user_is_not_told_to_ask_someone_else(self, manual: str) -> None:
        """⭐ 예전에는 「담당자에게 문의」로 끝났다 — 이제 본문이 답한다."""
        lookup = manual[
            manual.index("### [8] 「조회」 방식은 무엇인가요") : manual.index("# 9. 목표비율 관리")
        ]
        assert "설정 방법은\n> 담당자에게 문의해 주십시오." not in lookup
