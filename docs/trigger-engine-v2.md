# Trigger Engine v2

## Model Contract

`Trigger` no longer stores one ambiguous `ast` tree. Runtime metadata is split by role:

| Field | Purpose |
|---|---|
| `condition_ast` | Optional gate. Must infer to `BOOLEAN` when present. |
| `payload_ast` | Required data extraction tree. Server infers `payload_return_type`. |
| `payload_return_type` | Server-computed enum: `BOOLEAN`, `VALUE`, `LIST`. Client input is ignored. |
| `action_mapping_ast` | Optional mapping tree for AST-dependent DML actions. |
| `source_template_uuid` | Source table that emits the event. Required and tenant-scoped. |
| `target_template_uuid` | Target table/resource touched by the trigger. Tenant-scoped. |

## Validator Rules

| AST node | Inferred return type |
|---|---|
| `literal` bool | `BOOLEAN` |
| `literal`, `input`, scalar non-boolean `field` | `VALUE` |
| boolean `field` | `BOOLEAN` |
| `field` for `relation_list`, array/list-like, `multiple`, `is_array` | `LIST` |
| `relation_field` | `LIST` for multi relation, otherwise `VALUE` |
| `aggregation`, `array_reduce` | `VALUE` |
| `binary_op(gt/lt/eq)`, `logical_op` | `BOOLEAN` |
| `string_op(regex_match)` | `BOOLEAN` |
| arithmetic `binary_op`, `date_op`, other `string_op`, `object` | `VALUE` |
| `query` | `LIST` |
| `condition` | Branch type; branches must match. |

Validation also checks:

- `source_template_uuid` and `target_template_uuid` exist inside the same `instance_uuid`.
- Tenant isolation is applied to SQL and Mongo lookups.
- Cascade graph edges `source_template_uuid -> target_template_uuid` have no cycles for DML actions only (`INSERT_RECORD`, `UPDATE_RECORD`, `UPSERT_RECORD`, and legacy `mongo_*` aliases). Non-mutating system actions such as `RETURN_TO_CALLER` do not create cascade edges.
- DML write target is defined only by `Trigger.target_template_uuid`; `action_params.target_template_uuid` is legacy input and is rejected if it differs.
- Comparison operators reject LIST operands before save; operands must be scalar `VALUE` or `BOOLEAN`.
- `action_name` matches `triggers/action_contracts.py`.
- DML actions `INSERT_RECORD`, `UPDATE_RECORD`, `UPSERT_RECORD` require `action_mapping_ast`.

## Core Classes

| Class | File | Role |
|---|---|---|
| `EventReceptor` | `engine/event_receptor.py` | Captures events, builds initial scope, finds subscribed triggers. |
| `ASTEvaluator` / `EvaluationScope` | `engine/evaluator.py` | Stateful recursive AST evaluator with `document`, `current_item`, variables, `instance_uuid`. |
| `BatchDataLoader` | `engine/batch_loader.py` | Session cache and `$in` loader for related records and query-backed LIST payloads. |
| `IterationLoopEngine` | `engine/iteration_engine.py` | Isolated per-item scopes for LIST payload DML. |
| `ActionDispatcher` | `engine/action_registry.py` | Runtime contract guard and DML/system action dispatch. |
| `TargetAtomicWriter` | `engine/atomic_writer.py` | Translates abstract operations into Mongo `bulk_write`. |

## Pipelines

Scalar pipeline (`VALUE` / `BOOLEAN`):

1. `EventReceptor`
2. `ASTEvaluator(condition_ast)`
3. `ASTEvaluator(payload_ast)`
4. `ActionDispatcher.dispatch`

LIST pipeline:

1. `EventReceptor`
2. `ASTEvaluator(condition_ast)`
3. `ASTEvaluator(payload_ast)`
4. System action receives the full list, or DML goes through `IterationLoopEngine`
5. `TargetAtomicWriter.flush()` performs one unordered `bulk_write`

## Business Case JSON

