# 신규 아키텍처 — 구매자료 정제·검토 파이프라인

## 문서 정보

| 항목 | 값 |
|---|---|
| 작성일 | 2026-08-22 |
| 상태 | 📄 설계 + ✅ **STEP 1~3 구현 완료** · 🧪 **STEP 4 실험 환경** · 🔬 **STEP 5 코퍼스 검증 완료** (2026-08-22). DB-3 · Calculator 연결은 미착수 |
| 근거 | 2026-08-22 회의 결과 (PM 전달) |
| 관련 문서 | `DATABASE_PIPELINE_DESIGN.md` · `DESCRIPTION_SIMILARITY_DESIGN.md` · `REVIEW_INTERFACE_DESIGN.md` |
| ⛔ 이번 범위 아님 | 구현 · 스키마 변경 · Calculator 수정 · Electron/Rust 선택 · 고객 미확정 사항 결정 |

> **방향 전환**: 원본을 저장하고 바로 계산하던 구조에서, **원본을 보존한 채
> 분석 → 담당자 검토 → 확정 데이터셋 생성** 을 거쳐 계산하는 파이프라인으로
> 바꿉니다.

---

# 1. 전체 데이터 흐름

```text
   원본 Excel  (9컬럼 표준 양식)
        │
        ▼
   Excel Adapter          파일 → 머리글 + 행 (해석하지 않음)
        │
        ▼
   Validation             행·값 검증, 사업자번호 정규화
        │
        ▼
 ┌──────────────┐
 │    DB-1      │  원본 보존 · source of truth
 │  Raw Data    │  ⛔ 이후 어떤 단계도 이 값을 수정하지 않는다
 └──────────────┘
        │
        ├──────────────────────────────┐
        ▼                              │
   적요 유사도 분석                     │
   (BM25 / RAG / FUSE — 미선택)         │
        │  후보 + 점수                  │
        ▼                              │
   담당자 검토 인터페이스                │
   후보 확인 · 최종 선택                 │
        │                              │
        ▼                              │
 ┌──────────────┐                      │
 │    DB-2      │  분석 결과 + 담당자 확정 결과 │
 │Review/Class. │  ⛔ 자동 분석이 확정값을 덮어쓰지 않는다
 └──────────────┘                      │
        │                              │
        └──────────┬───────────────────┘
                   ▼
            DB-1 + DB-2 결합
                   │
                   ▼
            ┌──────────────┐
            │    DB-3      │  최종 계산용 확정 데이터셋
            │  Final Set   │  ⛔ 생성 시 DB-1 을 수정하지 않는다
            └──────────────┘
                   │
                   ▼
              Calculator          정책별 실적 · 달성률
                   │
                   ▼
              Dashboard / 최종 결과
```

## 1.1 단계별 책임

| 단계 | 책임 | 하지 않는 일 |
|---|---|---|
| **Excel Adapter** | 파일을 열어 머리글과 행을 그대로 꺼낸다 | 값 해석·업무 판정 |
| **Validation** | 날짜·금액·사업자번호 형식 검증, 오류를 행·컬럼·사유로 보고 | 저장, 업무 판정 |
| **DB-1** | 원본을 **있는 그대로** 보존. 모든 후속 단계의 기준점 | 분류값 보관, 원본 수정 |
| **적요 유사도 분석** | 적요를 읽어 구매유형 **후보와 점수**를 만든다 | **최종 확정** |
| **검토 인터페이스** | 원본 + 후보를 나란히 보여주고 담당자 선택을 받는다 | 자동 확정 |
| **DB-2** | 분석 결과와 담당자 확정 결과를 **분리해서** 보관. 이력 추적 | 원본 수정 |
| **DB-3** | DB-1(원본) + DB-2(확정) 을 결합한 계산용 스냅샷 | 새 업무 판정 |
| **Calculator** | 정책별 실적·달성률 계산 | 데이터 정제 |
| **Dashboard** | 결과 표시 | 계산 |

## 1.2 이 구조가 지키는 원칙

| 원칙 | 어떻게 |
|---|---|
| **원본 불변** | DB-1 은 append-only. 담당자 수정은 DB-2 에 별도 행으로 기록 |
| **자동 ≠ 확정** | DB-2 에서 분석 결과 컬럼과 담당자 확정 컬럼을 **물리적으로 분리** |
| **추적 가능** | "원본 적요 / AI 후보와 점수 / 담당자 결정 / 확정자 / 확정일시" 가 모두 남는다 |
| **재현 가능** | DB-3 은 특정 시점의 DB-1 + DB-2 로부터 **다시 만들 수 있다** |
| **UI 프레임워크 독립** | 업무 로직은 Python 계층에 두고, UI 는 HTTP API 만 호출 (13장) |

