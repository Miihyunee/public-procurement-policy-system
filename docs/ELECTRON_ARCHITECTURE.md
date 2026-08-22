# Electron 데스크톱 전환 — 아키텍처 분석 및 결정

## 문서 정보

| 항목 | 값 |
|---|---|
| 작성일 | 2026-08-15 |
| 근거 | **저장소 실측 + 실행 검증** (`scripts/verify-backend.js` 8/8 통과) |
| 상태 | 구조 결정 완료 · **Windows 배포 방식 미확정 (PM 결정 대기)** |

> 최종 제품은 공공구매 담당자가 Windows PC 에 설치해 쓰는 **Electron 데스크톱
> 애플리케이션**이다. 사용자는 Python · FastAPI · SQLite · Electron 을 알 필요가 없다.

---

# 1. 현재 구조 실측

| 항목 | 실측값 |
|---|---|
| Python 패키지 | `src/procurement` — 12개 하위 패키지 |
| 웹 프레임워크 | FastAPI + uvicorn |
| 화면 | **`web/static/index.html` 단일 파일 (34.6 KB)** |
| 외부 리소스 | **0건** — CDN·폰트·차트 라이브러리 없음 (폐쇄망 대응 완료) |
| API 엔드포인트 | 7개 (`/`, `/dashboard/*` 4개, `/policies` 2개) |
| DB | SQLite. 기본 경로 `<프로젝트>/database/procurement.db` |
| CLI | `init` · `run` · `health` (`python -m procurement`) |
| 테스트 | 910 passed / ruff / mypy strict |
| **Node** | **v22.22.2** |
| **npm** | **10.9.7** |
| **package.json** | ❌ 없었음 → 이번에 생성 |
| **node_modules** | ❌ 없음 (설치 미수행) |

## 1.1 🔑 결정을 좌우한 두 가지 사실

### ① 화면이 **상대 경로**로 API 를 호출한다

```javascript
fetchJson("/dashboard/data-status?year=" + year)   // index.html:835
fetchJson("/dashboard/policy-display")             // index.html:840
```

`file://` 로 열면 이 경로가 전부 깨진다.
→ **화면을 그대로 재사용하려면 localhost HTTP 로 서빙해야 한다.**
→ 반대로 그렇게만 하면 **프런트엔드를 한 줄도 고치지 않아도 된다.**

### ② DB 위치가 **환경변수로 이미 제어 가능하다**

```
DATABASE_PATH=/tmp/electron-userdata  →  /tmp/electron-userdata/procurement.db
```

`Settings.DATABASE_PATH` 가 환경변수를 읽으므로,
**Python 코드를 고치지 않고** `app.getPath("userData")` 로 옮길 수 있다.

---

# 2. 아키텍처 결정

## 2.1 검토한 3안

| 안 | 구조 | 판정 |
|---|---|---|
| **A** | Electron → **Python backend(FastAPI localhost)** → SQLite | ✅ **채택** |
| B | Electron → Python 실행파일 → SQLite | 배포 단계에서 A 의 하위 형태로 흡수 |
| C | Electron 안에서 SQLite 직접 접근 (JS 재작성) | ❌ **탈락** |

## 2.2 채택안 — A

```text
Electron main process
  ├─ (1) python -m procurement init      ← DB 준비 (멱등 · 데이터 보존)
  ├─ (2) python -m procurement run       ← 빈 포트 자동 선택
  ├─ (3) GET / 폴링으로 준비 확인
  └─ (4) BrowserWindow → http://127.0.0.1:<포트>/
                              │
                       기존 index.html 그대로
                              │
       FastAPI → Calculator → Repository → SQLite
                                            └ userData/database/procurement.db
```

**C 를 탈락시킨 이유**: 달성률 계산·인증 유효기간 판정·상계·기간 필터를
JavaScript 로 다시 쓰면 **910개 테스트로 지켜온 업무규칙을 전부 재검증**해야 한다.
지시서 §26-18 이 금지한 "Python 업무 로직 전체 재작성" 에 해당한다.

