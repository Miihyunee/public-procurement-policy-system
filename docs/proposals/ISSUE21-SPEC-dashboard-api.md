# Issue #21 명세서 — Dashboard API 계층

- 문서 유형: **설계 명세 (Spec)** — 구현은 본 명세 검토·승인 후 진행
- 작성 목적: `DashboardDataService` 가 생성한 `DashboardSummary` 를 **API 응답 형태**로
  제공하는 API 계층을 설계한다. (UI·Chart 제외, 데이터 제공까지만)
- 관련 코드: `procurement.dashboard.data_service.DashboardDataService` (Issue #19·#20-2),
  `procurement.dashboard.models`(DashboardSummary/PolicySummary/DashboardStatus),
  `procurement.api`(현재 플레이스홀더)

> ⚠️ 본 명세는 **문서만** 포함한다. 코드·테스트·의존성 변경은 없다.

---

## 1. 배경 및 목표

### 1.1 목표 (PM 지시)
- `DashboardSummary` 데이터를 **API 응답 형태**로 변환한다.
- `DashboardDataService` 호출 구조를 **유지**한다(그 위에 API 계층을 얹는다).
- **API 계층을 추가**한다.

### 1.2 범위 밖 (하지 않는 것)
- UI / Chart 구현 (API 데이터 제공까지만)
- Calculator 직접 호출 (금지)
- Repository 직접 접근 (금지)

### 1.3 계층 원칙 (유지)
```
Repository → Calculator → Rule Engine → DashboardDataService → [API 계층(신규)]
```
API 계층은 **오직 DashboardDataService 만** 호출한다.

---

## 2. 현황 분석 (As-Is)

- `src/procurement/api/__init__.py` 는 `TODO: Implement in future issues.` 플레이스홀더.
- 프로젝트 의존성에 **웹 프레임워크(FastAPI/Flask 등)가 없다.** (pydantic, pydantic-settings,
  python-dotenv 만 존재)
- `DashboardSummary`/`PolicySummary` 는 `Decimal`, `DashboardStatus`(Enum) 필드를 포함한다.
  → API 응답으로 내보내려면 **JSON 직렬화 가능한 형태로 변환**이 필요하다.

---

## 3. 요구사항

| # | 요구사항 | 우선순위 |
|---|---|---|
| R1 | DashboardSummary → API 응답(JSON 직렬화 가능) 변환 | 필수 |
| R2 | API 계층은 DashboardDataService 만 호출 (Calculator/Repository 직접 접근 금지) | 필수 |
| R3 | 기존 DashboardDataService 시그니처·동작 변경 없음 | 필수 |
| R4 | Decimal·Enum 등 직렬화 규칙 정의 | 필수 |
| R5 | 오류 상황(잘못된 목표율·존재하지 않는 정책 등)의 응답 규칙 정의 | 필수 |

---

## 4. 설계안

### 4.1 프레임워크 선택 (핵심 결정 사항)

| 방안 | 요지 | 장점 | 단점 |
|---|---|---|---|
| **A: 프레임워크 비의존 응답 계층** | HTTP 서버 없이, DashboardSummary → 직렬화 가능한 dict 를 만드는 API 서비스(facade) | 의존성 0, 순수 로직으로 테스트 용이, 기존 stdlib 원칙 유지 | 실제 HTTP 노출은 이후 별도 작업 |
| B: FastAPI 도입 | FastAPI 라우터 + pydantic 응답 모델 | 표준적 REST, 자동 문서(OpenAPI), pydantic 이미 사용 중 | 신규 의존성·ASGI 서버 필요, 범위 확대 |
| C: Flask 도입 | Flask 라우트 | 단순 | 신규 의존성, pydantic 직렬화 수작업 |

**권장: 방안 A (프레임워크 비의존 응답 계층)**
- PM 지시가 "API 데이터 제공까지만" 이고 현재 웹 프레임워크가 없으므로, 이번 단계는
  **응답 데이터(payload) 생성**에 집중한다.
- HTTP 바인딩(FastAPI 등)이 필요해지면, 방안 A 의 응답 계층을 그대로 재사용해 라우터만
  얹으면 된다(재작업 없음).

### 4.2 API 계층 구조 (방안 A 기준)

```
procurement/api/
├─ __init__.py            # 공개 심볼 re-export
├─ response.py            # 응답 DTO (직렬화 가능) 정의
└─ dashboard_api.py       # DashboardApiService (DashboardDataService 호출 + 변환)
```

- **DashboardApiService**
  - 생성자: `DashboardApiService(dashboard_service: DashboardDataService)`
    (DashboardDataService 를 주입 — Calculator/Repository 직접 접근 없음, R2)
  - 메서드(예시):
    - `get_dashboard()` → 등록된 목표율 기반 요약을 응답 형태로 반환
      (내부에서 `dashboard_service.build_summary_from_registered_targets()` 호출)
    - `get_dashboard_with_targets(target_rates)` → 외부 목표율 입력 방식도 노출
      (내부에서 `dashboard_service.build_summary(target_rates)` 호출, 하위호환)
  - 반환: **직렬화 가능한 응답 객체(dict 또는 응답 DTO)**.

### 4.3 응답 스키마 (제안)

`DashboardSummary` → 다음 JSON 구조로 변환한다.

```json
{
  "total_purchase_amount": "10000000",
  "policies": [
    {
      "policy_id": 1,
      "policy_code": "SMALL_BUSINESS",
      "policy_name": "중소기업",
      "purchase_amount": "3000000",
      "total_purchase_amount": "10000000",
      "target_rate": "50",
      "achievement_rate": "60.00",
      "shortage_rate": "40.00",
      "status": "SHORTAGE",
      "status_label": "부족"
    }
  ]
}
```

### 4.4 직렬화 규칙 (R4)
- **Decimal**: 정밀도 보존을 위해 **문자열(string)** 로 직렬화한다(금액 저장 규약과 동일).
  (부동소수 오차 방지. 숫자형이 필요하면 클라이언트가 파싱.)
- **DashboardStatus(Enum)**: `status`(코드: NORMAL/WARNING/SHORTAGE) + `status_label`
  (한글: 정상/주의/부족) **두 필드**로 제공 → 기계 판별과 화면 표시를 모두 지원.
- **필드명**: snake_case 유지(현 DTO와 일관).

### 4.5 오류 처리 (R5)
- API 계층은 `DashboardDataService`/`Calculator` 의 검증 예외
  (`CalculatorValidationError`, 목표율 미주입 시 `ValueError`)를 그대로 전파하거나,
  **API 표준 오류 응답 형태**로 감싼다. (예: `{"error": {"code": "...", "message": "..."}}`)
- 권장: 이번 단계(방안 A)에서는 **예외를 그대로 전파**하고, HTTP 상태 코드 매핑은 실제
  HTTP 도입 시(후속) 정의한다. → 범위 최소화.

---

## 5. 권장안 요약
- **방안 A**: `procurement/api` 에 프레임워크 비의존 **DashboardApiService** + **응답 DTO** 추가.
- DashboardDataService 만 호출(R2), 기존 서비스 무변경(R3).
- Decimal→string, Enum→code+label 직렬화(R4).
- 오류는 이번 단계에서 그대로 전파, HTTP 매핑은 후속(R5).

---

## 6. 구현 범위 (승인 시)

| 항목 | 포함 |
|---|---|
| `api/response.py` (응답 DTO) | ✅ |
| `api/dashboard_api.py` (DashboardApiService) | ✅ |
| 응답 변환(Decimal/Enum 직렬화) | ✅ |
| 단위 테스트(변환·필드·오류 전파) | ✅ |
| HTTP 서버/라우팅(FastAPI 등) | ❌ (후속, 필요 시 별도 Issue) |
| UI / Chart | ❌ |

---

## 7. PM 확인 필요 사항 (Open Questions)

| # | 질문 | 기본 제안 |
|---|---|---|
| Q1 | 프레임워크: 방안 A(비의존) 로 진행? | A 권장 |
| Q2 | Decimal 직렬화 = 문자열? (숫자형 대신) | 문자열 |
| Q3 | 상태를 code+label 두 필드로 제공? | 예 |
| Q4 | 응답 반환 타입 = dict / pydantic 모델 중? | dict(직렬화 단순) or pydantic(검증) — PM 선택 |
| Q5 | 등록 목표율 기반(get_dashboard)만? 외부 입력 방식도 노출? | 둘 다 노출(하위호환) |
| Q6 | HTTP 노출(FastAPI)은 후속 Issue 로 분리? | 분리 |

---

## 8. 요약
- 현재 API 계층은 플레이스홀더이고 웹 프레임워크가 없다.
- 이번 단계는 **DashboardSummary → API 응답(payload) 변환**에 집중한다(방안 A).
- API 계층은 **DashboardDataService 만** 호출하여 계층 원칙을 지키고, Calculator·Repository
  에는 직접 접근하지 않는다.
- Decimal·Enum 직렬화 규칙을 정의하고, HTTP 바인딩은 후속으로 분리한다.
- 위 Open Questions(특히 Q1 프레임워크, Q4 반환 타입) 확정 후 구현 착수.
