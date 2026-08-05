# Issue #22 명세서 — Dashboard 실제 사용 가능화 (실행 경로 / 진입점)

- 문서 유형: **설계 명세 (Spec)** — 구현은 본 명세 검토·승인 후 진행
- 작성 목적: 지금까지 만든 대시보드 데이터·응답 계층(`DashboardDataService` → `DashboardApiService`)을
  **실제 사용자가 실행해 결과를 볼 수 있는 형태**로 연결한다. (조립 경로 + 전달 표면 설계)
- 관련 코드:
  - `procurement.api.dashboard_api.DashboardApiService` (Issue #21, 응답 모델 반환)
  - `procurement.dashboard.data_service.DashboardDataService` (Issue #19·#20-2)
  - `procurement.calculators.*`, `procurement.database.*` (계산·저장 계층)
  - `procurement.__main__` (현재 버전 문자열만 출력하는 stub)
  - `procurement.core.config.settings` (DB 경로 등 설정)

> ⚠️ 본 명세는 **문서만** 포함한다. 코드·테스트·의존성 변경은 없다.

---

## 0. PM 최종 결정 (확정)

> 아래 결정으로 **본 명세의 권장안(CLI)은 채택되지 않았다.** 최종 목표는 **Web Dashboard**이며,
> 이번 Issue #22 는 그 백엔드인 **FastAPI 서버 + Dashboard API Endpoint** 구현까지를 목표로 한다.

| 항목 | 결정 |
|---|---|
| 전달 표면 (Q1) | **FastAPI 도입** (CLI 미구현) |
| 출력 형식 (Q2) | **JSON 전용** (table 미구현) |
| 조립 지점 (Q3) | **`src/procurement/app.py`** 로 분리. `__main__` 은 최대한 얇게 유지 |
| 범위 (Q4) | **등록 목표율 기반 Dashboard 만** — `GET /dashboard/summary` 만 구현. 외부 목표율 입력은 후속 Issue |
| 성공 기준 | **Swagger(OpenAPI) 문서가 동작**하면 성공 |

구조(유지):
```
FastAPI → DashboardApiService → DashboardDataService → Calculator → Repository
```
- `DashboardApiService` 만 `DashboardDataService` 를 호출한다.
- Calculator 직접 호출 금지 / Repository 직접 접근 금지.

> 아래 4장의 방안 비교·CLI 설계는 **의사결정 기록(history)** 으로 보존한다. 실제 구현은 본 0장 결정을 따른다.

---

## 1. 배경 및 목표

### 1.1 현재까지의 진행
- 데이터 계층: `DashboardDataService` 가 계산기 결과를 요약 DTO(`DashboardSummary`)로 조합.
- 응답 계층: `DashboardApiService` 가 요약을 **Pydantic 응답 모델**(`DashboardResponseModel`)로 변환
  (Decimal→문자열, status+status_label). — Issue #21 완료.

### 1.2 문제 (As-Is 한계)
- **실행 경로가 없다.** `DashboardApiService` 는 존재하지만, 이를 **생성·호출하는 코드가 없다.**
  - 저장소(`*Repository`) → 계산기(`ProcurementAchievementCalculator`) → `DashboardDataService`
    → `DashboardApiService` 로 이어지는 **의존성 조립(composition root)** 이 어디에도 없다.
- `procurement.__main__.main()` 은 아래처럼 **버전 문자열만 출력**한다.
  ```
  Public Procurement Policy System v0.1.0
  Status: initialized — business logic not yet implemented.
  ```
- 결과적으로 사용자는 **대시보드 데이터를 실제로 볼 방법이 없다.**

### 1.3 목표 (이번 단계)
- 실제 DB(`settings.db_file`)를 바라보는 **조립 경로**를 만들고,
- 사용자가 **명령 한 번으로 대시보드 요약을 확인**할 수 있는 **전달 표면**을 제공한다.
- 계층 원칙과 하위호환을 유지한다(아래 3장·5장).

---

## 2. 현황 분석 (As-Is)

| 계층 | 상태 |
|---|---|
| Repository (`database/*`) | ✅ 구현 완료 |
| Calculator (`ProcurementAchievementCalculator`) | ✅ 구현 완료 |
| `DashboardDataService` | ✅ 구현 완료 (`build_summary`, `build_summary_from_registered_targets`) |
| `DashboardApiService` | ✅ 구현 완료 (응답 모델 반환) |
| **Composition root (의존성 조립)** | ❌ **없음** |
| **사용자 진입점 (CLI/HTTP/UI)** | ❌ **없음** (`__main__` 은 stub) |

- 프로젝트 의존성: `pydantic`, `pydantic-settings`, `python-dotenv` 만 존재. **웹 프레임워크 없음.**
- 설정: `settings.db_file` 로 SQLite 경로가 결정됨(이미 존재).

---

## 3. 계층 원칙 (유지)

```
Repository → Calculator → DashboardDataService → DashboardApiService → [진입점(신규)]
```

- 진입점(CLI/HTTP)은 **오직 `DashboardApiService` 만** 호출한다.
- `DashboardDataService`/`Calculator`/`Repository` 를 진입점에서 직접 만지지 않는다
  (단, **조립(생성·주입)** 은 composition root 한 곳에서만 수행 — 아래 4.2).

---

## 4. 설계안

### 4.1 전달 표면 선택 (핵심 결정 사항)

| 방안 | 요지 | 장점 | 단점 |
|---|---|---|---|
| **A: CLI 명령** | `procurement dashboard` 로 응답 모델을 콘솔에 출력(JSON/표) | **의존성 0**, 기존 stdlib·no-web 원칙 유지, 즉시 "실제 사용" 달성, 테스트 용이 | HTTP 노출은 아님(후속) |
| B: FastAPI HTTP 엔드포인트 | `GET /dashboard` 등으로 응답 모델을 REST 노출 | 표준 REST·OpenAPI, 응답 모델 그대로 재사용 | **신규 의존성**(FastAPI+ASGI), 범위 확대, 서버 운영 필요 |
| C: Web UI / Chart | 프론트엔드 화면·차트 | 최종 사용자 친화 | 범위 매우 큼(별도 트랙), 백엔드 노출(B) 선행 필요 |

**권장: 방안 A (CLI 명령) — 이번 단계 MVP**
- 근거: 응답 모델(`DashboardResponseModel`)이 이미 `model_dump()`/`model_dump_json()` 으로
  **직렬화 가능**하므로, CLI 는 얇은 표현 계층만 추가하면 된다(신규 의존성 0).
- FastAPI(방안 B)는 **Issue #21에서 이미 "후속 분리"로 합의**된 항목 → 별도 Issue 로 진행.
- UI/Chart(방안 C)는 B 이후 별도 트랙.

### 4.2 Composition Root (의존성 조립) 설계

전달 표면과 무관하게 **공통으로 필요한** 조립 지점을 한 곳에 둔다(예: `procurement/app.py`
또는 `procurement/composition.py` — 이름은 구현 시 확정).

```python
# 개념 예시 (구현 아님)
def build_dashboard_api(db_path: Path) -> DashboardApiService:
    purchase_repo = PurchaseRepository(db_path)
    cert_repo = CertificationRepository(db_path)
    policy_repo = PolicyRepository(db_path)
    calculator = ProcurementAchievementCalculator(purchase_repo, cert_repo, policy_repo)
    data_service = DashboardDataService(calculator, policy_repository=policy_repo)
    return DashboardApiService(data_service)
```

- DB 경로는 `settings.db_file` 기본값 사용(설정으로 재정의 가능).
- `policy_repository` 를 주입하므로 `get_dashboard()`(등록 목표율 기반) 사용 가능.
- 이 함수 하나만 진입점(CLI/HTTP)이 호출 → 조립 로직 중복 없음.

### 4.3 CLI 설계 (방안 A 기준)

- `procurement.__main__` 에 **서브커맨드**를 도입(표준 라이브러리 `argparse`).
  - `procurement dashboard` → 등록된 목표율 기반 요약 출력(`api.get_dashboard()`).
  - 옵션(초안):
    - `--format {json,table}` (기본 `table`) — 사람이 읽는 표 / 기계용 JSON.
    - `--db PATH` (기본 `settings.db_file`) — DB 경로 재정의.
- 출력:
  - `json`: `response.model_dump_json(indent=2)` (Decimal→문자열, status+label 규칙 그대로).
  - `table`: 정책별 코드·정책명·구매액·목표율·달성률·부족률·상태(라벨) 열 정렬 출력.
- 오류 처리:
  - `CalculatorValidationError` / `ValueError`(설정 문제) 발생 시, **사용자용 메시지 + 비정상 종료코드**
    (예: exit code 1)로 표시. 스택트레이스 노출 최소화.

### 4.4 출력 예시 (개념)

`--format json`:
```json
{
  "total_purchase_amount": "10000000",
  "policies": [
    {
      "policy_code": "SMALL_BUSINESS",
      "policy_name": "중소기업",
      "purchase_amount": "3000000",
      "target_rate": "50",
      "achievement_rate": "60.00",
      "shortage_rate": "40.00",
      "status": "SHORTAGE",
      "status_label": "부족"
    }
  ]
}
```

`--format table` (개념):
```
전체 구매액: 10,000,000

정책코드         정책명     구매액       목표율  달성률   부족률   상태
SMALL_BUSINESS  중소기업   3,000,000    50%     60.00%   40.00%   부족
```

---

## 5. 하위호환·범위

### 5.1 하위호환 (변경 없음)
- `DashboardApiService` / `DashboardDataService` / `Calculator` / `Repository` **시그니처·동작 무변경**.
- 이번 단계는 **조립 + 얇은 표현(CLI) 계층만 추가**한다.
- 기존 `__main__.main()` 의 기본 출력(인자 없이 실행)은 **유지하거나 도움말로 대체**(구현 시 확정).

### 5.2 범위 밖 (하지 않는 것)
- FastAPI / HTTP 서버 / 라우터 / 인증·권한 (방안 B → 후속 Issue)
- Web UI / Chart (방안 C → 후속 트랙)
- 정책 목표율 등록/수정 기능(별도 관리 화면 영역)
- 데이터 수집(collectors)·매칭(matchers) 실제 파이프라인

---

## 6. 구현 범위 (승인 시, 방안 A 기준)

| 항목 | 포함 |
|---|---|
| Composition root (`build_dashboard_api` 등 조립 함수) | ✅ |
| `__main__` 서브커맨드(`dashboard`) + `argparse` | ✅ |
| 출력 포매터(json / table) | ✅ |
| 오류 처리(사용자 메시지 + 종료코드) | ✅ |
| 단위 테스트(조립·CLI 출력·오류 종료코드) | ✅ |
| README 실행 예시 갱신 | ✅ |
| FastAPI / HTTP 노출 | ❌ (후속 Issue) |
| UI / Chart | ❌ |

---

## 7. PM 확인 필요 사항 (Open Questions)

| # | 질문 | 기본 제안 |
|---|---|---|
| Q1 | 전달 표면: 방안 A(CLI) 로 진행? | **A 권장** (B FastAPI 는 후속 Issue) |
| Q2 | CLI 기본 출력 형식 = 표(table) / JSON 중? | table(사람 우선), `--format json` 병행 |
| Q3 | 조립 지점 위치 = `procurement/app.py`(신규) / `__main__` 내부 중? | 별도 모듈(`app.py`)로 분리(재사용·테스트 용이) |
| Q4 | `dashboard` 는 등록 목표율(`get_dashboard`)만? 외부 목표율 입력도 CLI 노출? | 등록 목표율만(단순). 외부 입력은 후속 |
| Q5 | 인자 없이 `procurement` 실행 시 = 도움말 / 기존 버전문구 유지 중? | 도움말 출력(서브커맨드 안내) |
| Q6 | FastAPI 노출은 별도 Issue 로 분리? | 분리(방안 B) |

---

## 8. 요약
- `DashboardApiService` 까지 만들었지만 **실행 경로(조립 + 진입점)** 가 없어 실제로 볼 수 없다.
- 이번 단계는 **composition root + CLI(`procurement dashboard`)** 로 "실제 사용 가능"을 달성한다(방안 A, 의존성 0).
- 진입점은 **`DashboardApiService` 만** 호출하여 계층 원칙을 지키고, 기존 계층은 변경하지 않는다.
- FastAPI(HTTP)·UI·Chart 는 후속으로 분리한다.
- 위 Open Questions(특히 Q1 전달 표면, Q2 출력 형식) 확정 후 구현 착수.
