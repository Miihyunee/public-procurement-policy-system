# 문서 안내 (Documentation Map)

> 최종 갱신: 2026-08-11 (문서 통폐합)

## 어디를 먼저 봐야 하나

| 알고 싶은 것 | 문서 |
|---|---|
| **지금 프로젝트가 어디까지 왔나** | `PROJECT_STATUS_AND_ROADMAP.md` |
| **무엇이 결정됐고 무엇이 안 됐나** | **`DECISIONS.md`** ⭐ |
| **고객에게 뭘 물어봐야 하나** | `CUSTOMER_DATA_QUESTIONS.md` |
| **데이터를 어디서 어떻게 받나** | `DATA_ACQUISITION_PLAN.md` |

> ⭐ **`DECISIONS.md` 가 결정의 단일 출처다.** 여기 없는 결정은 결정된 것이 아니다.
> 새 결정은 이 문서에만 추가한다.

---

## 1. 현황 · 결정

| 문서 | 내용 |
|---|---|
| `PROJECT_STATUS_AND_ROADMAP.md` | Git 실측 기준 현재 상태, Phase 0~5 로드맵 |
| **`DECISIONS.md`** | **D(결정) · W(고객 확인) · C(문서 충돌) 통합 대장** |

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

## 4. 구매데이터 (현재 작업 중심)

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
