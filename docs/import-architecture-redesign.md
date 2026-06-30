# Instance-schema import — staged dependency-resolving rewrite

Branch: `feature/import-staged-resolver` (off `main` @ 91d86c5)
Author: Claude (Эван) · Requested by: editor task `task-back-1.md`

## Problems being fixed

1. **Endpoint returns 200 even on failure.** `views.py:91` raises 422 only when
   `not report.valid and not report.created`. If *any* object was created the
   partial failure is reported as HTTP 200 → caller thinks it succeeded.
2. **Partial creation across stores.** Each sub-service commits independently
   (Postgres per stage; Mongo auto-commits). A mid-import failure leaves Mongo
   templates created while triggers/widgets/etc. are missing. No rollback.
3. **Monolithic, order-coupled creation.** Objects are built "fully formed" in a
   fixed stage order with a forward-ref fixup hack (`service.py:446-456`); this
   can't cleanly express or diagnose dependency cycles.

## Core model: Operations + Registry + Engine

Instead of "create each full object in a hand-fixed order", the import is a graph
of **fine-grained operations**, resolved by a generic engine.

### Operation
```
Operation:
  id:        str                  # stable, e.g. "template:shell:<bundleUuid>"
  kind:      OpKind               # enum (see grammar below)
  obj_ref:   (type, bundle_uuid|name)
  requires:  set[RegistryKey]     # deps that must be satisfied first
  produces:  set[RegistryKey]     # keys added to Registry on success
  apply():   awaitable            # performs the create/patch (within the txn)
  describe(): str                 # for the failure report
```
A `RegistryKey` is a typed token, e.g. `("template", uuid)`, `("template_relations", uuid)`,
`("template_complete", uuid)`, `("trigger", uuid)`. Deps are expressed as keys,
not object instances, so the engine stays generic.

### Registry
In-memory for the duration of one import. Holds the set of satisfied
`RegistryKey`s plus the produced entities (new UUIDs, created model handles).
An op is *runnable* iff `requires ⊆ registry.keys`. On success its `produces`
keys are added. This is "register immediately after successful creation".

### Engine (iterative passes)
```
pending = all operations
loop:
    ran_this_pass = 0
    for op in list(pending):
        if op.requires ⊆ registry:
            op.apply(); registry.add(op.produces); pending.remove(op); ran_this_pass++
    if not pending: break            # success
    if ran_this_pass == 0: break     # stuck → cycle / missing / bad ref
return pending                       # empty = full success
```
This is exactly stages 3–5 of the spec (Registry, Pending queue, repeated passes,
stop on zero-progress).

## Operation grammar (decomposition + dependency order — my design)

UUIDs for every bundle object are **pre-allocated** up front (`id_map`: old→new),
so dependencies are known before anything is written. Decomposition by type:

**Template** (Mongo doc with a `schema` dict). Split so mutual relations don't deadlock:
- `template:shell` — create the doc with **simple (non-referential) fields only**.
  `requires: {}` → always runnable first. `produces: {("template", uuid)}`.
- `template:relations` — add `relation` / `relation_list` fields.
  `requires: {("template", target) for each related template}` (target just needs
  to *exist* as a shell). `produces: {("template_relations", uuid)}`.
- `template:formulas` — add `formula` / `aggregation` fields.
  `requires:` the referenced **field** on the target to exist:
  simple field → `("template", target)`; relation field → `("template_relations", target)`;
  formula field → `("template_formulas", target)`. `produces: {("template_formulas", uuid)}`.
- `template:finalize` — validate + persist final schema.
  `requires: {("template_relations", uuid), ("template_formulas", uuid)}`.
  `produces: {("template_complete", uuid)}`.

  → Mutual `relation`s (Orders↔Clients) resolve fine (both shells first, relations
  attach independently). Only a genuine `formula→formula→…→back` cycle stalls →
  reported, not silently appended like today.

**Notification** — `requires: {("template_complete", source)}` if `source_template_uuid`
present (placeholders validated against the finalized schema), else `{}`. Postgres.

**Policy** — `requires: {("template_complete", template_by_name)}` (masks validated
against fields). Postgres.

**Widget** — `requires: {("template_complete", target)} ∪ {complete for each template in ast_filter}`. Postgres.

