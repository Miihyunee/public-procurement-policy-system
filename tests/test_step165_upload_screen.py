"""
STEP 165 — 올리기 전에 무슨 일이 일어날지 알 수 있게.

무엇이 문제였나
===============
- 「검증만」과 「검증 후 저장」이 **나란히 켜져 있었다.** 검증을 건너뛰고
  바로 저장할 수 있었고, 어느 것이 진짜 저장인지도 알기 어려웠다.
- 지금 그 기간에 **무엇이 들어 있는지** 올리기 전에는 알 수 없었다.
- 교체 확인이 브라우저 기본 창 한 덩어리였다. 「삭제하고」라고 적혀 있었는데
  실제로는 지우지 않고 이력으로 남기는 **논리 교체**이고(PM-012), 함께
  교체되는 등록이 있다는 사실도 말하지 못했다.
- 결과가 숫자 몇 개뿐이라 성공인지 실패인지 한눈에 들어오지 않았다.

무엇을 바꿨나 (🟢 2026-09-11 PM 확정)
=====================================
1. 단계 표시 — 파일 고르기 → 검증 → 저장.
2. **검증을 통과해야 저장이 열린다.** 비활성 사유를 단계마다 적는다.
3. 올리기 전에 **지금 등록되어 있는 자료**를 보여 준다.
4. 교체 확인창을 화면 안 대화상자로 바꾸고, 서버가 준 값(기존 배치 · 함께
   교체될 배치)을 나란히 적는다.
5. 결과에 배지(저장 완료 / 검증 통과 / 확인 필요)를 글자로 함께 적는다.

⛔ API · DB · 계산 로직 · 상태값을 바꾸지 않았다. 새 API 도 없다.
⛔ 화면이 겹침·포함을 판정하지 않는다 — 서버가 준 결과를 적을 뿐이다.
⛔ 기업정보 등록 화면은 이번 범위가 아니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import bootstrap

ROOT = Path(__file__).resolve().parents[1]
PAGE_PATH = ROOT / "src" / "procurement" / "web" / "static" / "index.html"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def markup(page: str) -> str:
    return page[: page.index("<script>")]


def _function_body(page: str, name: str) -> str:
    start = page.index("function " + name + "(")
    depth = 0
    opened = False
    for index in range(start, len(page)):
        char = page[index]
        if char == "{":
            depth += 1
            opened = True
        elif char == "}":
            depth -= 1
            if opened and depth == 0:
                return page[start : index + 1]
    raise AssertionError(f"{name} 의 끝을 찾지 못했다")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "t.db"
    bootstrap(db)
    return TestClient(create_app(db))


# ======================================================================
# 단계
# ======================================================================
class TestTheScreenSaysWhereYouAre:
    def test_the_three_steps_are_named(self, markup: str) -> None:
        for number, label in ((1, "파일 고르기"), (2, "검증"), (3, "저장")):
            assert f'id="upload-step-{number}"' in markup, number
            assert label in markup, label

    def test_the_current_step_is_not_colour_only(self, page: str) -> None:
        """⛔ 색만으로 알리지 않는다 — 보조기술이 읽을 표시를 함께 둔다."""
        body = _function_body(page, "uploadStep")
        assert 'setAttribute("aria-current"' in body

    def test_the_step_moves_with_the_work(self, page: str) -> None:
        assert "uploadStep(2)" in page  # 파일을 고른 뒤
        assert "uploadStep(3)" in page  # 검증을 통과한 뒤


# ======================================================================
# 단계별 CTA 와 비활성 사유
# ======================================================================
class TestOneActionAtATime:
    def test_nothing_is_enabled_before_a_file_is_chosen(self, markup: str) -> None:
        assert '<button id="upload-run" class="control" type="button" disabled>' in markup
        assert (
            '<button id="upload-save" class="control is-primary" type="button" disabled>'
            in markup
        )
        assert "파일을 먼저 선택하세요." in markup

    def test_choosing_a_file_opens_validation_only(self, page: str) -> None:
        """⭐ 파일을 골라도 저장은 아직 잠겨 있다."""
        body = _function_body(page, "initUpload")
        picked = body[body.index("uploadPath = res.path;") :]
        assert "run.disabled = false;" in picked
        assert "save.disabled = true;" in picked
        assert '"검증을 먼저 실행하세요."' in picked

    def test_a_new_file_invalidates_the_earlier_validation(self, page: str) -> None:
        """⛔ 앞 파일의 검증 결과로 새 파일을 저장하지 않는다."""
        body = _function_body(page, "initUpload")
        picked = body[body.index("uploadPath = res.path;") :]
        assert "uploadValidated = false;" in picked

    def test_passing_validation_opens_saving(self, page: str) -> None:
        body = _function_body(page, "renderUploadResult")
        assert "uploadValidated = !!result.ok;" in body
        assert "saveButton.disabled = !uploadValidated;" in body

    def test_a_failed_validation_says_what_to_do(self, page: str) -> None:
        body = _function_body(page, "renderUploadResult")
        assert '"오류를 수정한 파일을 다시 선택하세요."' in body
        assert "기존 자료는 그대로 있습니다." in body

    def test_the_desktop_only_reason_is_still_written(self, page: str) -> None:
        assert '"파일 선택은 데스크톱 앱에서만 됩니다."' in page

    def test_the_month_defaults_to_the_whole_year(self, page: str) -> None:
        """🟢 연 단위 운영이 확정됐으니 기본값도 그래야 한다(§0.63)."""
        body = _function_body(page, "buildUploadMonthOptions")
        assert "whole.selected = true;" in body
        # ⛔ 달 선택을 없애지 않았다.
        assert "month <= 12" in body

    def test_the_long_file_name_is_not_lost(self, page: str) -> None:
        """긴 경로도 확인할 수 있어야 한다."""
        assert 'el("upload-file").title = res.path;' in page


# ======================================================================
# 지금 등록되어 있는 자료
# ======================================================================
class TestYouCanSeeWhatIsAlreadyThere:
    def test_the_panel_exists(self, markup: str) -> None:
        assert 'id="upload-existing"' in markup

    def test_it_reads_the_server_value(self, page: str) -> None:
        """⛔ 화면이 「지금 쓰이는 배치」를 스스로 고르지 않는다."""
        body = _function_body(page, "renderExistingBatches")
        assert "item.is_current" in body

    def test_it_says_what_happens_on_reupload(self, page: str) -> None:
        body = _function_body(page, "renderExistingBatches")
        assert "되묻고" in body
        assert "이력으로 남습니다" in body

    def test_a_filtered_view_does_not_claim_there_is_nothing(self, page: str) -> None:
        """⛔ 걸러 본 목록으로 「등록된 것이 없다」고 적지 않는다."""
        body = _function_body(page, "renderExistingBatches")
        assert 'el("history-period").value' in body

    def test_no_new_endpoint_was_added(self, page: str) -> None:
        """⛔ 새 API 를 부르지 않는다 — 이미 받아 둔 이력을 쓴다."""
        body = _function_body(page, "renderExistingBatches")
        assert "fetch" not in body


# ======================================================================
# 교체 확인창
# ======================================================================
class TestTheReplaceDialogShowsWhatChanges:
    def test_it_is_a_real_dialog_not_a_browser_prompt(self, markup: str, page: str) -> None:
        assert 'id="replace-dialog"' in markup
        assert 'role="dialog" aria-modal="true"' in markup
        assert "window.confirm(" not in page

    def test_it_lists_the_four_things(self, page: str) -> None:
        body = _function_body(page, "confirmReplace")
        for row in ("지금 저장된 자료", "새로 올리는 자료", "대상 기간",
                    "함께 교체되는 등록", "그대로 두는 것"):
            assert row in body, row

    def test_the_batches_come_from_the_server(self, page: str) -> None:
        """⛔ 화면이 포함 관계를 다시 판정하지 않는다 (STEP 162 는 서버 몫)."""
        body = _function_body(page, "confirmReplace")
        assert "detail.contained_batches" in body
        assert "detail.existing_file_name" in body

    def test_it_does_not_say_the_old_data_is_deleted(self, page: str) -> None:
        """⛔ 논리 교체다 — 이전 배치는 이력으로 남는다(PM-012)."""
        body = _function_body(page, "confirmReplace")
        assert "이전 자료는 지워지지 않고 이력에 「대체됨」으로 남습니다." in body
        # 담당자에게 **보이는 글자**만 본다 — 주석에는 「삭제라고 쓰지 않는다」는
        # ⛔ 문장이 들어 있어 글자만 세면 잘못 걸린다.
        shown = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("//")
        )
        assert "삭제" not in shown

    def test_it_still_warns_about_the_confirmed_types(self, page: str) -> None:
        body = _function_body(page, "confirmReplace")
        assert "구매유형은 새 자료에 자동으로 옮겨지지 않습니다" in body

    def test_the_buttons_are_ranked(self, markup: str) -> None:
        assert '<button type="button" class="control" id="replace-cancel">취소</button>' in markup
        assert 'class="control is-primary" id="replace-go">교체하고 저장하기' in markup

    def test_cancelling_changes_nothing(self, page: str) -> None:
        body = _function_body(page, "sendUpload")
        assert "취소했습니다. 기존 자료를 그대로 두었습니다." in body

    def test_escape_counts_as_cancel(self, page: str) -> None:
        body = _function_body(page, "confirmReplace")
        assert 'event.key === "Escape"' in body
        assert "finish(false)" in body

    def test_approval_resends_with_the_flag(self, page: str) -> None:
        """⛔ 확인 없이는 절대 교체되지 않는다 — 승인해야 플래그가 붙는다."""
        body = _function_body(page, "sendUpload")
        assert "confirmed.replace_existing = true;" in body
        assert "if (!agreed)" in body


# ======================================================================
# 결과 표시
# ======================================================================
class TestTheResultIsReadableAtAGlance:
    def test_the_badge_is_written_not_only_coloured(self, page: str) -> None:
        body = _function_body(page, "renderUploadResult")
        for label in ("저장 완료", "검증 통과", "확인 필요"):
            assert f'"{label}"' in body, label

    def test_the_numbers_are_still_all_there(self, page: str) -> None:
        """⛔ 무엇 하나 없애지 않았다."""
        body = _function_body(page, "renderUploadResult")
        for stat in ("총 행", "정상", "오류 행", "저장", "배치"):
            assert stat in body, stat

    def test_the_sentence_comes_from_the_server(self, page: str) -> None:
        """⛔ 판정 문장을 화면이 새로 쓰지 않는다."""
        assert "result.storage_note" in _function_body(page, "renderUploadResult")

    def test_the_aggregate_and_prior_year_lines_survive(self, page: str) -> None:
        """⛔ STEP 160 의 안내를 지우지 않았다(서버 요약 문장에 담겨 온다)."""
        assert "summary_lines" in page or "storage_note" in page


# ======================================================================
# 바뀌지 않은 것
# ======================================================================
class TestNothingBehindTheScreenMoved:
    def test_no_external_resource(self, page: str) -> None:
        for banned in ("fonts.googleapis.com", "cdn.jsdelivr.net", "@import url("):
            assert banned not in page, banned

    def test_the_company_registration_screen_was_left_alone(self, markup: str) -> None:
        """⛔ 이번 범위가 아니다 — 건드리지 않았다."""
        assert 'id="company-card"' in markup
        assert 'id="cr-table"' in markup or "기업정보 등록" in markup

    def test_the_page_still_serves(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert 'id="replace-dialog"' in response.text
        assert 'id="upload-steps"' in response.text

    def test_the_upload_endpoints_are_unchanged(self, page: str) -> None:
        assert '"/uploads/purchases/validate"' in page
        assert '"/uploads/purchases"' in page
