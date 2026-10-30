# Go

## How imports resolve
- An import path is `<module path>/<directory>`; the module path comes from `go.mod`. There are no relative imports. Moving a directory therefore changes the import path of every package inside it, and every importer in the module (and any external module) must be updated.
- A directory is one package; all `.go` files in it share a `package` clause. Moving a single file into another directory changes its package, which is a refactor of its API, not just its location.
- `internal/`: packages under an `internal/` directory can only be imported by code rooted at the parent of `internal/`. Moving a package into `internal/` is the Go-idiomatic way to make it private — and it will break any external importer, so check whether the module is consumed elsewhere.
- Multi-module repos (`go.work`, nested `go.mod`) resolve each module separately; moving code across module boundaries is a dependency change.

## Files that pin paths — check every one
| File | What |
|---|---|
| `go.mod` | module path (the root of every import path), `replace` directives pointing at local dirs |
| `go.work` | `use ./path` lines |
| `Makefile`, `Taskfile.yml`, `magefile.go` | `go build ./cmd/x`, `go test ./pkg/...` |
| `.goreleaser.yml` | `main: ./cmd/x`, `builds[].dir` |
| `Dockerfile` | `COPY`, `go build -o … ./cmd/x` |
| CI workflows | package globs |
| `//go:generate` directives | often invoke tools with relative paths |
| `//go:embed` directives | embed paths are relative to the file — moving the file breaks them silently until build |
| `.golangci.yml` | `run.skip-dirs`, path-based `exclude-rules` |
| Protobuf / sqlc / ent / wire config | output directories and package names |

## Runtime path references to grep for
```
os.Open(     os.ReadFile(     filepath.Join(    "testdata/"    embed.FS
runtime.Caller    os.Getwd(    plugin.Open(      "configs/"
```
`testdata/` directories are relative to the package and move with it; test fixtures referenced by `../../fixtures` do not.

## Idiomatic layouts

**Small tool / library**
```
go.mod
main.go            # or <name>.go for a library
<a few>.go
<name>_test.go
```
A flat module is correct for small projects; the `cmd/pkg/internal` scaffold on a 1,000-line tool is the over-engineering case.

**Application with several binaries**
```
cmd/<binary>/main.go      # thin: parse flags, wire, call internal
internal/<domain>/        # the actual code, unimportable from outside
internal/platform/ or infra/   # db, http, queue adapters
pkg/                      # only if something is meant for external import
api/ or proto/
configs/
scripts/
```
`pkg/` is contested in the community; don't introduce it unless the project exports packages for other modules to use.

**Plugins** — Go has no good runtime plugin story; "plugins" are usually packages registered via `init()` and blank imports (`_ "app/plugins/foo"`) in a central file. Moving a plugin package means updating that blank-import list — grep for `_ "` imports.

## Tooling
- **gopls rename / gorename**: `gopls rename -w path/to/pkg` for identifiers; for *packages*, the reliable method is `gomvpkg -from … -to …` (golang.org/x/tools/cmd/gomvpkg) which rewrites import paths across the module, or a `sed` on import lines followed by `gofmt`/`goimports -w .`.
- **`go list -deps ./...`**, `go mod graph`, `goda graph` / `godepgraph` for dependency pictures; cycles are compile errors in Go, so `go build ./...` is the cycle check.
- **Verification**: `go build ./...`, `go vet ./...`, `go test ./...` (`-count=1` to defeat the cache), `go test -run xxx ./...` to compile tests without running them, `golangci-lint run`, `go mod tidy` (should produce no diff; a diff means an import path changed in a way that altered dependencies), `go list ./... | wc -l` before and after (a dropped package means a directory left the module's reach).

## Traps
- Renaming the directory of a package without renaming the `package` clause is legal but confusing; renaming the clause changes every call site (`oldpkg.Func` → `newpkg.Func`).
- `//go:embed` and `//go:generate` paths break on move with no IDE warning.
- Moving a package out of `internal/` makes it public API; moving one in may break downstream modules.
- Test caching: run `go test -count=1` for the baseline comparison.
- `replace` directives with relative paths in `go.mod` of dependents (other modules in a workspace) must follow the move.
