# JavaScript / TypeScript

## How imports resolve
- **Relative** (`./x`, `../y`): relative to the importing file. Every move of a file changes the relative path of every import *in* it and *to* it. This is the bulk of the mechanical work.
- **Bare** (`lodash`, `@scope/pkg`): Node resolution walks up `node_modules`; in a monorepo workspace, local packages resolve by their `package.json` `name`, not their folder.
- **Aliases**: `tsconfig.json` `compilerOptions.paths` + `baseUrl`; bundler aliases (`vite.config` `resolve.alias`, `webpack` `resolve.alias`); Jest `moduleNameMapper`; `package.json` `imports` (`#internal/*`). Each is a separate map and they drift: a repo often has the same alias defined in three places. Find all of them before moving anything.
- **Directory imports** (`./utils` → `./utils/index.ts`) work in CJS and bundlers but not in strict ESM; moving a file into a folder with an index can change behaviour under Node ESM.
- **Extensions**: TS `moduleResolution: node16/nodenext/bundler` changes whether `.js` extensions are required in imports.

## Files that pin paths — check every one
| File | Keys |
|---|---|
| `package.json` | `main`, `module`, `types`, `exports` (subpath map — this is the public API of a library), `bin`, `files`, `workspaces`, `scripts` (`node scripts/x.js`, `tsc -p tsconfig.build.json`) |
| `tsconfig*.json` | `include`, `exclude`, `rootDir`, `outDir`, `paths`, `baseUrl`, `references` (project references in monorepos) |
| `vite.config.*`, `webpack.config.*`, `rollup.config.*`, `esbuild` scripts, `next.config.*`, `nuxt.config.*`, `astro.config.*` | `entry`, `resolve.alias`, `root`, `publicDir`, `input` |
| `jest.config.*`, `vitest.config.*` | `roots`, `testMatch`, `moduleNameMapper`, `setupFiles` |
| `.eslintrc*` / `eslint.config.*` | `overrides[].files`, `import/resolver` settings, `ignorePatterns` |
| `.prettierignore`, `.eslintignore`, `.npmignore` | path globs |
| `babel.config.*` | `module-resolver` plugin aliases |
| `turbo.json`, `nx.json`, `pnpm-workspace.yaml`, `lerna.json` | package globs, pipeline inputs/outputs |
| `Dockerfile`, CI workflows | `COPY`, `npm run …`, `node dist/…` |
| `index.html` | `<script src>` |
| `angular.json`, `nest-cli.json` | source roots, entry files |

## Runtime path references to grep for
```
require(`         import(`        require.resolve(     __dirname      import.meta.url
fs.readFile       path.join(      path.resolve(        readdirSync(   glob(
new URL("./       process.cwd()   "public/"            "assets/"
```
Dynamic `import(\`./features/${name}\`)` is common in plugin systems and route loaders (Next.js, Nuxt, Remix all derive routes from the filesystem — in those frameworks **the folder layout is the routing table**; moving a file changes a URL).

## Idiomatic layouts

**Small app / script collection**
```
src/
  index.ts
  lib/
  cli/
tests/ or src/**/*.test.ts
```

**Feature-based front end (React/Vue/Svelte)** — the current mainstream recommendation is grouping by feature rather than by type (`components/`, `hooks/`, `utils/` at the top level is the organically-grown anti-pattern this skill usually meets):
```
src/
  app/            # routing, providers, global layout
  features/<feature>/
    components/ hooks/ api/ index.ts   # index.ts = the feature's public surface
  shared/  (or components/ui, lib/, hooks/)
  entities/ or types/
```

**Node service (Express/Fastify/Nest)**
```
src/
  modules/<domain>/ (controller, service, repository, dto)
  common/ or shared/
  config/
  main.ts
```
Nest already enforces modules; moving a Nest module means updating the `imports: []` of consumers.

**Library** — `src/` + `exports` map in `package.json`; every subpath in `exports` is a public contract. Add new subpaths freely, never remove or rename one without a forwarding entry.

**Monorepo** — `packages/*` or `apps/* + packages/*`; each package has its own `package.json`, `tsconfig` extending a base, and is referenced by name. Moving code between packages is a dependency change, not a file move: update both `package.json` dependency lists and `tsconfig.references`.

**Chrome / browser extension** — `manifest.json` pins every script, page and asset path; the bundler config maps entries to it. Treat `manifest.json` as the primary path-pinning file.

## Tooling
- **ts-morph**: `sourceFile.move("new/path.ts")` rewrites every import referencing the file across the project. The safest option for TS projects; a 20-line script handles most moves.
- **jscodeshift** for custom codemods on JS.
- **VS Code / WebStorm** "Move file" updates imports — fine interactively, not scriptable.
- **madge**: `madge --circular src/` for cycles, `madge --image deps.svg src/` for the graph. **dependency-cruiser** adds enforceable rules (`depcruise --validate`), useful to keep the new structure honest in CI.
- **knip** or `ts-prune` for unused exports (flag only, don't delete).
- **Verification**: `tsc --noEmit` (catches every broken import in TS), `eslint . --rule 'import/no-unresolved: error'`, `npm run build`, `vitest --run` / `jest`, `node --check` for plain JS files, `npm pack --dry-run` to see what the published package would contain.

## Traps
- Barrel files (`index.ts` re-exporting everything) make circular imports easy and tree-shaking hard; when you create a feature `index.ts`, export only the intended public surface.
- `exports` in `package.json` is strict: once present, any subpath not listed becomes unimportable from outside. Adding it during a restructure is a breaking change unless every previously importable path is listed.
- Case-sensitivity: a `git mv Utils.ts utils.ts` works on macOS but may need two steps; CI on Linux will see what macOS hides.
- Test files colocated with source move with it; test files in a mirror tree do not — update `testMatch`/`roots` and compare `--listTests` counts before and after.
- Framework filesystem conventions (`pages/`, `app/`, `routes/`, `public/`, `migrations/`, `prisma/`): moving them changes behaviour. Leave them where the framework expects them.
- Type-only imports and `declare module` augmentations referencing paths (`declare module "*.svg"`) live in `.d.ts` files that `tsc` may or may not include after the move — check `include`.