---

# 2. 기존 구조와 신규 구조 비교

## 2.1 흐름 비교

```text
[현재]
Excel → Validation → purchase 테이블 → Calculator → Dashboard
                        (원본 = 계산 대상)

[신규]
Excel → Validation → DB-1 ──┬─→ 분석 → 검토 → DB-2 ──┐
                            │                        ├→ DB-3 → Calculator → Dashboard
                            └────────────────────────┘
                        (원본 ≠ 계산 대상)
```

**가장 큰 변화**: 지금은 `purchase` 테이블이 **원본이자 계산 대상**입니다.
신규 구조에서는 두 역할이 분리되어, 원본은 DB-1 이고 계산 대상은 DB-3 입니다.

## 2.2 기존 코드 분류 (실제 파일·클래스 기준)

### ✅ 유지 — 그대로 쓴다

| 기능 | 파일 / 클래스 | 신규 구조에서의 위치 |
|---|---|---|
| Excel 읽기 | `uploads/excel_adapter.py` `read_standard_workbook()` · `WorkbookRead` | 변경 없음 |
| 표준 양식 정의 | `uploads/format.py` `STANDARD_COLUMNS`(9컬럼) | 변경 없음 |
| 양식 파일 생성 | `uploads/template.py` | 변경 없음 |
| 행 검증 | `uploads/validation.py` `validate_headers()` · `validate_rows()` | 변경 없음 |
| 검증→적재 연결 | `uploads/mapping.py` `to_import_rows()` | 변경 없음 |
| 사업자번호 정규화 | `matchers/business_no.py` | 변경 없음 |
| 기업 매칭 | `matchers/company_matcher.py` `CompanyMatcher` | 변경 없음 |
| 배치 관리 | `importers/batch_import_service.py` · `database/import_batch_repository.py` | DB-1 의 배치 단위로 그대로 |
| **상계 판정** | `core/offsetting.py` `offset_negative_purchases()` | ⛔ **수정하지 않음.** 검토 대상 생성에 재사용 (10장) |
| 구매유형 값·확정 매핑 | `core/purchase_type.py` `CONSTRUCTION/SERVICE/GOODS` · `CONFIRMED_BUDGET_ACCOUNT_TYPES` | 분류 체계의 **정본** |
| 정책 관리 | `admin/policy_admin.py` `PolicyAdminService` · `database/policy_repository.py` | 변경 없음 |
| 정책 판정 규칙 | `calculators/rules/` `PolicyRule` · `registry.py` | 변경 없음 |
| 기간 필터 | `core/period.py` `PeriodFilter` | 변경 없음 |
| Bootstrap | `database/bootstrap.py` | DB-2·DB-3 테이블 추가 시 확장 |

### 🟡 수정 예정 — 최소 변경

| 기능 | 파일 / 클래스 | 변경 내용 | 시점 |
|---|---|---|---|
| 구매 저장 | `database/purchase_repository.py` `PurchaseRepository` | **DB-1 Repository 로 의미 재정의.** 컬럼 변경 없음 | 즉시 가능 |
| 음수 저장 제약 | 같은 파일 `_validate()` | `amount <= 0` 거부 해제 | D단계 (별도 승인) |
| 계산 입력 | `calculators/procurement_achievement.py` `_sum_policy_purchase()` · `calculate_total_purchase()` | 읽는 대상을 DB-1 → **DB-3** 으로 | DB-3 확정 후 |
| 조립 | `app.py` `build_dashboard_api()` | DB-3 Repository 주입 | 위와 동시 |
| 상태 지표 | `dashboard/status_service.py` | 검토 진행률 등 추가 | 선택 |
| 화면 | `web/static/index.html` (1,157줄) | **검토 탭 추가**. 기존 탭 유지 | 검토 API 확정 후 |

### 🆕 신규 개발

| 기능 | 예상 위치 | 비고 |
|---|---|---|
| 적요 분석 인터페이스 | `core/description_classifier.py` (신규) | 추상 인터페이스만. 구현체는 미선택 |
| 분석 결과 모델 | `models/classification.py` (신규) | `ClassificationResult` |
| 검토 모델 | `models/review.py` (신규) | 분석 결과 + 담당자 확정 |
| DB-2 Repository | `database/review_repository.py` (신규) | |
| DB-3 Repository | `database/final_dataset_repository.py` (신규) | |
| DB-3 생성 서비스 | `services/final_dataset_builder.py` (신규) | DB-1 + DB-2 → DB-3 |
| 검토 API | `api/review_api.py` (신규) | 목록·후보·확정 |
| 검토 화면 | `web/static/` 검토 탭 | |

