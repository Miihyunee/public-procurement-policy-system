#!/usr/bin/env node
/**
 * scripts/verify-restart.js
 *
 * **껐다 켜도 데이터가 남는가**만 본다 (STEP 126-2 §6 ⑥⑦⑧).
 *
 * `verify-backend.js` 는 매번 새 폴더에서 시작하므로 재실행을 볼 수 없다.
 * 이 스크립트는 **같은 사용자 데이터 폴더**로 두 번 띄운다.
 *
 *     1회차   백엔드 기동 → 표준 양식 업로드 → 건수 기록 → 종료
 *     2회차   백엔드 기동 → 건수 확인 → 종료
 *
 * 1회차와 2회차의 건수가 같고 0보다 크면 통과다.
 *
 * 사용법::
 *
 *     node scripts/verify-restart.js
 *
 *     # 묶은 실행파일로 (Windows)
 *     $env:PROCUREMENT_BACKEND_EXE = "dist\procurement\procurement.exe"
 *     node scripts/verify-restart.js
 *
 * ⛔ 고객 데이터를 쓰지 않는다. 백엔드가 스스로 내주는 **빈 표준 양식**을
 *    그대로 되올릴 뿐이다.
 */

"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { startBackend } = require("../electron/backend");
const { TEMPLATE_PATH } = require("../electron/uploads");

const PROJECT_ROOT = path.resolve(__dirname, "..");

/** 이 검증에만 쓰는 폴더. 실제 사용자 데이터 폴더를 건드리지 않는다. */
const USER_DATA_DIR = path.join(os.tmpdir(), "procurement-restart-test");

function report(ok, label, detail = "") {
  console.log(`  [${ok ? "OK  " : "FAIL"}] ${label}${detail ? ` — ${detail}` : ""}`);
  return ok;
}

/** 백엔드 실행 설정. 실행파일을 지정했으면 Python 을 쓰지 않는다. */
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

/** 그 해의 저장된 구매 건수를 읽는다. */
async function purchaseCount(port, year) {
  const status = await (
    await fetch(`http://127.0.0.1:${port}/dashboard/data-status?year=${year}`)
  ).json();
  return status.purchase_count;
}

async function main() {
  const year = 2026;

  // ⛔ 앞선 실행이 남긴 것을 지우고 시작한다 — 그래야 결과가 분명하다.
  fs.rmSync(USER_DATA_DIR, { recursive: true, force: true });
  fs.mkdirSync(USER_DATA_DIR, { recursive: true });

  console.log("재실행 후 데이터 유지 검증");
  console.log(`  userData: ${USER_DATA_DIR}`);
  console.log(
    `  backend : ${process.env.PROCUREMENT_BACKEND_EXE || "개발용 Python"}\n`,
  );

  const results = [];

  // --- 1회차 --------------------------------------------------------
  let handle = await startBackend(backendConfig());
  results.push(report(true, "1회차 기동", `포트 ${handle.port}`));

  const templateResponse = await fetch(`http://127.0.0.1:${handle.port}${TEMPLATE_PATH}`);
  const templateBytes = Buffer.from(await templateResponse.arrayBuffer());
  results.push(
    report(
      templateResponse.ok && templateBytes.subarray(0, 2).toString() === "PK",
      "표준 양식 확보",
      `${templateBytes.length.toLocaleString()} bytes`,
    ),
  );

  const uploadPath = path.join(USER_DATA_DIR, "synthetic.xlsx");
  fs.writeFileSync(uploadPath, templateBytes);

  const imported = await (
    await fetch(`http://127.0.0.1:${handle.port}/uploads/purchases`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_path: uploadPath, year }),
    })
  ).json();
  results.push(
    report(imported.stored === true && imported.stored_rows > 0, "업로드 → DB 저장", `${imported.stored_rows}건`),
  );

  const before = await purchaseCount(handle.port, year);
  results.push(report(before > 0, "1회차 저장 건수", `${before}건`));

  await handle.stop();
  results.push(report(true, "1회차 종료"));

  // --- 2회차 --------------------------------------------------------
  handle = await startBackend(backendConfig());
  results.push(report(true, "2회차 기동", `포트 ${handle.port}`));

  const after = await purchaseCount(handle.port, year);
  results.push(
    report(
      after === before && after > 0,
      "⭐ 재실행 후에도 데이터가 남아 있음",
      `1회차 ${before}건 → 2회차 ${after}건`,
    ),
  );

  await handle.stop();
  results.push(report(true, "2회차 종료"));

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
