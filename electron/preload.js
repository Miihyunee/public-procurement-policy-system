/**
 * electron/preload.js
 *
 * 렌더러(대시보드 화면)에 노출할 최소한의 정보만 전달한다.
 *
 * .. warning::
 *     **Node API 를 렌더러에 열지 않는다.** 화면은 기존 웹 대시보드를 그대로
 *     재사용하며, 데이터는 전부 localhost HTTP API 로 주고받는다. 따라서
 *     preload 에서 파일 시스템·프로세스 접근을 노출할 이유가 없다.
 *
 *     `contextIsolation: true` · `nodeIntegration: false` · `sandbox: true`
 *     설정과 함께 동작한다.
 *
 *     업로드 기능 때문에 **파일 대화상자 두 개**만 추가로 노출한다. 파일
 *     시스템·프로세스 자체를 여는 것이 아니라, 미리 정해진 동작만 요청할 수
 *     있는 함수다. 고른 파일의 **경로만** 화면으로 돌아오며 파일 내용은
 *     넘기지 않는다. 엑셀 해석·검증은 전부 Python 백엔드가 한다.
 */

"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("procurementApp", {
  /** 데스크톱 앱에서 실행 중인지 여부. 화면이 필요하면 참고할 수 있다. */
  isDesktop: true,
  /**
   * 표준 업로드 양식을 저장할 위치를 고르고 저장한다.
   *
   * @returns {Promise<{saved: boolean, path?: string, message?: string}>}
   */
  saveTemplate: () => ipcRenderer.invoke("uploads:saveTemplate"),
  /**
   * 업로드할 엑셀 파일을 고른다. **경로만** 돌려준다.
   *
   * @returns {Promise<{selected: boolean, path?: string, name?: string}>}
   */
  selectUploadFile: () => ipcRenderer.invoke("uploads:selectFile"),
  /** Electron 버전(진단용). */
  versions: {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
  },
});