### ⚠️ 향후 폐기 **가능성** — 지금은 판단하지 않음

| 대상 | 이유 | 판단 시점 |
|---|---|---|
| `core/purchase_type.py::classify_budget_account()` | 예산과목 완전일치 분류. 적요 분석이 대체할 **가능성** | 분석 방식 확정 후 |
| `web/policy_display.py` 의 `GREEN` 항목 | 이미 비활성 정책이라 도달하지 않음 | 화면 변경 승인 시 |

> ⛔ **지금 폐기하지 않습니다.** `classify_budget_account()` 는 고객이 확정한
> 3건을 담고 있어 적요 분석의 **정답 기준(seed)** 으로 쓸 수도 있습니다.

---

# 3. 기능 대응표 (지시 14번)

| 기능 | 현재 위치 (실제) | 신규 구조에서 |
|---|---|---|
| Excel 업로드 | `uploads/upload_service.py::UploadService` · `app.py POST /uploads/purchases` | ✅ **유지** |
| 원본 저장 | `importers/purchase_importer.py::PurchaseImporter` → `database/purchase_repository.py::PurchaseRepository` → `purchase` 테이블 | 🟡 **DB-1** 로 의미 재정의 (컬럼 변경 없음) |
| 적요 분석 | **없음** — `description` 컬럼은 2026-08-20 에 추가되어 **보관만** 하고 있음 | 🆕 **신규** |
| 담당자 검토 | **없음** | 🆕 **신규** |
| 검토 결과 저장 | **없음** | 🆕 **DB-2** |
| 최종 데이터 | 현재는 `purchase` 테이블을 그대로 계산에 사용 (`find_for_calculation()`) | 🆕 **DB-3** |
| 계산 | `calculators/procurement_achievement.py::ProcurementAchievementCalculator` | 🟡 **입력 소스만 교체** — 계산식 무변경 |
| Dashboard | `dashboard/data_service.py::DashboardDataService` · `api/dashboard_api.py` · `web/static/index.html` | ✅ 유지 / 🟡 검토 탭 추가 |
| 상계 | `core/offsetting.py::offset_negative_purchases()` | ✅ **유지** (수정 금지). 검토 대상 생성에 재사용 |
| 정책 관리 | `admin/policy_admin.py::PolicyAdminService` · `app.py /policies` | ✅ 유지 |
| 구매유형 값 | `core/purchase_type.py` | ✅ 유지 — 분류 체계의 정본 |

---

# 4. Calculator 입력 변화 (지시 12번)

## 4.1 현재

```python
# calculators/procurement_achievement.py
def calculate_total_purchase(self, period):          # 분모
    for purchase in self._purchase_repository.find_for_calculation(period):
        total += purchase.amount

def _sum_policy_purchase(self, policy_id, period):   # 분자
    for purchase in self._purchase_repository.find_for_calculation(period):
        ...  # rule.applies(...) 인 것만
```

| 항목 | 현재 |
|---|---|
| 입력 | `purchase` 테이블 (= 원본) |
| 필터 | SUPERSEDED 배치 제외 + 기간 |
| 구매유형 | **사용하지 않음** |
| 상계 | **적용되지 않음** |
| 음수 | **존재할 수 없음** (저장 거부) |

## 4.2 변경 후

| 항목 | 변경 후 |
|---|---|
| 입력 | **DB-3** (확정 데이터셋) |
| 필터 | DB-3 생성 시점에 이미 적용됨 + 기간 |
| 구매유형 | DB-3 에 **확정값으로 존재** (여성기업 이원화 목표율에 사용 가능) |
| 상계 | DB-3 생성 시점에 반영 |
| 음수 | DB-3 정책에 따름 |

## 4.3 코드 관점의 차이 — **이음매가 하나뿐**

분모와 분자가 **같은 메서드**(`find_for_calculation()`)를 호출하므로, 그
호출부 하나만 DB-3 Repository 로 바꾸면 됩니다. 계산식은 건드리지 않습니다.

```python
# 변경 전
rows = self._purchase_repository.find_for_calculation(period)
# 변경 후
rows = self._final_dataset_repository.find_for_calculation(dataset_id, period)
```

⛔ **이번 작업에서 바꾸지 않습니다.** DB-3 구조 확정 후 별도 승인 대상입니다.

---

# 5. UI 프레임워크 독립성 (지시 13번)

