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
 */

"use strict";

const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("procurementApp", {
  /** 데스크톱 앱에서 실행 중인지 여부. 화면이 필요하면 참고할 수 있다. */
  isDesktop: true,
  /** Electron 버전(진단용). */
  versions: {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
  },
});