**Trigger** — `requires: {("template_complete", source)} ∪ {target if present} ∪ {complete for each template ref in condition/payload/action ASTs}`. Two ops:
- `trigger:create` (Postgres row).
- `trigger:inject` — embed trigger metadata into the target template's Mongo schema;
  `requires: {("trigger", uuid), ("template_complete", target)}`.
  (Trigger→trigger *cascade* is a runtime concern, **not** an import dependency, so
  we do not order triggers by cascade — removes a needless coupling.)

## Validation (stage 1 — before any operation is built)
Pure, side-effect-free, returns structured errors; no ordering decisions:
- JSON / Pydantic shape (already exists).
- Required fields.
- **Duplicates**: bundle UUIDs, template names, **field names within a template** (new),
  policy/widget/trigger names.
- **Referential integrity within the bundle**: every `target_template_uuid`,
  `template_name`, and AST template/field ref resolves to a bundle object. (Today the
  *trigger* validator checks the live DB, which lets dangling refs slip in — we
  validate against the bundle.)
- If any error → **HTTP 422, nothing created.**

## Atomicity (fixes bug #1 + #2)
Mongo here is **standalone** (no replica set found → no multi-doc Mongo txns), so:
- **Postgres**: one transaction for the whole import. Sub-services refactored to
  `flush()` (not `commit()`); the import orchestrator owns the single
  `commit()` / `rollback()`.
- **Mongo**: every write (template insert, schema `$set`, trigger-meta inject) is
  recorded in a **compensation log**. On any failure → roll back Postgres **and**
  run the compensation log in reverse (delete inserted template docs; revert
  in-place schema edits using the captured prior value).
- Order of durability: resolve the **entire** graph first (engine runs all ops
  inside the open transaction / against the compensation log); commit only when
  `pending` is empty. If the engine stalls → roll back everything.

→ Result is **all-or-nothing**: either the full bundle is applied, or nothing is,
and the response says exactly which it was.

(REPLACE mode: `previous_schema` is already exported before the wipe; wipe+create
run under the same all-or-nothing boundary, with `previous_schema` as the
last-resort restore. Flagged as the one area needing extra care.)

## HTTP semantics (fixes bug #1)
- `200` — fully applied (or `dry_run` that fully resolved). `report.valid=True`,
  `created` counts complete.
- `422` — validation failed **or** the graph could not be fully resolved
  (cycle / missing / bad ref). `report.valid=False`, **`created` is empty**
  (nothing was committed), `errors`/`unresolved` populated.
- `409` — MERGE conflict with existing objects.
- `403` — unauthorized (unchanged).
- `500` — unexpected infra error during apply → rolled back, nothing created.

## Failure report (stage 6)
Extend `ImportReport` with `unresolved: List[UnresolvedOp]`:
```
UnresolvedOp: { op_id, obj_type, obj_name, missing: List[RegistryKey],
                reason: "missing_dependency" | "cycle" | "dangling_ref" }
```
Plus a `cycles: List[List[str]]` diagnosis (the strongly-connected component of
the stuck sub-graph) so a bad schema is reported precisely: *which* objects form
the cycle, or *which* dependency never appeared.

## Dry-run
Run validation + build the operation graph + run the engine **inside a
transaction that is always rolled back**, so dry-run reports the real
apply-order, the real `unresolved`/`cycles`, and any apply-time errors — without
persisting. (Today dry-run stops before creation and can't catch create-time issues.)

## Tests to add (stage: "complex dependencies the old design couldn't resolve")
1. **Mutual relations** Orders↔Clients (both directions) — must succeed (old fixed
   order + forward-ref hack is brittle here).
2. **Deep formula chain** A.f→B.f→C.simple — must resolve in the right pass order.
3. **Formula cycle** A.f→B.f→A.f — must FAIL with `cycles=[[A,B]]`, nothing created.
4. **Dangling ref** trigger → template not in bundle — 422, nothing created.
5. **Atomicity** force a failure on the last trigger → assert **zero** templates,
   widgets, policies, notifications, triggers persisted (Postgres + Mongo).
6. **Honest status** the above returns non-200.
7. Keep the existing loyalty round-trip + large-instance tests green.

## Rollout
Implement behind the same endpoint (drop-in replacement of `import_schema`),
keep `ImportReport` backward-compatible (only additive fields). Existing tests must
stay green; new tests cover the gaps.
