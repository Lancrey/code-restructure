# Migration plan — PolyFaceLib

Based on: architecture-audit.md (2026-10-30)

## Target layout

```
src/
├── index.ts, public-api.ts, events.ts, styles.css   # unchanged
├── math/                  # vec3, vec4, mat4, quat, projection, index.ts — leaf, imports nothing
├── core/                  # projector, animator, frame — engine, built on math + polyhedra
├── polyhedra/             # unchanged (presets/ included), built on math
├── input/  render/  faces/  transparency/  persistence/  sync/   # unchanged
tests/  examples/  scripts/                            # unchanged locations
```

## Rationale

Addresses F1 only: lifting `core/math/` one level removes the `core/ ↔ polyhedra/` directory cycle and makes the layering stated in CONTRIBUTING true (`math ← polyhedra ← core ← render/input ← public-api`). Everything else already follows the project's own conventions and is kept as is. Rejected: moving `core/projector.ts` instead (it is the engine's main piece and the doc names it as core), splitting `public-api.ts` (logic refactor), deleting the unused barrels (deletion is out of scope; `src/core/math/index.ts` moves with its folder, unchanged).

## Module boundaries

- `math/` — vector, matrix, quaternion and projection primitives. Depends on nothing. Everything may depend on it.
- `polyhedra/` — polyhedron data, validation, adjacency, orientations, transition tables, presets. May depend on `math/`. Must not depend on `core/`, `render/`, `input/`.
- `core/` — projector, animator, frame loop. May depend on `math/`, `polyhedra/`.
- `render/`, `input/`, `transparency/`, `faces/`, `persistence/` — browser bindings. May depend on the three above.
- `public-api.ts`, `sync/`, `index.ts` — top.

## Relocation table

| From | To | Note |
|---|---|---|
| src/core/math/index.ts | src/math/index.ts | lift leaf module (unused barrel, moved unchanged) |
| src/core/math/mat4.ts | src/math/mat4.ts | lift leaf module |
| src/core/math/projection.ts | src/math/projection.ts | lift leaf module |
| src/core/math/quat.ts | src/math/quat.ts | lift leaf module |
| src/core/math/vec3.ts | src/math/vec3.ts | lift leaf module |
| src/core/math/vec4.ts | src/math/vec4.ts | lift leaf module |

## References to update

- Imports `./math/…` in `src/core/animator.ts`, `src/core/projector.ts` → `../math/…`.
- Imports `../core/math/…` in `src/polyhedra/{polyhedron,orientations,transitions}.ts`, `src/persistence/persistence.ts` → `../math/…`.
- Imports `../../core/math/…` in `src/polyhedra/presets/{dodecahedron,icosahedron,prism}.ts` → `../../math/…`.
- Type re-exports `./core/math/…` in `src/index.ts:51–53` and imports in `src/public-api.ts:1–2` → `./math/…`.
- Test imports `../src/core/math/…` in `tests/{math,polyhedra,transitions,zones}.test.ts` → `../src/math/…`.
- CONTRIBUTING.md layout tree and "### 1. Math (`src/core/math/`)"; SPECIFICATION.md §3 tree (also corrected for F2: real `faces/` content, `persistence/`, `sync/`, `events.ts`).
- Not affected: `tsup.config.ts`, `tsconfig.json`, `vitest.config.ts`, `package.json`, `examples/`, `scripts/` (none names `core/math`).

## Compatibility measures

- No public path changes: the published package contains only `dist/`, and tsup bundles the `.d.ts`, so source paths are not part of the contract. Verified by comparing `dist/` and `npm pack --dry-run` before and after. No shim.

## Migration sequence

1. `docs: architecture audit and migration plan` — reports and evidence only.
2. `refactor: move core/math to src/math` — `git mv` of the six files + import updates in `src/` and `tests/` — checks: typecheck, test, build, gate.
3. `docs: update project layout in CONTRIBUTING and SPECIFICATION` — doc edits only — checks: gate (references).
4. `docs: architecture validation report` — final gate, validation report, evidence.

## Validation strategy

- Baseline checks available: `npm run typecheck`, `npm run lint`, `npm run build`, `npm test` (83 tests), plus extras `npm pack --dry-run` and `node --check scripts/serve-examples.mjs`.
- Checks that already fail before migration: `npm run lint` (no ESLint config, audit R1).
- Extra evidence: `dist/` built before and after, compared file by file (`.d.ts` must be identical; minified JS compared by content).
- Gaps: examples are not executed by any check; they import `dist/`, which the comparison above covers.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Missed import | low | `tsc --noEmit` fails on any unresolved import; `check_moves.py` greps old paths |
| Bundle output changes | low | dist diff before/after |
| Doc trees drift again | med | out of scope; R3 (CI) could add a layout check |

## Decisions needed from you

- [ ] Approve the move of `core/math/` to `src/math/` (the only structural change).
- [ ] Include the documentation corrections (step 3), or keep the run to code only?
- [ ] Keep the two unused barrels as they are (default), or open a separate task to remove them?