**B 를 별도 안으로 두지 않은 이유**: A 의 `pythonPath` 를 `backendExecutable` 로
바꾸기만 하면 된다. 이미 `buildCommand()` 가 두 경우를 모두 지원한다.
즉 **배포 방식 결정이 아키텍처를 바꾸지 않는다.**

## 2.3 재사용 범위

| 계층 | 처리 |
|---|---|
| Calculator · Rule Engine · Repository · Matcher · Importer | ✅ **무변경 재사용** |
| FastAPI 앱 · API 응답 모델 | ✅ **무변경 재사용** |
| Dashboard 화면 (`index.html`) | ✅ **무변경 재사용** |
| CLI (`init` / `run` / `health`) | ✅ **무변경 재사용** — Electron 이 호출 |
| **신규** | Electron main · preload · 백엔드 생명주기 관리 |

> **Python 코드 변경 0줄로 데스크톱 앱이 동작한다.**

---

# 3. 실행 검증 결과

Electron 바이너리 없이 백엔드 생명주기만 검증하는 스크립트를 만들어 실행했다.
GUI·Windows 없이 헤드리스에서 돌아가므로 CI 에도 넣을 수 있다.

```
$ node scripts/verify-backend.js

  [OK] 백엔드 기동 — 포트 39499              ← 빈 포트 자동 선택
  [OK] 대시보드 화면 응답 — HTTP 200
  [OK] 기존 화면 그대로 재사용 — 차트 컨테이너 확인
  [OK] 외부 리소스 미사용 — 오프라인·폐쇄망에서도 동작
  [OK] 정책 API 응답 — HTTP 200
  [OK] 표준 양식 다운로드 — 구매실적_표준양식.xlsx · 6,404 bytes
  [OK] 내려받은 양식이 검증을 통과 — 총 1행 · 정상 1행
  [OK] 검증만 수행하고 저장하지 않음
  [OK] 잘못된 파일을 오류로 안내
  [OK] DB 가 사용자 데이터 디렉터리에 생성됨 — database/procurement.db
  [OK] 설치 디렉터리를 오염시키지 않음
  [OK] 종료 시 백엔드 정리

결과: 12/12 통과
```

업로드 4건은 Electron 이 실제로 하는 일(양식을 내려받아 저장 → 그 경로를
백엔드에 넘겨 검증)을 GUI 없이 그대로 재현한 것이다.

## 3.1 🔴 검증 과정에서 드러난 요건 — 최초 실행 처리

첫 시도는 **실패**했다. `run` 이 서버를 띄우지 않고 이렇게 안내하며 종료했다.

```
[FAIL] DB 파일 — 파일이 없습니다: .../procurement.db.
       'python -m procurement init' 을 실행하세요.
```

F-1 기능이 의도대로 동작한 것이다. 다만 **데스크톱 사용자는 명령줄을 쓰지 않으므로**
앱이 대신 처리해야 한다(지시서 §24).

→ `ensureDatabase()` 를 추가해 기동 전에 `init` 을 한 번 실행하도록 했다.

| `init` 의 보장 | 근거 |
|---|---|
| **멱등** | 여러 번 실행해도 안전 (테스트로 고정) |
| **기존 데이터를 삭제하지 않음** | 없는 테이블·컬럼만 추가 |
| 구 스키마 자동 보완 | `migrate_schema()` — `ALTER TABLE ADD COLUMN` |
| 기본 정책 등록 | 이미 있으면 건너뜀 (목표율 덮어쓰지 않음) |

> ⛔ **데이터를 삭제하는 마이그레이션은 하지 않는다**(§26-16).

---

# 4. 파일 구성