Electron / Rust 중 **어느 것도 선택하지 않습니다.** 대신 종속되지 않도록 다음
경계를 지킵니다.

```text
┌─────────────────────────────────────────┐
│  UI  (HTML/JS · Electron · Rust · CLI)  │  ← 교체 가능
└──────────────────┬──────────────────────┘
                   │ HTTP (JSON)
┌──────────────────▼──────────────────────┐
│  FastAPI (app.py)                        │
│  ApiService → DataService → Calculator   │  ← 업무 로직 전부 여기
│  → Rule Engine → Repository → SQLite     │
└─────────────────────────────────────────┘
```

| 규칙 | 근거 |
|---|---|
| ⛔ 업무 판정을 JavaScript 에 두지 않는다 | 이미 `ELECTRON_ARCHITECTURE.md` 에서 확정. 테스트로 고정됨 (`test_electron_upload_wiring.py`) |
| 검토 인터페이스도 **HTTP API 로만** 동작 | UI 를 바꿔도 API 가 그대로면 재작업이 없다 |
| 파일 선택 등 네이티브 기능만 프레임워크에 남긴다 | 현재 Electron 구조와 동일 |

---

# 6. 🟡 결정 대기 (임의로 정하지 않음)

| # | 항목 | 상태 | 문서 |
|---|---|---|---|
| ① | **최종 8개 정책 구성** | 🔴 미확정 — 파일 11개 − 확정 제외 2개 = 9개, 1개 차이 근거 없음 | `DECISIONS.md` §0.5.5 |
| ② | **구매유형 분류 업무규칙** | 🔴 미확정 — 확정된 것은 예산과목 3건뿐 | `DECISIONS.md` §0.5.3 |
| ③ | **담당자 확인 대상(상계 84건) 처리** | 🔴 미확정 | `NEGATIVE_OFFSET_WIRING_DESIGN.md` §3.2 |
| ④ | **상계 최종 확정 방식** | 🔴 미확정 — 담당자 선택을 시스템에 기록할지 | `CUSTOMER_DATA_QUESTIONS.md` Q4-6 |
| ⑤ | 분석 방법 (BM25 / RAG / FUSE) | 🔴 미선택 | `DESCRIPTION_SIMILARITY_DESIGN.md` |
| ⑥ | DB-3 생성·버전 정책 | 🟡 권장안 제시 | `DATABASE_PIPELINE_DESIGN.md` §5 |
| ⑦ | 여성기업 목표율 이원화 반영 | 🔴 D-1·D-2 대기 | `DECISIONS.md` §0.5.4 |
| ⑧ | Electron / Rust | 🔴 미선택 | 본 문서 §5 |

---

# 7. 다음 개발 단계 (우선순위 제안)

| 순위 | 단계 | 선행 조건 | 근거 |
|---|---|---|---|
| ~~**1**~~ | ~~DB-2 스키마 + Repository~~ | — | ✅ **완료** (2026-08-22) — `database/review_repository.py` |
| ~~**2**~~ | ~~분석 인터페이스(추상) + 규칙 없는 기본 구현~~ | — | ✅ **완료** — `core/description_classifier.py` |
| ~~**3**~~ | ~~검토 API + 화면~~ | — | ✅ **완료** — `reviews/` · `/reviews` · index.html 검토 카드 |
| ~~**4**~~ | ~~분석 방법 비교 실험 (BM25/RAG/FUSE)~~ | — | 🧪 **실험 환경 완료** — `experiments/` · `scripts/compare_description_methods.py`. ⛔ **선택은 미결**(PM/고객). 실측: `DESCRIPTION_SIMILARITY_DESIGN.md` §8 |
| **4.5** | 코퍼스 품질 검증 | 4 | 🔬 **완료** — `DESCRIPTION_CLASSIFICATION_DATA_ANALYSIS.md`. 🔴 작업본 `구분` 은 예산과목 파생값. 고객 확인 Q5-1~Q5-7 대기 |
| **5** | DB-3 생성 서비스 | 1~3 · 결정 ⑥ | |
| **6** | Calculator 입력 교체 | 5 | 숫자가 바뀌는 단계 — 마지막 |
| **7** | 상계 검토 통합 | 결정 ③·④ | 검토 인터페이스에 상계 후보 추가 |

> **1~4 는 기존 계산 결과를 전혀 바꾸지 않습니다.** 새 테이블·새 화면·실험
> 코드만 추가되며, 4번 실험 코드는 운영 경로에서 import 되지 않습니다(테스트로
> 강제). 숫자가 바뀌는 것은 6번뿐입니다.
