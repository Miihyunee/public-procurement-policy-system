# 표준 Excel 업로드 — 남은 구현 목록과 결정 지점

## 문서 정보

| 항목 | 값 |
|---|---|
| 작성일 | 2026-08-15 |
| 목적 | PM 지시서 §32 (1·2·3순위) — **확인 · 목록화 · 결정 지점 분리** |
| 범위 | **설계 문서.** 이번에 코드를 구현하지 않았다 |
| 기준 | HEAD `54fcdbe` |

---

# 1. 이미 구현된 것 — 다시 만들지 않는다 (§32 1순위)

`b12fc85` · `58752aa` · `bf7c1ff` · `54fcdbe` 를 확인했다.

| 계층 | 파일 | 상태 |
|---|---|---|
| 표준 양식 컬럼 정의 | `uploads/format.py` | ✅ 확정 5개 · 미확정은 `PENDING_COLUMNS` |
| 행 단위 검증 | `uploads/validation.py` | ✅ 테스트 45건 |
| 사업자번호 정규화 | `matchers/business_no.py` | ✅ **재사용 중** (새로 만들지 않음) |
| 행 적재 | `importers/purchase_importer.py` | ✅ 매핑된 행을 받아 저장 |
| 배치 적재 | `importers/batch_import_service.py` | ✅ 월별 누적 · `ACTIVE`/`SUPERSEDED` |
| 창업기업 OR 판정 | `calculators/rules/date_rules.py` | ✅ `PaymentOrContractDateRule` |
| 음수 상계 | `core/offsetting.py` | ✅ 로직만 (연결 안 됨) |
| 구매유형 매핑 | `core/purchase_type.py` | ✅ 확정 3건만 (연결 안 됨) |
| Electron 생명주기 | `electron/backend.js` | ✅ 검증 8/8 |

> **`PurchaseImporter` 가 이미 "매핑된 행"을 받는 구조**다. 표준 양식 → 내부 필드
> 변환만 앞에 붙이면 되고, 적재 로직을 새로 만들 필요가 없다.

---

# 2. 🔬 openpyxl 필요 여부 — 실측 결과 (§32 2순위)

지시서가 "필요 여부를 검토" 하라고 했으므로 추측하지 않고 실험했다.

## 2.1 실험 1 — 표준 라이브러리만으로 .xlsx 를 만들 수 있는가

`zipfile` + `ElementTree` 로 최소 OOXML 패키지를 직접 만들었다.
(저장소에 선례가 있다 — `collectors/smpp.py` 등이 `ElementTree` 로 XML 을 다룬다.)

| 항목 | 결과 |
|---|---|
| 생성 | ✅ 성공 (1,749 bytes, 파트 5개) |
| **openpyxl 로 다시 읽기** | ✅ **성공** — 시트명·전 셀 값 일치 |

→ **쓰기는 표준 라이브러리로 가능하다.**

## 2.2 실험 2 — 표준 라이브러리만으로 .xlsx 를 읽을 수 있는가

openpyxl 이 만든 파일을 소박한 stdlib 리더로 읽어 봤다.

| 항목 | 결과 |
|---|---|
| 파일 파트 수 | 9개 (`styles.xml` · `theme1.xml` · `docProps` 등 추가) |
| **값 읽기** | 🔴 **실패 — 전부 `None`** |

**원인**: 셀 값 표현 방식이 여러 가지다. 리더가 `<v>` 만 보고 있었는데 실제
파일은 `t="inlineStr"` + `<is><t>` 를 썼다.

```xml
<c r="A1" t="inlineStr"><is><t>결의일자</t></is></c>
```

실제 Excel 이 저장한 파일은 여기에 더해 다음을 쓴다.

| 변형 | 내용 |
|---|---|
| `sharedStrings.xml` | 문자열을 별도 테이블에 두고 `t="s"` + 인덱스 참조 |
| 날짜 | 문자열이 아니라 **1900 기준 일련번호** + `styles.xml` 의 서식 참조 |
| 빈 셀 | 아예 `<c>` 자체가 없음 → 열 위치가 밀림 |
| 병합·수식 | `<f>` · `mergeCells` |

## 2.3 판단

| 방향 | 쓰기(양식 다운로드) | 읽기(업로드) |
|---|---|---|
| 표준 라이브러리 | ✅ 가능 (약 120줄) | 🔴 **권장하지 않음** |
| openpyxl | ✅ 간단 | ✅ 변형 처리 검증됨 |