| 파일 | 역할 | Electron 의존 |
|---|---|---|
| `package.json` | 앱 메타·스크립트·devDependencies | — |
| `electron/backend.js` | 백엔드 생명주기 (포트·DB경로·init·기동·정리) | ❌ **없음** |
| `electron/main.js` | Electron 결합 (창·메뉴·오류 안내) | ✅ |
| `electron/preload.js` | 렌더러 노출 최소화 | ✅ |
| `electron/uploads.js` | 파일 대화상자 · 양식 저장 (업무 로직 없음) | ✅ (주입식) |
| `scripts/verify-backend.js` | 헤드리스 검증 | ❌ 없음 |

**`backend.js` 가 Electron 을 import 하지 않는 이유**: 가장 깨지기 쉬운 부분
(자식 프로세스·준비 판정·DB 경로)을 Electron 바이너리 없이 검증하기 위함이다.

## 4.1 보안 설정

```javascript
contextIsolation: true    nodeIntegration: false    sandbox: true
```

preload 가 노출하는 것은 **네 가지뿐**이다.

| 키 | 역할 |
|---|---|
| `isDesktop` | 데스크톱 실행 여부(화면이 브라우저 모드와 구분) |
| `saveTemplate()` | 표준 양식 저장 위치를 고르고 저장 |
| `selectUploadFile()` | 업로드할 파일을 고르고 **경로만** 반환 |
| `versions` | 진단용 버전 정보 |

⛔ `ipcRenderer` 자체를 노출하지 않는다(임의 채널 호출 방지). 파일 시스템·프로세스를
여는 것이 아니라 **미리 정해진 동작만** 요청할 수 있다. 파일 **내용**은 렌더러로
넘어가지 않으며, 엑셀 해석·검증은 전부 Python 이 한다.

외부 링크는 `setWindowOpenHandler` 로 기본 브라우저에 넘긴다.

## 4.1.1 업로드 경로

```text
렌더러 [파일 선택]  →  preload.selectUploadFile()  →  main: dialog.showOpenDialog
                                                          ↓ 경로 문자열
렌더러 [검증 실행]  →  POST /uploads/purchases/validate {file_path}
                                                          ↓
                        Python: excel_adapter → validation → 결과 JSON
```

파일 본문을 네트워크로 다시 실어 보내지 않는다. 백엔드가 같은 PC 의
`127.0.0.1` 전용 자식 프로세스이기 때문이다(선택 근거: `UPLOAD_PIPELINE_DESIGN.md`
§4.5.1).

## 4.2 오류 안내

`BackendStartError` 가 **사용자 메시지**(`message`)와 **진단 정보**(`detail`)를
분리한다. stack trace 를 그대로 보여주지 않는다(§21).

```
프로그램을 시작하지 못했습니다

데이터 저장소를 준비하지 못했습니다.

다음을 확인해 주세요.
  · 프로그램을 다시 실행해 보세요.
  · 문제가 계속되면 관리자에게 아래 내용을 전달해 주세요.
```

---

# 5. 🔴 미확정 — PM 결정 필요

## 5.1 Windows 배포 방식

| 항목 | 상태 |
|---|---|
| Electron Builder 도입 | 🔴 미결정 — `package.json` 에 아직 넣지 않음 |
| Python 백엔드 번들 방식 | 🔴 미결정 — PyInstaller / embeddable Python / 사전 설치 |
| 코드 서명 | 🔴 미검토 |
| 설치 파일 형식 | 🔴 미검토 (NSIS / MSI / portable) |

> 지시서 §30 "지금 당장 배포 설정을 확정하지 말고" 에 따라 넣지 않았다.

## 5.2 Electron 실행 검증 상태

