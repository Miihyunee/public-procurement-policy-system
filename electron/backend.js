/**
 * electron/backend.js
 *
 * Python 백엔드(FastAPI) 프로세스의 생명주기를 관리한다.
 *
 * **이 파일은 Electron API 를 import 하지 않는다.** 순수 Node 모듈이므로
 * Electron 바이너리 없이도 테스트할 수 있다. Electron 결합은 `main.js` 가 맡는다.
 *
 * 구조
 * ----
 *
 *     Electron main process
 *       └─ (spawn) Python backend ── FastAPI ── SQLite
 *                     ▲
 *       BrowserWindow ─┘  http://127.0.0.1:<선택한 포트>/
 *
 * 왜 이 구조인가
 * -------------
 *
 * 기존 화면(`web/static/index.html`)이 `/dashboard/summary` 같은 **상대 경로**로
 * API 를 호출한다. `file://` 로 열면 그 경로가 깨지므로, 화면을 그대로 재사용하려면
 * **localhost HTTP 로 서빙**해야 한다. 이 방식이면 프런트엔드를 한 줄도 고치지
 * 않고 Electron 창에 그대로 띄울 수 있다.
 */

"use strict";

const { spawn } = require("node:child_process");
const net = require("node:net");
const path = require("node:path");

/** 백엔드가 뜰 때까지 기다리는 최대 시간(ms). */
const DEFAULT_STARTUP_TIMEOUT_MS = 30000;

/** 준비 상태를 확인하는 간격(ms). */
const POLL_INTERVAL_MS = 200;

/** 종료 요청 후 강제 종료까지 기다리는 시간(ms). */
const SHUTDOWN_GRACE_MS = 3000;

/**
 * 비어 있는 TCP 포트를 하나 얻는다.
 *
 * 고정 포트(8000)를 쓰면 사용자 PC 에서 다른 프로그램과 충돌할 수 있다.
 * OS 에게 빈 포트를 받아 그 값을 백엔드에 넘긴다.
 *
 * @returns {Promise<number>} 사용 가능한 포트 번호.
 */
function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

/**
 * 백엔드가 응답할 때까지 기다린다.
 *
 * 별도의 health 엔드포인트를 새로 만들지 않고, 대시보드 화면(`GET /`)이 200 을
 * 돌려주는지로 판단한다. API 표면을 늘리지 않기 위함이다.
 *
 * @param {number} port 확인할 포트.
 * @param {{timeoutMs?: number, signal?: AbortSignal}} [options]
 * @returns {Promise<void>} 준비되면 resolve.
 */
