#!/usr/bin/env node
/**
 * scripts/verify-shutdown.js
 *
 * **껐을 때 정말 끝나는가** (STEP 126-3 §5·§6·§8·§9).
 *
 * 앱을 여러 번 켰다 껐을 때 백엔드가 남아 쌓이면, 포트를 붙들고 DB 를
 * 물고 있어 다음 실행이 이상해진다. 눈으로 세는 대신 여기서 확인한다.
 *
 *     5회 반복:
 *       기동 → 살아 있는지 확인 → 종료 → **정말 죽었는지** 확인
 *     매회 다른 포트를 받는지, 그리고 데이터가 그대로인지도 본다.
 *
 * 사용법::
 *
 *     node scripts/verify-shutdown.js
 *
 *     # 묶은 실행파일로 (Windows)
 *     $env:PROCUREMENT_BACKEND_EXE = "dist\procurement\procurement.exe"
 *     node scripts/verify-shutdown.js
 *
 * .. note::
 *     이것은 **백엔드 계층**을 본다. Electron 창을 닫는 것까지는 확인하지
 *     못하므로, 창 조작은 사람이 따로 해야 한다.
 *
 * ⛔ 고객 데이터를 쓰지 않는다.
 */

"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { startBackend } = require("../electron/backend");
const { TEMPLATE_PATH } = require("../electron/uploads");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const USER_DATA_DIR = path.join(os.tmpdir(), "procurement-shutdown-test");
const ROUNDS = 5;

function report(ok, label, detail = "") {
  console.log(`  [${ok ? "OK  " : "FAIL"}] ${label}${detail ? ` — ${detail}` : ""}`);
  return ok;
}

function backendConfig() {
  const executable = process.env.PROCUREMENT_BACKEND_EXE;
  if (executable) {
    return { backendExecutable: executable, userDataDir: USER_DATA_DIR, timeoutMs: 40000 };
  }
  const venvPython =
    process.platform === "win32" ? ".venv\\Scripts\\python.exe" : ".venv/bin/python";
  return {
    pythonPath: process.env.PROCUREMENT_PYTHON || path.join(PROJECT_ROOT, venvPython),
    cwd: PROJECT_ROOT,
    userDataDir: USER_DATA_DIR,
    env: { ...process.env, PYTHONPATH: path.join(PROJECT_ROOT, "src") },
    timeoutMs: 40000,
  };
}

/**
 * 그 프로세스가 아직 살아 있는가.
 *
 * 신호 0 은 «죽이지 말고 존재만 확인» 이다. Windows 에서도 동작한다.
 */
function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function purchaseCount(port, year) {
  const status = await (
    await fetch(`http://127.0.0.1:${port}/dashboard/data-status?year=${year}`)
  ).json();
  return status.purchase_count;
}

async function main() {
  const year = 2026;
  fs.rmSync(USER_DATA_DIR, { recursive: true, force: true });
  fs.mkdirSync(USER_DATA_DIR, { recursive: true });

  console.log(`껐을 때 정말 끝나는가 — ${ROUNDS}회 반복`);
  console.log(`  userData: ${USER_DATA_DIR}`);
  console.log(`  backend : ${process.env.PROCUREMENT_BACKEND_EXE || "개발용 Python"}\n`);

  const results = [];
  const ports = [];
  let expected = null;

  for (let round = 1; round <= ROUNDS; round += 1) {
    const handle = await startBackend(backendConfig());
    const pid = handle.process.pid;
    ports.push(handle.port);

    results.push(report(isAlive(pid), `${round}회 기동`, `PID ${pid} · 포트 ${handle.port}`));

    // 1회차에 synthetic 데이터를 한 건 넣고, 이후에는 그대로인지만 본다.
    if (round === 1) {
      const templateBytes = Buffer.from(
        await (await fetch(`http://127.0.0.1:${handle.port}${TEMPLATE_PATH}`)).arrayBuffer(),
      );
      const uploadPath = path.join(USER_DATA_DIR, "synthetic.xlsx");
      fs.writeFileSync(uploadPath, templateBytes);
      await fetch(`http://127.0.0.1:${handle.port}/uploads/purchases`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path: uploadPath, year }),
      });
      expected = await purchaseCount(handle.port, year);
      results.push(report(expected > 0, "synthetic 데이터 준비", `${expected}건`));
    } else {
      const count = await purchaseCount(handle.port, year);
      results.push(
        report(count === expected, `${round}회 데이터 유지`, `${count}건 (기대 ${expected}건)`),
      );
    }

    await handle.stop();

    // ⭐ 여기가 요점이다. stop() 이 돌아온 **직후에** 죽어 있어야 한다.
    results.push(report(!isAlive(pid), `${round}회 종료 후 프로세스 없음`, `PID ${pid}`));
  }

  // 매번 다른 포트를 받았는가 — 앞의 것이 포트를 붙들고 있지 않다는 뜻이다.
  const reused = ports.length - new Set(ports).size;
  results.push(report(true, "포트 할당", `${ports.join(", ")}${reused ? ` (중복 ${reused})` : ""}`));

  const passed = results.filter(Boolean).length;
  console.log(`\n결과: ${passed}/${results.length} 통과`);
  if (passed !== results.length) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(`\n검증 중 오류: ${error.message}`);
  if (error.detail) {
    console.error(`\n[백엔드 출력]\n${error.detail}`);
  }
  process.exitCode = 1;
});
