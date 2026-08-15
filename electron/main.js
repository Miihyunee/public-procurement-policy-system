/**
 * electron/main.js
 *
 * Electron 메인 프로세스. Python 백엔드를 띄우고 그 화면을 창에 표시한다.
 *
 *     app 시작
 *       → 사용자 데이터 디렉터리 확인
 *       → Python 백엔드 실행 (빈 포트 자동 선택)
 *       → 준비될 때까지 대기
 *       → BrowserWindow 로 http://127.0.0.1:<포트>/ 로드
 *       → 종료 시 백엔드 정리
 *
 * 백엔드 생명주기는 `backend.js` 가 담당한다(Electron 없이도 검증 가능하도록 분리).
 *
 * .. note::
 *     화면은 기존 `web/static/index.html` 을 **그대로** 사용한다. 프런트엔드를
 *     새로 만들지 않는다.
 */

"use strict";

const path = require("node:path");
const { app, BrowserWindow, dialog, shell } = require("electron");

const { startBackend } = require("./backend");

/** 개발 모드 여부. 배포본에서는 번들된 백엔드 실행파일을 사용한다. */
const isDev = !app.isPackaged;

/** 실행 중인 백엔드 핸들. 종료 시 정리에 사용한다. */
let backend = null;

/** 메인 창. */
let mainWindow = null;

/**
 * 백엔드 실행 설정을 만든다.
 *
 * @returns {object} `startBackend` 에 넘길 설정.
 */
function backendConfig() {
  const projectRoot = path.resolve(__dirname, "..");

  if (isDev) {
    return {
      // 개발 환경에서는 저장소의 가상환경 Python 을 사용한다.
      pythonPath: process.env.PROCUREMENT_PYTHON || "python",
      cwd: projectRoot,
      userDataDir: app.getPath("userData"),
      env: { ...process.env, PYTHONPATH: path.join(projectRoot, "src") },
    };
  }

  // 배포본: 번들된 실행파일 경로. 실제 번들 방식은 아직 확정되지 않았다.
  return {
    backendExecutable: path.join(process.resourcesPath, "backend", "procurement"),
    userDataDir: app.getPath("userData"),
  };
}

/**
 * 메인 창을 만든다.
 *
 * @param {number} port 백엔드가 사용 중인 포트.
 */
function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1024,
    minHeight: 700,
    title: "우선구매 정책 달성률 관리",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());

  // 외부 링크는 기본 브라우저로 연다(앱 창이 엉뚱한 페이지로 바뀌지 않도록).
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.loadURL(`http://127.0.0.1:${port}/`);
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

/**
 * 사용자에게 시작 실패를 안내한다.
 *
 * stack trace 를 그대로 보여주지 않고, 무엇을 해야 하는지 알려준다.
 *
 * @param {Error & {detail?: string}} error
 */
function showStartupFailure(error) {
  const detail = (error.detail || "").trim();
  dialog.showErrorBox(
    "프로그램을 시작하지 못했습니다",
    [
      error.message,
      "",
      "다음을 확인해 주세요.",
      "  · 프로그램을 다시 실행해 보세요.",
      "  · 문제가 계속되면 관리자에게 아래 내용을 전달해 주세요.",
      detail ? `\n[진단 정보]\n${detail.slice(-2000)}` : "",
    ].join("\n"),
  );
}

app.whenReady().then(async () => {
  try {
    backend = await startBackend(backendConfig());
  } catch (error) {
    showStartupFailure(error);
    app.quit();
    return;
  }

  createWindow(backend.port);

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0 && backend) {
      createWindow(backend.port);
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", async (event) => {
  if (!backend) {
    return;
  }
  event.preventDefault();
  const handle = backend;
  backend = null;
  await handle.stop();
  app.quit();
});
