"""STEP 147 — 데스크톱 앱에서 목표비율을 저장하지 못하던 결함.

STEP 146 Windows 실기기 검증에서 드러난 **실제 제품 결함**입니다.

    「목표비율 관리」에서 값을 넣고 저장 →
        설정 변경 기능이 꺼져 있습니다. 관리자에게 문의해 주세요.

원인은 두 겹이었습니다.

  ① 목표비율 저장 API 3개가 관리자 토큰 뒤에 있는데(``admin/auth.py``),
     배포 데스크톱 앱에는 ``ADMIN_API_TOKEN`` 을 넘기는 자리가 없었습니다.
  ② 그래서 토큰을 설정해도 소용이 없었습니다 — 화면이 ``Authorization``
     헤더를 **아예 보내지 않았습니다.**

목표비율이 없으면 달성률을 계산하지 않으므로, 고객은 이 프로그램의 목적
자체를 쓸 수 없었습니다.

고친 방식(안 A). 데스크톱 앱이 켜질 때마다 **일회용 세션 토큰**을 만들어

    Electron → (환경변수) → 백엔드
             → (IPC · preload) → 화면 → ``Authorization: Bearer``

로 넘깁니다.

.. warning::
    ⛔ **관리자 가드를 없애지 않았습니다.** ``require_admin_token`` 은 세
    엔드포인트에 그대로 있고, 서버로 띄우는 경우의 동작도 그대로입니다.
    ⛔ 인증을 우회하는 공개 API 를 만들지 않았습니다.
    ⛔ 토큰을 소스·``package.json``·``.env``·빌드 산출물에 고정하지 않습니다.

⚠️ 데이터는 전부 **합성**입니다. 토큰도 테스트 전용 값이며 운영 값이 아닙니다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import init_db, seed_policies

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "src" / "procurement" / "web" / "static" / "index.html"
BACKEND_JS = ROOT / "electron" / "backend.js"
MAIN_JS = ROOT / "electron" / "main.js"
PRELOAD_JS = ROOT / "electron" / "preload.js"

#: 테스트 전용 토큰(운영 값 아님).
TEST_TOKEN = "step147-test-token"

#: 화면이 실제로 쓰는 정책 코드. ⛔ 고객 데이터가 아니라 정본 seed 값이다.
SMALL_BUSINESS = "SMALL_BUSINESS"
WOMAN = "WOMAN"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node 실행파일이 없습니다")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "step147.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def guarded(db_path: Path) -> TestClient:
    """토큰이 설정된 앱 — 데스크톱 앱이 만드는 상황과 같다."""
    return TestClient(create_app(db_path, admin_token=TEST_TOKEN))


@pytest.fixture
def unguarded(db_path: Path) -> TestClient:
    """토큰이 설정되지 않은 앱 — 서버 배포에서 관리자가 값을 안 넣은 경우."""
    return TestClient(create_app(db_path, admin_token=""))


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_body(page: str, name: str) -> str:
    """``function name(`` 부터 짝이 맞는 닫는 중괄호까지.

    ⚠️ 본문의 첫 중괄호를 **매개변수 목록이 끝난 뒤**에서 찾는다. 기본값이
       있는 선언(``function startBackend(config = {})``)에서는 매개변수 안의
       ``{}`` 가 먼저 나오므로, 그것을 본문으로 착각하면 한 글자도 못 읽는다.
    """
    start = page.index("function " + name + "(")
    paren = page.index("(", start)
    depth = 0
    for index in range(paren, len(page)):
        if page[index] == "(":
            depth += 1
        elif page[index] == ")":
            depth -= 1
            if depth == 0:
                paren = index
                break

    depth = 0
    started = False
    for index in range(paren, len(page)):
        char = page[index]
        if char == "{":
            depth += 1
            started = True
        elif char == "}":
            depth -= 1
            if started and depth == 0:
                return page[start : index + 1]
    raise AssertionError(f"{name} 의 끝을 찾지 못했습니다")


def _code_only(source: str) -> str:
    """주석 줄을 걷어낸 코드만 남긴다.

    ⚠️ 이것이 없으면 「``Math.random`` 을 쓰지 않는다」 같은 **주석 문구**가
       금칙어 검사에 걸려, 규칙을 지켰다고 적어 둔 줄 때문에 검사가 실패한다.
    """
    return "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*", "#:"))
    )


def _run_node(script: str) -> str:
    """저장소 루트에서 node 한 줄을 돌리고 표준출력을 돌려준다."""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return result.stdout.strip()


# ----------------------------------------------------------------------
# ① 서버 배포의 기존 인증 동작은 그대로
# ----------------------------------------------------------------------
class TestServerAuthIsUnchanged:
    """⛔ 가드를 약화시키지 않았다."""

    def test_no_token_configured_still_disables_writes(self, unguarded: TestClient) -> None:
        response = unguarded.put(
            f"/policy-targets/2026/{SMALL_BUSINESS}", json={"target_rate": "50"}
        )

        assert response.status_code == 503

    def test_missing_header_is_rejected(self, guarded: TestClient) -> None:
        response = guarded.put(f"/policy-targets/2026/{SMALL_BUSINESS}", json={"target_rate": "50"})

        assert response.status_code == 401

    def test_wrong_token_is_rejected(self, guarded: TestClient) -> None:
        response = guarded.put(
            f"/policy-targets/2026/{SMALL_BUSINESS}",
            json={"target_rate": "50"},
            headers={"Authorization": "Bearer wrong-token"},
        )

        assert response.status_code == 401

    def test_the_guard_is_still_on_all_three_endpoints(self) -> None:
        """⛔ ``require_admin_token`` 을 떼지 않았다."""
        app_source = _source(ROOT / "src" / "procurement" / "app.py")

        assert app_source.count("dependencies=[Depends(require_admin_token)]") == 3

    def test_no_bypass_endpoint_was_added(self) -> None:
        """⛔ 인증을 우회하는 별도 공개 API 를 만들지 않았다."""
        app_source = _source(ROOT / "src" / "procurement" / "app.py")

        for banned in ("/policy-targets/local", "/admin/token", "allow_local", "skip_admin"):
            assert banned not in app_source, banned


# ----------------------------------------------------------------------
# ② 환경변수로 넘긴 토큰이 실제로 인증에 쓰인다
# ----------------------------------------------------------------------
class TestTokenArrivesThroughTheEnvironment:
    """Electron 이 넘기는 통로를 **그대로** 통과시켜 본다.

    ⚠️ ``monkeypatch.setenv`` 로는 확인할 수 없다. 설정은 모듈을 들여올 때
       한 번 읽히므로, 이미 들여온 뒤에 환경변수를 바꿔도 늦다. 실제 배포에서는
       백엔드가 **새 프로세스**로 뜨면서 그때 환경을 읽는다. 그래서 여기서는
       ``startBackend`` 로 진짜 백엔드를 띄워 확인한다 — 이 결함이 배포까지 간
       이유가 「실제 경로를 한 번도 밟지 않아서」였기 때문이다.
    """

    @node
    def test_a_real_backend_accepts_only_the_session_token(self, tmp_path: Path) -> None:
        script = f"""
        const {{ startBackend }} = require("./electron/backend");
        (async () => {{
          const handle = await startBackend({{
            pythonPath: process.cwd() + "/.venv/bin/python",
            cwd: process.cwd(),
            userDataDir: {json.dumps(str(tmp_path / "userdata"))},
            env: {{ ...process.env, PYTHONPATH: process.cwd() + "/src" }},
          }});
          const base = "http://127.0.0.1:" + handle.port;
          const url = base + "/policy-targets/2026/{SMALL_BUSINESS}";
          const body = JSON.stringify({{ target_rate: "50" }});
          const send = (auth) => fetch(url, {{
            method: "PUT",
            headers: auth
              ? {{ "Content-Type": "application/json", Authorization: auth }}
              : {{ "Content-Type": "application/json" }},
            body,
          }}).then((r) => r.status);

          const out = {{
            none: await send(null),
            wrong: await send("Bearer wrong-token"),
            session: await send("Bearer " + handle.adminToken),
          }};
          const listed = await (await fetch(base + "/policy-targets?year=2026")).json();
          const row = listed.items.find((i) => i.policy_code === "{SMALL_BUSINESS}");
          out.saved = row ? row.target_rate : null;
          await handle.stop();
          console.log(JSON.stringify(out));
        }})().catch((error) => {{
          console.error(error.message);
          process.exit(1);
        }});
        """
        result = json.loads(_run_node(script))

        # 토큰이 설정된 상태이므로 「기능이 꺼짐(503)」이 아니라 「인증 실패(401)」다.
        assert result["none"] == 401
        assert result["wrong"] == 401
        assert result["session"] == 200
        assert Decimal(str(result["saved"])) == Decimal("50")


# ----------------------------------------------------------------------
# ③ 화면이 실제로 쓰는 저장 경로 (이번 결함의 핵심)
# ----------------------------------------------------------------------
class TestTheScreenSavePathWorks:
    """CLI 가 아니라 **화면이 보내는 요청**을 그대로 재현한다.

    ⚠️ 예전 테스트는 목표비율을 CLI(``targets``)로 넣었다. 그래서 화면 경로가
       한 번도 실행되지 않았고, 이 결함이 배포까지 갔다.
    """

    def test_small_business_fifty_percent_round_trip(self, guarded: TestClient) -> None:
        auth = {"Authorization": f"Bearer {TEST_TOKEN}"}

        # 화면이 만드는 URL 그대로: /policy-targets/{year}/{code}
        saved = guarded.put(
            f"/policy-targets/2026/{SMALL_BUSINESS}", json={"target_rate": "50"}, headers=auth
        )
        assert saved.status_code == 200

        # 저장 뒤 화면은 다시 읽는다(loadPolicyTargets).
        listed = guarded.get("/policy-targets?year=2026")
        assert listed.status_code == 200
        row = next(item for item in listed.json()["items"] if item["policy_code"] == SMALL_BUSINESS)
        assert Decimal(str(row["target_rate"])) == Decimal("50")

    def test_woman_scoped_targets_round_trip(self, guarded: TestClient) -> None:
        """여성기업은 구매유형별로 칸이 셋 — 화면이 각각 따로 보낸다."""
        auth = {"Authorization": f"Bearer {TEST_TOKEN}"}
        wanted = {"CONSTRUCTION": "3", "SERVICE": "5", "GOODS": "5"}

        for scope, rate in wanted.items():
            # 화면이 만드는 URL 그대로: /policy-targets/{year}/{code}/{scope}
            response = guarded.put(
                f"/policy-targets/2026/{WOMAN}/{scope}",
                json={"target_rate": rate},
                headers=auth,
            )
            assert response.status_code == 200, (scope, response.text)

        listed = guarded.get("/policy-targets?year=2026").json()
        row = next(item for item in listed["items"] if item["policy_code"] == WOMAN)
        got = {
            scoped["scope"]: Decimal(str(scoped["target_rate"])) for scoped in row["scoped_targets"]
        }
        assert got == {key: Decimal(value) for key, value in wanted.items()}

    def test_clearing_is_still_unset_not_zero(self, guarded: TestClient) -> None:
        """⛔ 빈칸은 «미설정» 이지 0% 가 아니다 — 기존 규칙 유지."""
        auth = {"Authorization": f"Bearer {TEST_TOKEN}"}
        guarded.put(
            f"/policy-targets/2026/{SMALL_BUSINESS}", json={"target_rate": "50"}, headers=auth
        )

        cleared = guarded.put(
            f"/policy-targets/2026/{SMALL_BUSINESS}", json={"target_rate": None}, headers=auth
        )

        assert cleared.status_code == 200
        listed = guarded.get("/policy-targets?year=2026").json()
        row = next(item for item in listed["items"] if item["policy_code"] == SMALL_BUSINESS)
        assert row["target_rate"] is None


# ----------------------------------------------------------------------
# ④ Electron — 토큰 생성과 전달
# ----------------------------------------------------------------------
class TestElectronMakesAndPassesTheToken:
    """``electron/backend.js``."""

    def test_it_uses_a_cryptographic_random(self) -> None:
        source = _source(BACKEND_JS)

        assert "crypto.randomBytes" in source
        # ⛔ 예측 가능한 난수를 쓰지 않는다.
        assert "Math.random" not in _code_only(source)

    def test_the_token_is_long_enough(self) -> None:
        source = _source(BACKEND_JS)

        assert "const SESSION_TOKEN_BYTES = 32;" in source

    def test_it_goes_through_the_environment_not_the_command_line(self) -> None:
        """⛔ 명령줄 인자는 작업 관리자에 그대로 보인다."""
        source = _source(BACKEND_JS)
        build_env = _function_body(source, "buildEnv")
        build_command = _function_body(source, "buildCommand")

        assert "env.ADMIN_API_TOKEN = config.adminToken" in build_env
        assert "adminToken" not in build_command

    def test_start_backend_makes_one_when_none_is_given(self) -> None:
        source = _source(BACKEND_JS)
        body = _function_body(source, "startBackend")

        assert "config.adminToken ?? createSessionToken()" in body
        # DB 준비와 서버가 **같은 토큰**을 봐야 한다.
        assert "ensureDatabase(runConfig)" in body
        assert "buildEnv(runConfig)" in body

    def test_the_token_is_returned_to_the_caller(self) -> None:
        source = _source(BACKEND_JS)
        body = _function_body(source, "startBackend")

        assert "adminToken," in body

    @node
    def test_two_launches_never_share_a_token(self) -> None:
        """⑤ 앱을 다시 켜면 새 토큰이다 — 이전 것을 재사용하지 않는다."""
        out = _run_node(
            'const b=require("./electron/backend");'
            "const seen=new Set();"
            "for(let i=0;i<50;i+=1){seen.add(b.createSessionToken());}"
            "console.log(JSON.stringify({unique:seen.size,"
            "len:[...seen][0].length,url:[...seen].every(t=>/^[A-Za-z0-9_-]+$/.test(t))}));"
        )
        result = json.loads(out)

        assert result["unique"] == 50, "같은 토큰이 다시 나왔습니다"
        assert result["len"] >= 40
        assert result["url"] is True

    @node
    def test_build_env_injects_exactly_what_it_was_given(self) -> None:
        out = _run_node(
            'const b=require("./electron/backend");'
            'const withToken=b.buildEnv({env:{},adminToken:"abc"});'
            "const without=b.buildEnv({env:{}});"
            "console.log(JSON.stringify({"
            "set:withToken.ADMIN_API_TOKEN,absent:without.ADMIN_API_TOKEN===undefined}));"
        )
        result = json.loads(out)

        assert result["set"] == "abc"
        # 토큰을 주지 않으면 건드리지 않는다 — 서버 배포 동작을 바꾸지 않는다.
        assert result["absent"] is True


class TestElectronHandsItToTheScreen:
    """``main.js`` · ``preload.js``."""

    def test_main_registers_the_handler_with_the_running_token(self) -> None:
        source = _source(MAIN_JS)

        assert 'ipcMain.handle("admin:sessionToken"' in source
        assert "registerAdminSessionHandler(backend.adminToken)" in source

    def test_preload_exposes_only_a_function(self) -> None:
        source = _source(PRELOAD_JS)

        assert 'adminSessionToken: () => ipcRenderer.invoke("admin:sessionToken")' in source

    def test_electron_security_settings_are_untouched(self) -> None:
        """⛔ 보안 설정을 약화시켜 문제를 푼 것이 아니다."""
        source = _source(MAIN_JS)

        assert "contextIsolation: true" in source
        assert "nodeIntegration: false" in source
        assert "sandbox: true" in source

    def test_the_token_is_not_put_on_the_command_line(self) -> None:
        source = _code_only(_source(MAIN_JS))

        assert "additionalArguments" not in source


# ----------------------------------------------------------------------
# ⑤ 화면 — Authorization 헤더
# ----------------------------------------------------------------------
class TestScreenSendsTheHeader:
    """``index.html`` 의 저장 요청."""

    def test_save_asks_for_the_header_first(self) -> None:
        page = _source(INDEX)
        body = _function_body(page, "savePolicyTargets")

        assert "adminAuthHeader()" in body
        assert "headers.Authorization = auth.Authorization" in body

    def test_the_header_is_a_bearer_token(self) -> None:
        page = _source(INDEX)
        body = _function_body(page, "adminAuthHeader")

        assert '"Authorization": "Bearer " + token' in body
        assert "adminSessionToken()" in body

    def test_a_browser_sends_nothing(self) -> None:
        """⛔ 서버 배포에서는 예전과 똑같은 요청이 나가야 한다."""
        page = _source(INDEX)
        body = _function_body(page, "adminAuthHeader")

        assert "desktop()" in body
        assert "Promise.resolve(null)" in body

    def test_the_token_is_asked_for_only_once(self) -> None:
        page = _source(INDEX)
        body = _function_body(page, "adminAuthHeader")

        assert "adminTokenPromise === null" in body

    def test_a_failure_does_not_break_the_screen(self) -> None:
        """토큰을 못 얻으면 헤더 없이 보내고 서버 응답으로 안내한다."""
        page = _source(INDEX)
        body = _function_body(page, "adminAuthHeader")

        assert ".catch(" in body

    def test_the_existing_failure_messages_are_kept(self) -> None:
        """⛔ 503/401 안내를 지우지 않았다 — 서버 배포에서는 여전히 나온다."""
        page = _source(INDEX)
        body = _function_body(page, "ptFailureMessage")

        assert "설정 변경 기능이 꺼져 있습니다" in body
        assert "설정을 변경할 권한이 없습니다" in body

    def test_only_the_two_endpoints_the_screen_uses(self) -> None:
        """⛔ 화면이 쓰지 않는 API 를 새로 부르지 않았다."""
        page = _source(INDEX)
        body = _function_body(page, "savePolicyTargets")

        assert "/policy-targets/" in body
        assert "target-rate" not in body


# ----------------------------------------------------------------------
# ⑥ 토큰이 어디에도 고정·노출되지 않는다
# ----------------------------------------------------------------------
class TestNothingLeaks:
    """⛔ 비밀값을 저장소·산출물·로그에 남기지 않는다."""

    def test_no_token_value_in_package_json(self) -> None:
        source = _source(ROOT / "package.json")

        assert "ADMIN_API_TOKEN" not in source

    def test_no_env_file_is_committed(self) -> None:
        committed = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()

        assert [name for name in committed if name.endswith(".env")] == []

    def test_no_hardcoded_token_in_the_electron_sources(self) -> None:
        for path in (BACKEND_JS, MAIN_JS, PRELOAD_JS):
            source = _source(path)
            # 값을 문자열로 박아 넣은 자리가 없어야 한다.
            assert 'ADMIN_API_TOKEN = "' not in source, path.name
            assert 'ADMIN_API_TOKEN: "' not in source, path.name

    def test_the_token_is_never_logged(self) -> None:
        """⛔ 로그·오류 메시지에 토큰을 넣지 않는다."""
        for path in (BACKEND_JS, MAIN_JS, PRELOAD_JS):
            source = _source(path)
            for line in source.splitlines():
                code = line.split("//")[0]
                if "adminToken" not in code and "sessionToken" not in code:
                    continue
                for sink in ("console.log", "console.error", "onLog(", "showErrorBox"):
                    assert sink not in code, f"{path.name}: {line.strip()}"

    def test_the_screen_does_not_persist_the_token(self) -> None:
        """⛔ localStorage·URL 에 남기지 않는다 — 메모리에만 둔다."""
        page = _source(INDEX)
        body = _function_body(page, "adminAuthHeader")

        for banned in ("localStorage", "sessionStorage", "location.search", "document.cookie"):
            assert banned not in body, banned

    def test_the_packaging_spec_ships_no_token(self) -> None:
        source = _source(ROOT / "packaging" / "procurement-backend.spec")

        assert "ADMIN_API_TOKEN" not in source