### 1. Orders -> Clients UPSERT

```json
{
  "name": "Orders -> Clients upsert",
  "trigger_type": "AUTOMATION",
  "event_type": "ON_RECORD_CREATE",
  "source_template_uuid": "<orders-template-uuid>",
  "target_template_uuid": "<clients-template-uuid>",
  "condition_ast": {
    "type": "binary_op",
    "operator": "gt",
    "left": {"type": "field", "value": "client_phone"},
    "right": {"type": "literal", "value": ""}
  },
  "payload_ast": {
    "type": "object",
    "fields": {
      "phone": {"type": "field", "value": "client_phone"},
      "name": {"type": "field", "value": "client_name"}
    }
  },
  "action_name": "UPSERT_RECORD",
  "action_params": {
    "search_fields": ["phone"]
  },
  "action_mapping_ast": {
    "type": "object",
    "fields": {
      "phone": {"type": "field", "value": "client_phone"},
      "name": {"type": "field", "value": "client_name"}
    }
  }
}
```

### 2. LIVE_EVAL Product Suggestions

```json
{
  "name": "Product live suggestions",
  "trigger_type": "LIVE_EVAL",
  "event_type": "MANUAL",
  "source_template_uuid": "<products-template-uuid>",
  "target_template_uuid": "<products-template-uuid>",
  "condition_ast": {
    "type": "binary_op",
    "operator": "gt",
    "left": {"type": "input"},
    "right": {"type": "literal", "value": ""}
  },
  "payload_ast": {
    "type": "query",
    "target_template_uuid": "<products-template-uuid>",
    "filters": [
      {"field": "name", "operator": "contains", "value": {"type": "input"}},
      {"field": "quantity_left", "operator": "gt", "value": {"type": "literal", "value": 0}}
    ],
    "return_fields": ["name", "quantity_left"]
  },
  "action_name": "RETURN_TO_CALLER"
}
```

Evaluate request:

```json
{
  "context_data": {},
  "manual_input": "кольцо"
}
```

### 3. Paid Order Stock Decrement

```json
{
  "name": "Paid order stock decrement",
  "trigger_type": "AUTOMATION",
  "event_type": "ON_RECORD_UPDATE",
  "source_template_uuid": "<orders-template-uuid>",
  "target_template_uuid": "<products-template-uuid>",
  "condition_ast": {
    "type": "binary_op",
    "operator": "eq",
    "left": {"type": "field", "value": "payment"},
    "right": {"type": "literal", "value": "картой"}
  },
  "payload_ast": {"type": "field", "value": "product_list"},
  "action_name": "UPDATE_RECORD",
  "action_mapping_ast": {
    "type": "object",
    "fields": {
      "_id": {"type": "field", "value": "current_item.target_uuid"},
      "quantity_left": {
        "type": "object",
        "fields": {
          "op": {"type": "literal", "value": "inc"},
          "value": {
            "type": "binary_op",
            "operator": "multiply",
            "left": {"type": "field", "value": "current_item.qty"},
            "right": {"type": "literal", "value": -1}
          }
        }
      }
    }
  }
}
```

## Migration

Recommended two-phase rollout:

```bash
alembic upgrade 20260605_0001
# deploy and verify trigger-engine-v2 writes payload_ast
alembic upgrade 20260605_0002
```

Migration `20260605_0001_trigger_engine_v2_stage_1.py`:

- Adds `condition_ast`, `payload_ast`, `payload_return_type`, `action_mapping_ast`, `source_template_uuid`.
- Copies legacy `ast` into `payload_ast`.
- Sets legacy `payload_return_type` to `VALUE`.
- Backfills `source_template_uuid` from `target_template_uuid`; orphaned legacy rows use zero UUID for operator repair.
- Keeps old `ast` for rollback/read compatibility.

Migration `20260605_0002_drop_legacy_trigger_ast.py`:

- Drops old `ast` after deploy verification.
- Downgrade recreates `ast` from `payload_ast`; split v2 metadata cannot be represented by the legacy single-AST runtime.

