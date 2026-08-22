/**
 * electron/uploads.js
 *
 * 업로드 관련 **데스크톱 기능만** 담당한다.
 *
 *   · 표준 양식 저장 위치 선택 + 파일 쓰기
 *   · 업로드할 엑셀 파일 선택
 *
 * .. warning::
 *     ⛔ **업무규칙을 여기에 구현하지 않는다.** 엑셀 해석·검증·저장은 전부
 *     Python 백엔드가 한다. 이 파일이 하는 일은 "사용자에게 파일 대화상자를
 *     보여주고, 고른 경로를 백엔드에 알려주는 것" 뿐이다.
 *
 *     화면(렌더러)에는 파일 **경로 문자열**만 전달하며, 파일 내용을 렌더러로
 *     넘기지 않는다.
 */

"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");

/** 표준 양식 내려받기 엔드포인트. */
const TEMPLATE_PATH = "/uploads/template";

/** 저장 대화상자 기본 파일명. 백엔드 응답 헤더가 없을 때만 사용한다. */
const DEFAULT_TEMPLATE_NAME = "구매실적_표준양식.xlsx";

/** 엑셀 파일 필터. */
const EXCEL_FILTERS = [{ name: "Excel 통합 문서", extensions: ["xlsx"] }];

/**
 * `Content-Disposition` 헤더에서 파일명을 꺼낸다.
 *
 * 백엔드가 한글 파일명을 `filename*=UTF-8''...` 형식으로 보내므로 그것을 우선
 * 사용하고, 없으면 기본값을 쓴다.
 *
 * @param {string | null} header
 * @returns {string}
 */
function fileNameFromDisposition(header) {
  if (!header) {
    return DEFAULT_TEMPLATE_NAME;
  }
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1]);
    } catch {
      return DEFAULT_TEMPLATE_NAME;
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : DEFAULT_TEMPLATE_NAME;
}

/**
 * 표준 양식을 백엔드에서 받아 사용자가 고른 위치에 저장한다.
 *
 * @param {object} options
 * @param {number} options.port 백엔드 포트.
 * @param {object} options.dialog Electron `dialog` 모듈.
 * @param {object | null} options.window 부모 창(모달 표시용).
 * @returns {Promise<{saved: boolean, path?: string, message?: string}>}
 */
async function saveTemplate({ port, dialog, window }) {
  let response;
  try {
    response = await fetch(`http://127.0.0.1:${port}${TEMPLATE_PATH}`);
  } catch (error) {
    return { saved: false, message: `양식을 받아오지 못했습니다: ${error.message}` };
  }
  if (!response.ok) {
    return { saved: false, message: `양식을 받아오지 못했습니다 (HTTP ${response.status}).` };
  }

  const suggested = fileNameFromDisposition(response.headers.get("content-disposition"));
  const result = await dialog.showSaveDialog(window ?? undefined, {
    title: "표준 업로드 양식 저장",
    defaultPath: suggested,
    filters: EXCEL_FILTERS,
  });
  if (result.canceled || !result.filePath) {
    return { saved: false };
  }

  const buffer = Buffer.from(await response.arrayBuffer());
  try {
    await fs.writeFile(result.filePath, buffer);
  } catch (error) {
    return { saved: false, message: `파일을 저장하지 못했습니다: ${error.message}` };
  }
  return { saved: true, path: result.filePath };
}

/**
 * 업로드할 엑셀 파일을 고른다.
 *
 * @param {object} options
 * @param {object} options.dialog Electron `dialog` 모듈.
 * @param {object | null} options.window 부모 창.
 * @returns {Promise<{selected: boolean, path?: string, name?: string}>}
 */
async function selectExcelFile({ dialog, window }) {
  const result = await dialog.showOpenDialog(window ?? undefined, {
    title: "업로드할 파일 선택",
    filters: EXCEL_FILTERS,
    properties: ["openFile"],
  });
  if (result.canceled || result.filePaths.length === 0) {
    return { selected: false };
  }
  const filePath = result.filePaths[0];
  return { selected: true, path: filePath, name: path.basename(filePath) };
}

module.exports = {
  DEFAULT_TEMPLATE_NAME,
  EXCEL_FILTERS,
  TEMPLATE_PATH,
  fileNameFromDisposition,
  saveTemplate,
  selectExcelFile,
};
