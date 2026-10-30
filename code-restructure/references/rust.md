# Rust

## How modules resolve
- The module tree is declared, not discovered: `mod foo;` in `lib.rs`/`main.rs` (or a parent module) tells the compiler to look for `foo.rs` or `foo/mod.rs` next to the declaring file. Moving a file without updating the `mod` declaration is a compile error — loud, which is good. Moving it to a different parent changes every `crate::…`/`super::…` path that reaches it.
- `use` paths are absolute from the crate root (`crate::`), the parent (`super::`), or an external crate. Re-exports (`pub use`) are how a crate keeps an old path alive after a move.
- A crate is the unit of compilation and of public API; `pub` items at the crate root (or re-exported there) are the public surface. Workspaces hold several crates; moving code between crates is a dependency change in `Cargo.toml`.
- Binaries: `src/main.rs` by default, plus `src/bin/*.rs` auto-discovered, plus explicit `[[bin]]` entries with `path =`.

## Files that pin paths — check every one
| File | Keys |
|---|---|
| `Cargo.toml` | `[workspace] members`, `exclude`, `[lib] path`, `[[bin]] path`, `[[example]]`, `[[test]] path`, `[[bench]]`, `build = "build.rs"`, `include`/`exclude` for packaging, path dependencies `foo = { path = "../foo" }` |
| `build.rs` | `println!("cargo:rerun-if-changed=…")`, `include_str!`/`include_bytes!` of generated files, `OUT_DIR` consumers |
| `.cargo/config.toml` | `[alias]`, `[build] target-dir` |
| `rust-toolchain.toml`, `rustfmt.toml`, `clippy.toml` | rarely path-specific; `rustfmt.toml` `ignore = [...]` is |
| `deny.toml`, `Makefile.toml` (cargo-make), `justfile` | package names and paths |
| CI workflows | `cargo test -p <crate>`, `--manifest-path` |
| `Dockerfile` | `COPY crates/… `, cargo-chef recipes |
| `wasm-pack`, `tauri.conf.json`, `Trunk.toml` | entry crate / dist dir |

## Compile-time path references to grep for
```
include_str!(     include_bytes!(     include!(     env!("CARGO_MANIFEST_DIR")
concat!(env!("CARGO_MANIFEST_DIR"),    #[path = "…"]    mod tests  (with #[cfg(test)])
std::fs::read     Path::new("        "tests/fixtures"
```
`include_str!("../assets/x.txt")` is relative to the *source file*; it moves with the file only if the asset is moved by the same amount. `#[path = "..."]` on a `mod` overrides discovery and is the explicit case to update.

## Idiomatic layouts

**Single crate**
```
Cargo.toml
src/
  lib.rs          # public API, mod declarations, re-exports
  main.rs         # thin, calls into lib
  <module>.rs  or  <module>/mod.rs  (2018 edition prefers <module>.rs + <module>/ dir)
tests/            # integration tests, one crate each
benches/  examples/
```

**Workspace** — split when there are genuinely separable units (a core library, a CLI, a server, a shared types crate) or when compile times demand it:
```
Cargo.toml        # [workspace] members = ["crates/*"]
crates/
  <name>-core/
  <name>-cli/
  <name>-server/
```
Keep `[workspace.dependencies]` and `[workspace.package]` for shared versions. Internal dependencies use `path =`.

**Plugins** — usually a trait + registration; sometimes `inventory` or `linkme` crates for distributed registration. Moving a plugin module does not change registration with those crates, but does change its `mod` declaration.

## Tooling
- **rust-analyzer** "Move item"/"Rename" (via editor) and **`cargo fix --edition`**-style rewrites; no first-party CLI for moving modules. Practical method: `git mv`, update `mod` declarations, then let `cargo check` list every broken `use` path and fix them (rustc error messages are precise enough to script against).
- **cargo-modules** (`cargo modules structure`, `cargo modules dependencies`) for the module/dependency graph; **cargo-depgraph** for crates; **cargo-udeps** (nightly) for unused crate deps — flag only.
- **Verification**: `cargo check --workspace --all-targets` (fast, catches all path errors incl. tests/examples/benches), `cargo clippy --all-targets -- -D warnings` only if the project already enforces it, `cargo test --workspace`, `cargo doc --no-deps` (catches broken intra-doc links after moves), `cargo package --list` (what ships), `cargo public-api` or `cargo semver-checks` if the crate is published — this is the direct test for "did I break the public surface".

## Traps
- `pub use` at the old path preserves external callers; forgetting it on a published crate is a semver-breaking change. `cargo semver-checks` catches it.
- `#[cfg(test)] mod tests` inside a moved file keeps working; `tests/*.rs` integration tests import the crate by name and only break if public paths changed.
- `mod.rs` vs `<name>.rs` styles can coexist; mixing them within one restructure is legal but confusing — pick the style the project already uses.
- Macros exported with `#[macro_export]` live at the crate root regardless of where they are defined; `macro_rules!` without it are textually scoped and moving the file can change which modules see them.
- Moving code between workspace crates can create a dependency cycle between crates, which cargo rejects — check `[dependencies]` before planning the split.
- Edition differences (2015 `extern crate`, 2018+ paths) in old organically-grown repos.
