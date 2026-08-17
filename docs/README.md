# 문서 안내 (Documentation Map)

> 최종 갱신: 2026-08-17

## 처음 오셨다면

**`PM_HANDOVER.md` 부터 읽으세요.** 현재 상태 · 결정 대기 목록 · 작업 원칙을
한 문서에 정리해 두었습니다.

## 어디를 먼저 봐야 하나

| 알고 싶은 것 | 문서 |
|---|---|
| **프로젝트를 처음 이어받는다** | **`PM_HANDOVER.md`** ⭐ |
| **무엇이 결정됐고 무엇이 안 됐나** | **`DECISIONS.md`** ⭐ |
| **지금 무슨 작업 중인가** | `UPLOAD_PIPELINE_DESIGN.md` |
| **고객에게 뭘 물어봐야 하나** | `CUSTOMER_DATA_QUESTIONS.md` |
| **데이터를 어디서 어떻게 받나** | `DATA_ACQUISITION_PLAN.md` |

> ⚠️ `PROJECT_STATUS_AND_ROADMAP.md` 는 2026-08-11 기준이라 현재와 다릅니다.
> 현재 상태는 `PM_HANDOVER.md` 를 보세요.

> ⭐ **`DECISIONS.md` 가 결정의 단일 출처다.** 여기 없는 결정은 결정된 것이 아니다.
> 새 결정은 이 문서에만 추가한다.

---

## 1. 현황 · 결정

| 문서 | 내용 |
|---|---|
| **`PM_HANDOVER.md`** | **현재 상태 · 결정 대기 · 작업 원칙 (진입점)** |
| **`DECISIONS.md`** | **D(결정) · W(고객 확인) · C(문서 충돌) 통합 대장** |
| `PROJECT_STATUS_AND_ROADMAP.md` | Phase 0~5 로드맵. ⚠️ 현황 부분은 2026-08-11 기준 |

## 2. 프로젝트 기반

| 문서 | 내용 |
|---|---|
| `PROJECT_BRIEF.md` | 프로젝트 개요 |
| `REQUIREMENTS.md` | 요구사항 |
| `SYSTEM_ARCHITECTURE.md` | 시스템 구조 |
| `DATABASE_DESIGN.md` | DB 설계 |
| `DATA_DICTIONARY.md` | 논리명·물리명 대조, 필드 정의 |
| `POLICY_DEFINITION.md` | 정책 정의 |

## 3. 정책 분석

| 문서 | 내용 |
|---|---|
| `POLICY_GAP_ANALYSIS_2026.md` | 2026 정부권장정책 vs 현재 설계 전수 대조 |
| `POLICY_DECISION_ANALYSIS.md` | 정책 범위·계산기준 확정 전 검증 (D-1~D-9) |

## 3.5 업로드 (현재 작업 중심)

| 문서 | 내용 |
|---|---|
| `UPLOAD_PIPELINE_DESIGN.md` | 업로드 파이프라인 설계 · 남은 결정 지점 |
| `STANDARD_UPLOAD_FORMAT.md` | 표준 양식 컬럼 정의와 근거 |
| `ELECTRON_ARCHITECTURE.md` | Electron 구조 · 보안 설정 · 미검증 항목 |

## 4. 구매데이터

| 문서 | 내용 | 단계 |
|---|---|---|
| `REAL_PURCHASE_DATA_ANALYSIS.md` | **샘플** 파일 구조 분석 | 완료 |
| `PURCHASE_DATA_SPEC.md` | 고객 요청용 데이터 규격 (9장에 샘플 확인 결과) | 활성 |
| `PURCHASE_PARSER_SPEC.md` | Parser 설계 명세 | 승인 대기 |
| `PURCHASE_DATA_STRUCTURE_VALIDATION_SPEC.md` | 구조 검증 리포트 도구 명세 | 승인 대기 (D-22) |
| **`PURCHASE_PERIOD_AND_DEDUP_SPEC.md`** | **연도별 집계 · 중복 적재 방지** | 승인 대기 |
| `PURCHASE_IMPORT_DESIGN.md` | Import 설계 (행 단위) | 구현됨 |
| `CUSTOMER_DATA_QUESTIONS.md` | 고객 전달용 질문지 (W-1~W-10) | 전달 대기 |

## 5. 데이터 확보

| 문서 | 내용 |
|---|---|
| `DATA_ACQUISITION_PLAN.md` | 구매데이터·기업정보·인증정보 확보 계획, API 3종 |
| `EXTERNAL_API_ONBOARDING.md` | 외부 API 신청 절차 상세 |

## 6. 제안서 (`proposals/`)

| 문서 | 상태 |
|---|---|
| `ISSUE24-SPEC-real-data-e2e-validation.md` | Phase 4 — 실제 데이터 E2E 검증 |
| `ISSUE49-SPEC-purchase-amount-validation.md` | **보류** — 음수/0 금액 (W-4 대기) |
| `ISSUE26-SPEC-period-filter-and-import-batch.md` | 📜 **역사적 설계 문서** — 기간 필터 · Import Batch. **구현 완료**되었으며 현재 상태는 `DECISIONS.md` 가 정본 |

---

## 정리 이력 (2026-08-11)

문서가 39개까지 늘어나 내용이 서로 겹쳤다. **18개를 삭제하고 21개로 통합**했다.

| 삭제한 문서 | 대체 |
|---|---|
| `POLICY_IMPLEMENTATION_STATUS.md` | `PROJECT_STATUS_AND_ROADMAP.md` |
| `POLICY_TARGET_RATE_ADMIN_SPEC.md` | 구현 완료 — 결정은 `DECISIONS.md` D-11~D-17 |
| `PARSER_SPEC_CONSISTENCY_CHECK.md` | 결과가 `PURCHASE_DATA_SPEC.md` 9장 · `DECISIONS.md` C-1~C-3 에 반영됨 |
| `PREPROCESSING_DECISION_ANALYSIS.md` | `PURCHASE_PARSER_SPEC.md` |
| `proposals/ISSUE25-SPEC-purchase-preprocessing.md` | `PURCHASE_PARSER_SPEC.md` |
| `PURCHASE_DATA_VERIFICATION_PLAN.md` | `PURCHASE_DATA_STRUCTURE_VALIDATION_SPEC.md` |
| `DATA_INTAKE_CHECKLIST.md` | 〃 |
| `PURCHASE_DATA_MAPPING_TEMPLATE.md` | 샘플 구조 확인으로 대체됨 |
| `DATA_MAPPING.md` | `DATA_DICTIONARY.md` |
| `DATA_SOURCES.md` | `DATA_ACQUISITION_PLAN.md` · `EXTERNAL_API_ONBOARDING.md` |
| `DATA_COLLECTION_STRATEGY.md` | `DATA_ACQUISITION_PLAN.md` |
| `E2E_TEST_SCENARIOS.md` | `tests/test_e2e_scenarios.py` (코드로 존재) |
| `proposals/ISSUE11·12·16·17·20·21·22·23` (8건) | 구현 완료 — 결과가 코드에 있음 |

> 삭제한 문서는 **Git 이력에 그대로 남아 있다.** 필요하면 언제든 복원할 수 있다.

## 앞으로의 원칙

| 원칙 |
|---|
| **새 문서를 만들기 전에 기존 문서를 갱신할 수 있는지 먼저 확인한다** |
| 결정 사항은 **`DECISIONS.md` 한 곳**에만 기록한다 |
| 구현이 끝난 Spec 은 결과를 `DECISIONS.md` 에 남기고 **문서는 삭제**한다 |
| 샘플 관찰값을 운영 규칙으로 승격하지 않는다 |