> ## 결론: **읽기 때문에 openpyxl 이 필요하다.**
>
> 쓰기만 보면 의존성 없이도 되지만, **사용자가 Excel 로 저장한 파일**을 읽는
> 쪽이 문제다. 날짜 일련번호·sharedStrings·빈 셀을 직접 처리하면 사실상
> openpyxl 을 다시 만드는 일이고, 잘못 읽으면 **틀린 금액·날짜가 조용히 저장된다.**

**폐쇄망 영향**: openpyxl 은 순수 Python 휠(`py2.py3-none-any`)이며 의존성은
`et_xmlfile` 하나뿐이다. 컴파일러·시스템 라이브러리가 필요 없어 오프라인 설치가
쉽다. (설치 자체는 승인 후 수행 — 이번에는 격리된 임시 환경에서 실험만 했고
**프로젝트 의존성과 `.venv` 는 건드리지 않았다.**)

> ⚠️ **LibreOffice 검증은 실패했다.** 이 환경의 `soffice` 가 stdlib 파일도
> openpyxl 파일도 모두 열지 못했다(`javaldx` 경고). **파일 문제가 아니라 도구
> 문제**로 보이며, 실제 Excel 호환성은 **미검증**이다. Windows 검증 시 함께 확인해야 한다.

---

# 3. 남은 구현 목록 (§32 2순위)

## 3.1 표준 양식 다운로드

| 항목 | 설계안 |
|---|---|
| 생성 위치 | Python backend (Electron 이 만들지 않는다) |
| 내용 | 시트1 = 머리글 + 예시 1행 / 시트2 = 작성 안내 |
| 근거 | `format.header_row()` · `example_row()` · `guide_lines()` **이미 있음** |
| 전달 | `GET /uploads/template` → `.xlsx` 파일 응답 |
| Electron | 저장 위치를 `dialog.showSaveDialog` 로 고르게 함 |

## 3.2 업로드 API

| 항목 | 설계안 |
|---|---|
| 엔드포인트 | `POST /uploads/purchases` (`multipart/form-data`) |
| 파일 전달 | Electron 이 파일 **경로**가 아니라 **내용**을 보낸다 (sandbox 유지) |
| 처리 | 엑셀 읽기 → `validate_headers` → `validate_rows` → (승인 후) Mapping → 저장 |
| 미리보기 | `?dry_run=true` 로 **검증만** 수행. 지시서 §44 "검증 후 저장" 대응 |

**검증 결과 JSON (안)**

```json
{
  "ok": false,
  "summary": { "total": 1250, "valid": 1230, "error_rows": 20, "warnings": 3 },
  "issues": [
    { "row": 12, "column": "사업자등록번호", "message": "값이 없습니다.", "severity": "error" }
  ]
}
```

`ValidationReport` 가 이미 이 정보를 전부 들고 있다. 응답 모델만 씌우면 된다.

**저장 결과 JSON (안)**

```json
{
  "batch_id": 7,
  "saved": 1230,
  "skipped": 0,
  "period": { "start": "2026-01-01", "end": "2026-12-31" }
}
```

> `BatchImportService.import_batch()` 가 이미 배치 단위 적재·대체를 처리한다.
> **대상 기간은 호출자가 지정**하므로 화면에서 받아야 한다(파일에서 유추 금지).

## 3.3 Electron preload API (안)

`sandbox: true` 를 유지하면서 최소만 노출한다.

| 노출 함수 | 역할 |
|---|---|
| `pickExcelFile()` | `dialog.showOpenDialog` — 경로만 main 이 알고, **내용은 main 이 읽어 백엔드로 전송** |
| `saveTemplate()` | `dialog.showSaveDialog` + 백엔드에서 받은 양식 저장 |

렌더러에는 **파일 시스템 접근을 노출하지 않는다.** 검증·파싱·저장은 전부 Python.

## 3.4 renderer 호출 방식

기존 화면이 `fetch("/dashboard/...")` 로 백엔드를 직접 부른다. 업로드도 같은
방식을 쓰되, **파일 선택만** preload 를 거친다.

```
[파일 선택] → preload.pickExcelFile() → main: dialog + 파일 읽기
           → main 이 백엔드로 POST → 결과를 렌더러에 반환 → 화면 표시
```

## 3.5 트랜잭션 (§44)

