# Validation report — PolyFaceLib

Branch: `restructure/math-leaf` · Commits: 893a7b5..52923cf (this report is committed after them) · PR/MR: not opened

## Summary for stakeholders

The library's mathematical building blocks were moved into their own top-level folder, so the code now has a clear bottom-to-top order: math, then shapes, then the 3D engine, then the browser parts. Before, the engine and the shapes depended on each other, which made both harder to understand and change. The program itself was not changed.

The same 83 automated tests pass before and after, the type check and the build succeed, and the published package is the same: its type definitions and stylesheet are byte-identical, and the only difference in the compiled code is the name of one internal variable chosen by the minifier. The demo pages are served correctly, but nobody looked at them in a real browser. The project's lint command was already broken before this work and still is.

The contributor guide and the specification now describe the folders as they really are.

Next steps for the team: review and merge the branch. No deployment action is needed. The previous version is intact on the main branch and can be restored at once.

Status: **Verified within limits**.

## What changed

`src/core/math/` was moved to `src/math/`. Imports were updated in 11 source files and 4 test files. The layout sections of CONTRIBUTING.md and SPECIFICATION.md were corrected (audit F1, F2).

```
before                         after
src/                           src/
├── core/                      ├── math/        ← moved
│   ├── math/                  ├── core/
│   ├── projector.ts           │   ├── projector.ts
│   ├── animator.ts            │   ├── animator.ts
│   └── frame.ts               │   └── frame.ts
├── polyhedra/ …               ├── polyhedra/ …
```

## Relocations performed

| From | To | Status |
|---|---|---|
| src/core/math/index.ts | src/math/index.ts | done |
| src/core/math/mat4.ts | src/math/mat4.ts | done |
| src/core/math/projection.ts | src/math/projection.ts | done |
| src/core/math/quat.ts | src/math/quat.ts | done |
| src/core/math/vec3.ts | src/math/vec3.ts | done |
| src/core/math/vec4.ts | src/math/vec4.ts | done |

## References updated

- Imports: `src/core/{animator,projector}.ts`, `src/polyhedra/{polyhedron,orientations,transitions}.ts`, `src/polyhedra/presets/{dodecahedron,icosahedron,prism}.ts`, `src/persistence/persistence.ts`, `src/index.ts`, `src/public-api.ts`, `tests/{math,polyhedra,transitions,zones}.test.ts`.
- Docs: CONTRIBUTING.md (layout tree, dependency order, "Math" section and its coverage line), SPECIFICATION.md §3 (tree; nonexistent `faces/{text,image,html,component}.ts` removed; `persistence/`, `sync/`, `events.ts`, `styles.css` added).
- Manifests, build, test config, CI: none needed (`tsup.config.ts`, `tsconfig.json`, `vitest.config.ts`, `package.json` do not name `core/math`).

## Compatibility shims in place

None. Source paths are not published (`files: ["dist"]`), and the bundled `.d.ts` is byte-identical (see Checks).

## Checks

| Check | Before (0b6e7d3) | After (52923cf) |
|---|---|---|
| `npm run typecheck` | pass | pass |
| `npm test` (vitest) | 83 passed, 7 files | 83 passed, 7 files |
| `npm run build` | pass | pass |
| `npm run lint` | fail (no ESLint config, pre-existing, audit R1) | fail, same |
| `npm pack --dry-run` | pass | pass |
| `node --check scripts/serve-examples.mjs` | pass | pass |
| `dist/` comparison | — | `.d.ts`, `.d.cts`, `index.js`, `index.cjs`, `styles.css` identical; `presets.js`/`.cjs` same size, identical after normalising the minifier's local name `o`↔`n`; `.map` differ by source path only (`restructure-evidence/dist-compare.txt`) |
| Clean environment (`rm -rf node_modules dist && npm ci`, typecheck, test, build) | — | pass (`restructure-evidence/clean-env-check.txt`) |
| Example pages + `dist/index.js`, `dist/styles.css`, `_shared.css` over HTTP | — | 18/18 HTTP 200 (`restructure-evidence/examples-http.txt`) |
| `gate.py --final` (relocation table, stale references, baseline, secrets, strict secrets) | — | all PASS |
| Old path `core/math` in the tree (excluding these reports) | — | 0 occurrences (`grep -rn core/math`) |

Gate evidence: `restructure-evidence/baseline-{before,step-2,step-3}.json`, `references-{before,step-2,step-3}.json`.

## Not verified

- **Example pages in a browser.** They were served and returned HTTP 200, but their JavaScript was not executed and nothing was rendered. Given the byte-identical `index.js`, risk is low.
- **Lint.** It cannot run before or after (audit R1), so import-ordering rules described in CONTRIBUTING were not checked.
- **Stale references in prose by `check_moves.py`.** The gate passed while CONTRIBUTING.md still named `src/core/math/` after step 2. The per-file relocation rows never search for the folder path itself. Adding a directory row caught that line but produced 29 false hits on the new, correct `../math/…` imports (`restructure-evidence/check-moves-directory-row-experiment.txt`). The two doc lines were found and fixed by a manual `grep`, which is the actual verification.
- **Secrets scan.** The two "high" hits in `package-lock.json` are the npm package name `path-key` with a semver range, a false positive. Nothing secret is in the repository as far as the heuristic scanner can tell.

## Excluded or reverted

- The directory row `src/core/math/ → src/math/`, briefly added to the plan as an experiment, was reverted (see above). No move was excluded.

## Rollback

```
git checkout main
git branch -D restructure/math-leaf
```

## Status

VERIFIED within the limits above.
