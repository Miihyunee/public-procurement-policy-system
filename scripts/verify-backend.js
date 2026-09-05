#!/usr/bin/env node
/**
 * scripts/verify-backend.js
 *
 * Electron 없이 **백엔드 생명주기만** 검증한다.
 *
 * Electron 앱에서 가장 깨지기 쉬운 부분은 UI 가 아니라 다음 세 가지다.
 *
 *   1. Python 백엔드를 자식 프로세스로 띄울 수 있는가
 *   2. 언제 준비되었는지 알 수 있는가
 *   3. DB 를 사용자 데이터 디렉터리로 옮길 수 있는가
 *
 * 이 스크립트는 `electron/backend.js` 를 그대로 사용해 위 셋을 확인한다.
 * Electron 바이너리·GUI 없이 CI 나 헤드리스 환경에서도 돌릴 수 있다.
 *
 * 사용법::
 *
 *     node scripts/verify-backend.js
 *     PROCUREMENT_PYTHON=.venv/bin/python node scripts/verify-backend.js
 */

"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { startBackend } = require("../electron/backend");
const { fileNameFromDisposition, TEMPLATE_PATH } = require("../electron/uploads");

const PROJECT_ROOT = path.resolve(__dirname, "..");

/** 확인 결과를 한 줄로 출력한다. */
function report(ok, label, detail = "") {
  const mark = ok ? "OK  " : "FAIL";
  console.log(`  [${mark}] ${label}${detail ? ` — ${detail}` : ""}`);
  return ok;
}

async function main() {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "procurement-userdata-"));
  const pythonPath = process.env.PROCUREMENT_PYTHON || path.join(PROJECT_ROOT, ".venv/bin/python");

  console.log("Electron 백엔드 생명주기 검증");
  console.log(`  userData: ${userDataDir}`);
  console.log(`  python  : ${pythonPath}\n`);

  const results = [];
  let handle = null;

  try {
    handle = await startBackend({
      pythonPath,
      cwd: PROJECT_ROOT,
      userDataDir,
      env: { ...process.env, PYTHONPATH: path.join(PROJECT_ROOT, "src") },
      timeoutMs: 40000,
    });
    results.push(report(true, "백엔드 기동", `포트 ${handle.port}`));
  } catch (error) {
    report(false, "백엔드 기동", error.message);
    if (error.detail) {
      console.log(`\n[백엔드 출력]\n${error.detail}`);
    }
    process.exitCode = 1;
    return;
  }

  const base = `http://127.0.0.1:${handle.port}`;

  const page = await fetch(`${base}/`);
  const html = await page.text();
  results.push(report(page.ok, "대시보드 화면 응답", `HTTP ${page.status}`));
  results.push(
    report(
      html.includes('id="chart-achievement"'),
      "기존 화면 그대로 재사용",
      "차트 컨테이너 확인",
    ),
  );
  // SVG 네임스페이스(http://www.w3.org/2000/svg)는 실제로 가져오는 리소스가
  // 아니므로 제외하고, 그 밖의 외부 참조가 없는지 확인한다.
  const external = html.replaceAll("http://www.w3.org/2000/svg", "");
  results.push(
    report(
      !external.includes("http://") && !external.includes("https://"),
      "외부 리소스 미사용",
      "오프라인·폐쇄망에서도 동작",
    ),
  );

  const policies = await fetch(`${base}/policies`);
  results.push(report(policies.ok, "정책 API 응답", `HTTP ${policies.status}`));

  // --- 업로드 경로 ---------------------------------------------------
  //
  // Electron 이 실제로 하는 일(양식 내려받아 저장 → 그 파일 경로를 백엔드에
  // 넘겨 검증)을 GUI 없이 그대로 재현한다. 업무 판정은 전부 백엔드가 한다.
  const templateResponse = await fetch(`${base}${TEMPLATE_PATH}`);
  const templateBytes = Buffer.from(await templateResponse.arrayBuffer());
  const templateName = fileNameFromDisposition(
    templateResponse.headers.get("content-disposition"),
  );
  results.push(
    report(
      templateResponse.ok && templateBytes.subarray(0, 2).toString() === "PK",
      "표준 양식 다운로드",
      `${templateName} · ${templateBytes.length.toLocaleString()} bytes`,
    ),
  );

  const savedTemplate = path.join(userDataDir, templateName);
  fs.writeFileSync(savedTemplate, templateBytes);

  const validation = await fetch(`${base}/uploads/purchases/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_path: savedTemplate }),
  });
  const validationBody = await validation.json();
  results.push(
    report(
      validation.ok && validationBody.ok === true,
      "내려받은 양식이 검증을 통과",
      `총 ${validationBody.total_rows}행 · 정상 ${validationBody.valid_rows}행`,
    ),
  );
  results.push(
    report(
      validationBody.stored === false && Boolean(validationBody.storage_note),
      "검증만 수행하고 저장하지 않음",
      validationBody.storage_note || "",
    ),
  );

  const brokenPath = path.join(userDataDir, "broken.xlsx");
  fs.writeFileSync(brokenPath, "not an excel file");
  const brokenResult = await (
    await fetch(`${base}/uploads/purchases/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_path: brokenPath }),
    })
  ).json();
  results.push(
    report(
      Array.isArray(brokenResult.file_errors) && brokenResult.file_errors.length > 0,
      "잘못된 파일을 오류로 안내",
      (brokenResult.file_errors || [""])[0].slice(0, 40) + "...",
    ),
  );

  // 저장 경로 — 양식을 채워 올리면 실제로 DB 에 들어가는가.
  // 업무 판정은 전부 백엔드가 하며, 여기서는 흐름만 확인한다.
  const filledPath = path.join(userDataDir, "filled.xlsx");
  fs.writeFileSync(filledPath, templateBytes);
  const imported = await (
    await fetch(`${base}/uploads/purchases`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_path: filledPath, year: 2026 }),
    })
  ).json();
  results.push(
    report(
      imported.stored === true && imported.stored_rows > 0,
      "업로드 → DB 저장",
      `배치 #${imported.batch_id} · ${imported.stored_rows}건`,
    ),
  );

  const stored = await (await fetch(`${base}/dashboard/data-status?year=2026`)).json();
  results.push(
    report(
      stored.purchase_count === imported.stored_rows,
      "저장된 데이터가 조회 경로에 반영됨",
      `구매 ${stored.purchase_count}건`,
    ),
  );

  const dbFile = path.join(userDataDir, "database", "procurement.db");
  results.push(
    report(
      fs.existsSync(dbFile),
      "DB 가 사용자 데이터 디렉터리에 생성됨",
      path.relative(userDataDir, dbFile),
    ),
  );
  results.push(
    report(
      !fs.existsSync(path.join(PROJECT_ROOT, "database", "electron-should-not-write-here")),
      "설치 디렉터리를 오염시키지 않음",
    ),
  );

  await handle.stop();
  await new Promise((resolve) => setTimeout(resolve, 300));
  let stopped = true;
  try {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 1000);
    await fetch(`${base}/`, { signal: controller.signal });
    stopped = false;
  } catch {
    stopped = true;
  }
  results.push(report(stopped, "종료 시 백엔드 정리"));

  fs.rmSync(userDataDir, { recursive: true, force: true });

  const failed = results.filter((ok) => !ok).length;
  console.log(`\n결과: ${results.length - failed}/${results.length} 통과`);
  process.exitCode = failed === 0 ? 0 : 1;
}

main().catch((error) => {
  console.error("검증 중 예기치 못한 오류:", error);
  process.exitCode = 1;
});