| 항목 | 상태 |
|---|---|
| 백엔드 생명주기 (헤드리스) | ✅ **검증 완료** (14/14) |
| `npm install` | ✅ **완료** — Electron 43.4.0 |
| **Electron 실제 기동** | ✅ **검증 완료** (2026-08-17, Xvfb) |
| 백엔드 자동 기동·포트 선택 | ✅ 실기동 확인 |
| renderer 로딩 (실제 창) | ✅ 실기동 확인 |
| preload 노출 범위 | ✅ 실기동 확인 — 4키만, `require`/`process` 미노출 |
| 네이티브 대화상자 호출 | ✅ 실제 호출됨 (열림 확인) |
| 앱 종료 시 백엔드 정리 | ✅ 실기동 확인 |
| 재시작 후 DB 유지 | ✅ 실기동 확인 |
| 포트 충돌 회피 | ✅ 2개 인스턴스 동시 기동 확인 |
| 대화상자 **조작** | 🟡 자동화 불가 — 이 환경에 `xdotool` 없음 |
| **Windows 동작** | ❌ **NOT VERIFIED** — Windows 환경 없음 |

### Windows 에서 반드시 확인해야 할 항목

Linux 에서 통과했다는 이유로 아래를 통과로 표시하지 않는다.

| 항목 | 왜 다를 수 있는가 |
|---|---|
| Windows Python 실행 | `python` / `py` 런처 · 가상환경 경로 규칙이 다르다 |
| subprocess 종료 동작 | Windows 에 `SIGTERM` 이 없다. 현재 코드는 `SIGTERM` 후 `SIGKILL` 순서를 쓴다 |
| 경로 처리 | 구분자 · 드라이브 문자 · 공백/한글 경로 |
| SQLite userData 경로 | `%APPDATA%` 아래로 바뀐다 |
| Electron 설치/패키징 | electron-builder 미도입 — 배포 방식 자체가 미결정 |
| native file dialog | GTK 가 아니라 Windows 공용 대화상자 |
| 앱 종료 lifecycle | `window-all-closed` · `before-quit` 동작 차이 |

### 실기동 검증 방법 (2026-08-17)

GUI 가 없는 환경이라 **Xvfb 가상 디스플레이**에서 실제 Electron 바이너리를
띄우고, 렌더링은 CDP(`--remote-debugging-port`)로, 네이티브 대화상자는 메인
프로세스 **Node 인스펙터**(`--inspect`)로 미리 답하게 하여 확인했다.

⛔ **제품 코드에 테스트용 분기를 넣지 않았다.** 대화상자만 밖에서 대신
답하게 했고, 버튼 핸들러·IPC·fetch·백엔드·저장은 전부 실제 코드가 돌았다.

> `contextBridge` 로 노출된 `window.procurementApp` 은 **불변**이라 렌더러에서
> 덮어쓸 수 없음을 실측으로 확인했다(보안상 올바른 동작). 그래서 메인 프로세스
> 쪽에서 대화상자를 스텁해야 했다.

## 5.3 알려진 이식성 고려사항

| 항목 | 내용 |
|---|---|
| 프로세스 종료 | `SIGTERM` → Windows 에서는 동작이 다르다. 검증 필요 |
| 경로 구분자 | `path.join` 사용으로 대응했으나 실측 필요 |
| 방화벽 | localhost 바인딩이라 인바운드 규칙 불필요할 것으로 보이나 확인 필요 |
| 한글 경로 | 사용자명이 한글인 `userData` 경로에서의 동작 확인 필요 |

---

# 6. 다음 단계 제안

| 순서 | 작업 | 선행 조건 |
|---|---|---|
| 1 | `npm install` → Electron 실제 기동 확인 | 디스크·네트워크 |
| 2 | Windows 실환경 검증 | Windows PC |
| 3 | 표준 Excel 업로드 화면·API | `STANDARD_UPLOAD_FORMAT.md` 확정 |
| 4 | 정책별 상세 화면 | 3번 이후 |
| 5 | electron-builder 배포 설정 | PM 승인 |

> 3번(업로드)이 사용자 가치가 가장 크다. 현재는 데이터를 넣을 방법이 없어
> 대시보드가 빈 상태로만 뜬다.