> 🔴 **현재 `BaseRepository` 의 트랜잭션 경계를 확인해야 한다.**
> 이전 보고에서 Technical Risk 로 기록해 둔 항목이며, "전부 검증 후 저장" 을
> 보장하려면 이 부분을 먼저 봐야 한다. 이번에는 손대지 않았다.

---

# 4. 🔴 결의일자 결정 지점 (§32 3순위)

## 4.1 지금 정확히 어디가 막혀 있는가

```
표준 Excel "결의일자"
      │
      ▼
 validation.py  →  values["resolution_date"]      ← ✅ 여기까지 구현됨
      │
      ▼
 ┌─────────────────────────────────────┐
 │  Mapping 계층 — 아직 만들지 않음     │  ← 🔴 여기서 막힌다
 │  resolution_date → Purchase 의 ???  │
 └─────────────────────────────────────┘
      │
      ▼
 PurchaseImporter.import_rows()  ← ✅ 이미 있음. payment_date / contract_date 를 받는다
```

검증 계층은 값을 **`resolution_date` 라는 중립적인 키**로 담아 둔다. 어느 물리
필드에 넣을지 정하지 않았으므로, 이 키는 **표준 양식의 컬럼 이름일 뿐**이며
DB 필드를 뜻하지 않는다.

## 4.2 선택지

| 안 | 변경 범위 | 결과 |
|---|---|---|
| **A** `payment_date` 재사용 | Mapping 1줄 | 스키마 무변경. **필드 이름이 실제 의미와 어긋남** |
| **B** `resolution_date` 신설 | 모델 · 스키마 · 마이그레이션 · `PeriodFilter` 허용값 · `offsetting.date_of` · 테스트 | 의미가 정확. **지금이 최적기** |
| **C** `payment_date` 의미 재정의 | 문서만 | 나중에 진짜 지급일이 필요해지면 재작업 |

**B 를 택할 경우 함께 바뀌는 곳** (실측):

| 파일 | 변경 |
|---|---|
| `models/purchase.py` | 필드 추가 |
| `database/purchase_repository.py` | 컬럼 · `_row_to_purchase` · SQL |
| `database/bootstrap.py` | `_ADDED_COLUMNS` 에 마이그레이션 추가 |
| `core/period.py` | `ALLOWED_DATE_FIELDS` 에 추가 |
| `calculators/rules/date_rules.py` | OR 규칙이 볼 날짜 재검토 |
| `core/offsetting.py` | `date_of` 허용값 |
| `importers/purchase_importer.py` | 행 키 추가 |

> ⚠️ **B 는 창업기업 OR 규칙에도 영향이 있다.** 현재 규칙은 "모델의 두 날짜를
> 모두 본다" 는 전제인데, 날짜 필드가 셋이 되면 **어느 둘을 볼지 다시 정해야
> 한다.** 고객 확정 문구는 "결의일자 OR 계약일자" 이므로 B 에서는
> `resolution_date` 와 `contract_date` 가 대상이 된다.

**PM 승인 없이 A·B·C 어느 것도 구현하지 않았다.**

---

# 5. 이번 작업에서 하지 않은 것

| 항목 | 이유 |
|---|---|
| openpyxl 설치 | PM 승인 대기 (§12) — 임시 환경에서 **실험만** |
| 엑셀 어댑터 · 양식 다운로드 | 위와 동일 |
| Mapping 계층 | 결의일자 필드 미확정 |
| 업로드 API · 화면 | 위 둘에 종속 |
| 중복 판정 | 기준 미확정 (§18) |
| 음수 저장 제약 | 승인 대기 (§7) |
| 트랜잭션 경계 변경 | 먼저 확인 필요 (§44) |

---

# 6. 승인되면 바로 진행할 순서

| # | 작업 | 선행 |
|---|---|---|
| 1 | openpyxl 추가 + 엑셀 어댑터 (읽기) | **승인 ①** |
| 2 | 양식 다운로드 (`GET /uploads/template`) | 승인 ① |
| 3 | Mapping 계층 | **승인 ②** (결의일자 필드) |
| 4 | 업로드 API (`POST /uploads/purchases`) | 1 · 3 |
| 5 | Electron 파일 선택 + 결과 화면 | 4 |

> **①·② 두 개만 풀리면 업로드가 끝까지 연결된다.** 나머지(중복 판정 · 음수 저장 ·
> 구매유형)는 업로드가 동작한 뒤에 붙여도 되는 항목이다.