`alembic/versions/*` is ignored in this repository. No tracked prior migrations existed on disk or in git history when this branch was authored, so `down_revision = None` in `20260605_0001` is intentional.

## Runtime Guarantees

- Related record reads are tenant-scoped and batched through one `$in` query per pending template group.
- LIST DML writes are accumulated and flushed through one unordered `bulk_write`.
- Runtime action dispatch re-checks `triggers/action_contracts.py`; mismatches raise `SystemContractViolation`.
- DML runtime writes and cascades use `Trigger.target_template_uuid` exclusively. If legacy `action_params.target_template_uuid` is present and differs, dispatch raises `SystemContractViolation`.
- `RecordService` lets `SystemContractViolation` propagate after the record save; the persisted create/update is acceptable, and the 500 response signals an engine/config invariant breach that stage-2 validation should have prevented. Other automation exceptions keep the existing log-and-continue behavior.
- Cascade is rejected when depth exceeds 5 (first rejected depth = 6).
- All Mongo reads/writes include `instance_uuid`; DML writes also include `template_uuid`.

## Hardening (task3, ГЗ-2 — 2026-06-06)

### 1. Идемпотентность UPDATE: $old/$new state-tracking
`EvaluationScope.previous_document` несёт снимок записи ДО изменения
(прокидывается из `RecordService.update_existing_record` →
`AutomationService.execute_automation_triggers(previous_record=...)` →
`EventReceptor.capture`). В AST доступны `FieldNode`-пути `$old.<field>` и
`$new.<field>` (валидатор выводит тип по базовому полю схемы; bare `$old`
в типизированных выражениях запрещён — 422). Оператор `ne` добавлен в
грамматику; `eq`/`ne` вычисляются и с None-операндами (на CREATE `$old.* is
None`, поэтому условие «поле изменилось» работает на всех событиях).
Идемпотентный паттерн: `AND(eq($new.payment,'картой'), ne($old.payment,$new.payment))`.

### 2. Частичный отказ батча (BulkWriteError)
`TargetAtomicWriter.flush()` ловит `BulkWriteError` (ordered=False): счётчики
берутся из `exc.details` (nMatched/nModified/nUpserted/nInserted), упавшие
индексы исключаются из touched-наборов — `fetch_touched_records()` и каскады
видят ТОЛЬКО реально записанные документы. `flush()` возвращает
`failed_count` + `write_errors[{index, code, errmsg}]`; `ActionDispatcher`
отдаёт `status: "partial"` при частичном сбое.

### 3. Защита от OOM и лимитов MongoDB
- `BatchDataLoader.CHUNK_SIZE = 500`: `load()` и `get_by_field_many()` режут
  `$in` на чанки ≤500 ID.
- `AutomationService.process_cron_triggers`: вместо длинного открытого
  курсора — пагинация по `_id` батчами `CRON_BATCH_SIZE = 500` (короткие
  запросы, нет cursor-timeout, ограниченная память).

### 4. Dirty-тесты
`playground/tests/test_engine_hardening.py`: двойной PATCH (повторное
списание исключено), partial bulk failure (2/3 записаны, каскад ровно по
двум, duplicate-key code 11000), стресс DataLoader 10k ID по чанкам,
юнит-семантика $old/$new включая CREATE.

### 5. Pre-images для каскадов (дополнение к п.1)
Каскадные UPDATE-события тоже несут `$old`-состояние: `ActionDispatcher`
перед `flush()` снимает pre-images целевых записей
(`TargetAtomicWriter.fetch_pre_images()`) и передаёт их в `cascade_callback`
→ `handle_event(previous_document=...)`. Благодаря этому пороговые
threshold-crossing условия (например, «баллы пересекли 100») идемпотентны
и в цепочках второго звена. Для insert/upsert-вставок pre-image отсутствует
— каскад корректно видит `$old.* == None` (семантика CREATE).