async function waitUntilReady(port, options = {}) {
  const timeoutMs = options.timeoutMs ?? DEFAULT_STARTUP_TIMEOUT_MS;
  const deadline = Date.now() + timeoutMs;
  const url = `http://127.0.0.1:${port}/`;

  for (;;) {
    if (options.signal?.aborted) {
      throw new Error("백엔드 준비 대기가 취소되었습니다.");
    }
    try {
      const response = await fetch(url, { method: "GET" });
      if (response.ok) {
        return;
      }
    } catch {
      // 아직 뜨지 않았다. 다음 주기에 다시 확인한다.
    }
    if (Date.now() > deadline) {
      throw new Error(
        `백엔드가 ${Math.round(timeoutMs / 1000)}초 안에 시작되지 않았습니다 (포트 ${port}).`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
}

/**
 * Python 백엔드를 실행할 명령을 만든다.
 *
 * 개발 환경에서는 `python -m procurement run` 을 쓰고, 배포본에서는 번들된
 * 실행파일을 쓴다. **어느 쪽을 쓸지는 아직 확정하지 않았다**(PM 결정 대기).
 *
 * @param {{pythonPath?: string, backendExecutable?: string, port: number}} config
 * @returns {{command: string, args: string[]}}
 */
function buildCommand(config) {
  if (config.backendExecutable) {
    return {
      command: config.backendExecutable,
      args: ["run", "--host", "127.0.0.1", "--port", String(config.port)],
    };
  }
  return {
    command: config.pythonPath ?? "python",
    args: [
      "-m",
      "procurement",
      "run",
      "--host",
      "127.0.0.1",
      "--port",
      String(config.port),
    ],
  };
}

/**
 * 백엔드 프로세스에 넘길 환경변수를 만든다.
 *
 * **DB 위치는 환경변수로만 바꾼다.** 설정(`Settings.DATABASE_PATH`)이 이미
 * 환경변수를 읽으므로 Python 코드를 고칠 필요가 없다. 설치 디렉터리가 아니라
 * 사용자 데이터 디렉터리를 쓰기 위한 것이다.
 *
 * @param {{userDataDir?: string, env?: NodeJS.ProcessEnv}} config
 * @returns {NodeJS.ProcessEnv}
 */
function buildEnv(config) {
  const env = { ...(config.env ?? process.env) };
  if (config.userDataDir) {
    env.DATABASE_PATH = path.join(config.userDataDir, "database");
  }
  // 표준 출력이 버퍼링되면 기동 로그가 늦게 보인다.
  env.PYTHONUNBUFFERED = "1";
  return env;
}

/**
 * DB 를 사용할 수 있는 상태로 만든다 (최초 실행 · 구 버전 업그레이드).
 *
 * 백엔드의 ``run`` 은 **DB 를 자동으로 바꾸지 않는다.** 스키마가 오래됐거나 DB 가
 * 없으면 서버를 띄우지 않고 ``init`` 을 안내하며 종료한다. 데스크톱 앱에서는
 * 사용자가 명령줄을 쓰지 않으므로, 앱이 대신 ``init`` 을 한 번 실행한다.
 *
 * ``init`` 은 다음을 보장한다.
 *
 * - **멱등** — 여러 번 실행해도 안전하다
 * - **기존 데이터를 삭제하지 않는다** — 없는 테이블·컬럼만 추가한다
 * - 기본 정책을 등록한다(이미 있으면 건너뛴다)
 *
 * @param {object} config `startBackend` 와 같은 설정.
 * @returns {Promise<string>} ``init`` 이 출력한 점검 결과.
 * @throws {BackendStartError} 초기화에 실패한 경우.
 */
function ensureDatabase(config = {}) {
  const { command, args } = buildCommand({ ...config, port: 0 });
  const initArgs = args.slice(0, args.indexOf("run"));
  initArgs.push("init");

  return new Promise((resolve, reject) => {
    const child = spawn(command, initArgs, {
      cwd: config.cwd,
      env: buildEnv(config),
      stdio: ["ignore", "pipe", "pipe"],
    });

    let output = "";
    const capture = (chunk) => {
      output += chunk.toString();
    };
    child.stdout.on("data", capture);
    child.stderr.on("data", capture);

    child.on("error", (error) => {
      reject(
        new BackendStartError(
          "데이터 저장소를 준비하지 못했습니다.",
          `${error.message}\n${output}`,
          null,
        ),
      );
    });
    child.on("exit", (code, signal) => {
      if (code === 0) {
        config.onLog?.(output);
        resolve(output);
        return;
      }
      reject(
        new BackendStartError(
          "데이터 저장소를 준비하지 못했습니다.",
          output,
          { code, signal },
        ),
      );
    });
  });
}

/**
 * 백엔드를 실행하고 준비될 때까지 기다린다.
 *
 * @param {object} config
 * @param {string} [config.pythonPath] 개발 환경에서 사용할 Python 실행 경로.
 * @param {string} [config.backendExecutable] 배포본에서 사용할 번들 실행파일 경로.
 * @param {string} [config.cwd] 작업 디렉터리.
 * @param {string} [config.userDataDir] 사용자 데이터 디렉터리(DB 저장 위치).
 * @param {number} [config.port] 사용할 포트. 생략하면 빈 포트를 자동 선택.
 * @param {number} [config.timeoutMs] 기동 대기 시간.
 * @param {(line: string) => void} [config.onLog] 백엔드 로그 콜백.
 * @returns {Promise<{port: number, process: import("node:child_process").ChildProcess, stop: () => Promise<void>}>}
 * @throws {BackendStartError} DB 초기화 또는 기동에 실패한 경우.
 */
async function startBackend(config = {}) {
  // 서버를 띄우기 전에 DB 를 사용할 수 있는 상태로 만든다(최초 실행·업그레이드).
  await ensureDatabase(config);

  const port = config.port ?? (await findFreePort());
  const { command, args } = buildCommand({ ...config, port });

  const child = spawn(command, args, {
    cwd: config.cwd,
    env: buildEnv(config),
    stdio: ["ignore", "pipe", "pipe"],
  });

  const logs = [];
  const capture = (chunk) => {
    const text = chunk.toString();
    logs.push(text);
    // 메모리를 무한정 쓰지 않도록 최근 것만 유지한다.
    if (logs.length > 200) {
      logs.shift();
    }
    config.onLog?.(text);
  };
  child.stdout.on("data", capture);
  child.stderr.on("data", capture);

  let exited = false;
  let exitInfo = null;
  child.on("exit", (code, signal) => {
    exited = true;
    exitInfo = { code, signal };
  });

  const controller = new AbortController();
  child.on("exit", () => controller.abort());

  try {
    await waitUntilReady(port, {
      timeoutMs: config.timeoutMs,
      signal: controller.signal,
    });
  } catch (error) {
    const detail = logs.join("").trim();
    await stopProcess(child);
    if (exited) {
      throw new BackendStartError(
        "데이터 관리 프로그램을 시작하지 못했습니다.",
        detail,
        exitInfo,
      );
    }
    throw new BackendStartError(error.message, detail, exitInfo);
  }

  return {
    port,
    process: child,
    stop: () => stopProcess(child),
  };
}

/**
 * 백엔드 시작 실패를 나타내는 오류.
 *
 * 사용자에게는 `message` 만 보여주고, `detail`(백엔드 로그)은 진단용으로 둔다.
 * stack trace 를 그대로 노출하지 않기 위한 구분이다.
 */
class BackendStartError extends Error {
  /**
   * @param {string} message 사용자에게 보여줄 메시지.
   * @param {string} detail 백엔드가 출력한 로그.
   * @param {{code: number|null, signal: string|null}|null} exitInfo 종료 정보.
   */
  constructor(message, detail, exitInfo) {
    super(message);
    this.name = "BackendStartError";
    this.detail = detail;
    this.exitInfo = exitInfo;
  }
}

/**
 * 백엔드 프로세스를 정리한다.
 *
 * 정상 종료 신호를 먼저 보내고, 유예 시간 안에 끝나지 않으면 강제 종료한다.
 *
 * @param {import("node:child_process").ChildProcess} child
 * @returns {Promise<void>}
 */
function stopProcess(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      resolve();
    }, SHUTDOWN_GRACE_MS);

    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
    child.kill("SIGTERM");
  });
}

module.exports = {
  BackendStartError,
  DEFAULT_STARTUP_TIMEOUT_MS,
  buildCommand,
  buildEnv,
  ensureDatabase,
  findFreePort,
  startBackend,
  stopProcess,
  waitUntilReady,
};
