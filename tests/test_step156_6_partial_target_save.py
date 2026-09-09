"""
STEP 156.6 — 목표비율이 일부만 저장됐을 때 화면이 거짓말하지 않는다.

무엇이 문제였나
===============
목표비율은 **기준마다 따로** 저장됩니다. 여성기업 공사·용역·물품이면 요청이
셋입니다. 그래서 가운데 하나가 거부되면 나머지 둘은 **이미 저장된 뒤**입니다.

    공사 3 → 200
    용역 0 → 422   (0 은 받지 않습니다)
    물품 5 → 200

그런데 화면은 「저장하지 못했습니다」만 적고 서버 값을 다시 읽지 않았습니다.
담당자는 아무것도 안 됐다고 읽는데 두 칸은 바뀌어 있었고, 화면에는 담당자가
친 값이 그대로 남아 서버와 어긋났습니다(STEP 156.5 P1-4).

무엇을 바꿨나
=============
성공·부분 실패·전체 실패 **어느 경우에도** 서버 값을 다시 읽고, 세 경우의
문구를 다르게 적습니다.

⛔ 서버 API 를 바꾸지 않았습니다 — 0 거부·기준별 저장·연도 분리 그대로입니다.
⛔ 여러 요청을 한 덩어리 거래로 묶지 않았습니다.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import bootstrap

#: ⛔ 실제 배포 토큰이 아니다. 설정 변경 API 를 열어 두기 위한 시험용 값.
ADMIN_TOKEN = "step1566-test-token"

YEAR = 2026

PAGE = Path("src/procurement/web/static/index.html").read_text(encoding="utf-8")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db, admin_token=ADMIN_TOKEN))


def _save(client: TestClient, scope: str, value: str) -> int:
    """화면이 보내는 것과 **같은 요청** 하나."""
    response = client.put(
        f"/policy-targets/{YEAR}/WOMAN/{scope}",
        json={"target_rate": value},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    return int(response.status_code)


def _scoped(client: TestClient) -> dict[str, Any]:
    items = client.get("/policy-targets", params={"year": YEAR}).json()["items"]
    woman = next(item for item in items if item["policy_code"] == "WOMAN")
    return {row["scope"]: row["target_rate"] for row in woman["scoped_targets"]}


# ======================================================================
# 서버 — 무엇이 저장되고 무엇이 남는가
# ======================================================================
class TestTheServerSavesEachScopeOnItsOwn:
    def test_1_all_three_succeed(self, client: TestClient) -> None:
        """TEST 1 — 셋 다 정상이면 셋 다 저장된다."""
        for scope, value in (("CONSTRUCTION", "3"), ("SERVICE", "5"), ("GOODS", "5")):
            assert _save(client, scope, value) == 200, scope

        saved = _scoped(client)
        assert {k: Decimal(str(v)) for k, v in saved.items()} == {
            "CONSTRUCTION": Decimal("3"),
            "SERVICE": Decimal("5"),
            "GOODS": Decimal("5"),
        }

    def test_2_one_rejected_leaves_the_others_saved(self, client: TestClient) -> None:
        """TEST 2 — 가운데가 거부돼도 앞뒤는 저장된다.

        ⭐ 이것이 화면이 거짓말하던 이유다. 서버 동작 자체는 옳다.
        """
        assert _save(client, "CONSTRUCTION", "3") == 200
        assert _save(client, "SERVICE", "0") == 422
        assert _save(client, "GOODS", "5") == 200

        saved = _scoped(client)
        assert Decimal(str(saved["CONSTRUCTION"])) == Decimal("3")
        assert saved["SERVICE"] is None
        assert Decimal(str(saved["GOODS"])) == Decimal("5")

    def test_3_a_rejected_value_does_not_erase_the_existing_one(self, client: TestClient) -> None:
        """TEST 3 — 이미 5 가 들어 있는 칸에 0 을 넣어도 5 가 남는다."""
        assert _save(client, "SERVICE", "5") == 200
        assert _save(client, "SERVICE", "0") == 422

        assert Decimal(str(_scoped(client)["SERVICE"])) == Decimal("5")

    def test_4_all_rejected_changes_nothing(self, client: TestClient) -> None:
        """TEST 4 — 전부 거부되면 서버는 그대로다."""
        before = _scoped(client)
        for scope in ("CONSTRUCTION", "SERVICE", "GOODS"):
            assert _save(client, scope, "0") == 422
        assert _scoped(client) == before

    def test_clearing_a_scope_is_not_the_same_as_zero(self, client: TestClient) -> None:
        """⛔ 빈칸(해제)과 0 은 다르다 — 빈칸은 받아들이고 0 은 거부한다."""
        assert _save(client, "GOODS", "5") == 200
        cleared = client.put(
            f"/policy-targets/{YEAR}/WOMAN/GOODS",
            json={"target_rate": None},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert cleared.status_code == 200
        assert _scoped(client)["GOODS"] is None


# ======================================================================
# 화면 — 세 경우를 가르고, 언제나 다시 읽는가
# ======================================================================
def _function_body(name: str) -> str:
    """``function <name>(`` 부터 그 함수의 닫는 중괄호까지."""
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


class TestThePageTellsTheThreeOutcomesApart:
    @pytest.fixture
    def body(self) -> str:
        return _function_body("savePolicyTargets")

    def test_5_the_three_messages_are_different(self, body: str) -> None:
        """TEST 5 — 전부 성공 · 일부 실패 · 전부 실패가 각각 다른 문구다."""
        assert "년 목표비율을 저장했습니다." in body
        assert "일부 목표비율이 저장되지 않았습니다." in body
        assert "저장된 값은 아래에 다시 표시했습니다." in body

    def test_a_failed_request_does_not_stop_the_others(self, body: str) -> None:
        """⛔ 하나가 거부됐다고 나머지 결과를 버리지 않는다."""
        # 각 요청의 실패를 그 자리에서 결과 객체로 바꾼다.
        assert "{ ok: true }" in body
        assert "{ ok: false, error: err }" in body
        assert "results.filter(" in body

    def test_the_server_is_read_again_in_every_outcome(self, body: str) -> None:
        """⭐ 성공이든 실패든 서버 값을 다시 읽는다 — P1-4 의 핵심."""
        assert body.count("loadPolicyTargets()") == 1
        # 재조회는 성공/실패 판정 **뒤에** 있고, catch 로 갈라지지 않는다.
        outcome_at = body.index("results.filter(")
        reload_at = body.index("loadPolicyTargets()")
        assert reload_at > outcome_at

    def test_the_message_is_written_after_the_reload(self, body: str) -> None:
        """⚠️ 재조회가 오류 문구를 지우므로 문구는 그 뒤에 적어야 한다."""
        reload_at = body.index("loadPolicyTargets()")
        assert body.index("ptError(outcome.error") > reload_at
        assert body.index("ptNotice(outcome.notice") > reload_at

    def test_no_technical_code_is_shown(self, body: str) -> None:
        """⛔ HTTP 422 같은 기술 용어를 화면에 적지 않는다."""
        for banned in ("422", "HTTP ", "status ="):
            assert banned not in body, banned

    def test_the_api_contract_was_not_changed(self, body: str) -> None:
        """⛔ 기준마다 따로 보내는 구조 그대로다 — 한 덩어리로 묶지 않았다."""
        assert 'method: "PUT"' in body
        assert "/policy-targets/" in body
        assert "encodeURIComponent(change.scope)" in body


# ======================================================================
# 곁들여 고친 표시 문제 (STEP 156.5 P1-1 · P1-3)
# ======================================================================
class TestTheScreenNamesMatch:
    def test_the_review_screen_has_one_name(self) -> None:
        """⛔ 「구매실적 검토」라는 이름의 화면은 없다."""
        assert "구매실적 검토" not in PAGE
        assert 'go: "구매유형 검토"' in PAGE
        assert '<h2 class="card-title" id="review-card">구매유형 검토</h2>' in PAGE

    def test_the_status_filter_is_in_korean(self) -> None:
        """⛔ 내부 상태값을 그대로 보여 주지 않는다. value 는 그대로 둔다."""
        for value, label in (
            ("PENDING", "검토 전"),
            ("CONFIRMED", "확정됨"),
            ("REOPENED", "확정 취소됨"),
        ):
            assert f'<option value="{value}">{label}</option>' in PAGE, value
            assert f'<option value="{value}">{value}</option>' not in PAGE, value

    def test_no_internal_status_word_is_rendered_as_text(self) -> None:
        """화면 본문(주석·script 제외)에 내부 상태값이 글자로 남지 않았다."""
        body = PAGE[PAGE.index("<body") :]
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
        text = re.sub(r"<[^>]+>", "\n", body)
        for banned in ("PENDING", "CONFIRMED", "REOPENED"):
            assert banned not in text, banned


class TestTheManualPointsAtTheRightTab:
    @pytest.fixture
    def manual(self) -> str:
        return Path("docs/USER_MANUAL.md").read_text(encoding="utf-8")

    def test_the_review_screen_is_under_data_management(self, manual: str) -> None:
        """구매유형 검토는 **자료 관리** 탭에 있다(index.html panel-data)."""
        assert "**자료 관리** 탭 → **구매유형 검토**" in manual
        assert "**점검** 탭 → **구매유형 검토**" not in manual

    def test_the_flow_chart_says_the_same(self, manual: str) -> None:
        assert "⑤ 구매유형 검토        자료 관리 탭" in manual

    def test_the_progress_guidance_survived(self, manual: str) -> None:
        """⛔ STEP 156 의 진행률 안내를 지우지 않았다."""
        assert "⏳ 큰 파일은 시간이 걸립니다" in manual
        assert "43,250 / 98,832건" in manual

    def test_the_review_card_really_sits_in_the_data_panel(self) -> None:
        """매뉴얼이 맞는지 화면 원본으로 확인한다."""
        data_panel = PAGE.index('id="panel-data"')
        check_panel = PAGE.index('id="panel-check"')
        review_card = PAGE.index('id="review-card"')
        assert check_panel < data_panel < review_card
