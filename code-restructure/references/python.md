# Python

## How imports resolve
- `import a.b` searches `sys.path`: the script's directory (or cwd for `-m`), `PYTHONPATH`, site-packages. A package is a directory on that path; since PEP 420 a directory without `__init__.py` is a namespace package, so missing `__init__.py` does not stop imports — but it does change what packaging tools include.
- Relative imports (`from . import x`) depend on `__package__`; a file run directly as a script has none, which is why `python src/app/cli.py` breaks after a move while `python -m app.cli` works.
- Editable installs (`pip install -e .`) map the import path to the source tree through the manifest. After moving packages, a stale editable install keeps old paths importable — re-install before trusting the baseline.
- Stale `__pycache__` can mask a missing module for a while; delete them (`find . -name __pycache__ -exec rm -r {} +`) before verification.

## Files that pin paths — check every one
| File | Keys |
|---|---|
| `pyproject.toml` | `[tool.setuptools] packages`, `package-dir`, `[tool.setuptools.packages.find] where/include`, `[project.scripts]` (entry points as `module:func`), `[tool.pytest.ini_options] testpaths`, `pythonpath`, `[tool.ruff] src`, `per-file-ignores`, `[tool.mypy] files`, `mypy_path`, `[tool.coverage.run] source` |
| `setup.py` / `setup.cfg` | `packages=`, `package_dir=`, `entry_points=`, `package_data=`, `scripts=` |
| `MANIFEST.in` | `include`/`recursive-include` paths |
| `pytest.ini`, `tox.ini`, `conftest.py` | `testpaths`, `rootdir`, fixtures that build paths with `__file__` |
| `requirements*.txt` | `-e ./path` lines |
| `Dockerfile`, `docker-compose.yml` | `COPY src/ …`, `CMD ["python", "app/main.py"]`, `WORKDIR` |
| `.github/workflows/*.yml`, `.gitlab-ci.yml` | `python scripts/x.py`, `pytest tests/unit` |
| `Makefile`, `noxfile.py`, `tasks.py` | hard-coded paths |
| `.pre-commit-config.yaml` | `files:` / `exclude:` regexes |
| `alembic.ini` | `script_location` |
| Django `settings.py` | `INSTALLED_APPS` (dotted paths), `ROOT_URLCONF`, `WSGI_APPLICATION`, `TEMPLATES[...]['DIRS']`, `STATICFILES_DIRS`, `MIGRATION_MODULES` |
| Flask / FastAPI | app factory import string (`gunicorn app.wsgi:app`, `uvicorn app.main:app`), blueprint imports, `template_folder=`, `static_folder=` |
| Celery | `include=[...]`, `autodiscover_tasks` |
| Sphinx `conf.py` | `sys.path.insert`, `autodoc` module names |

## Runtime path references to grep for
```
open(                 Path(__file__)       os.path.dirname(__file__)
importlib.import_module    __import__(    pkgutil.walk_packages
entry_points(         load_entry_point    resources.files(
"templates"           "static"            "fixtures"
```
Also grep for the string form of every module you plan to move: `"app.utils"`, `'app.utils'`, `app/utils` — string references do not show in import analysis.

## Idiomatic layouts (pick by size and kind)

**Small application (< ~30 modules)** — flat package, grouped by concern:
```
app/
  __init__.py
  cli.py / main.py
  config.py
  models.py
  services.py
  io/ or adapters/
tests/
```

**Library or published package** — `src/` layout, which prevents the cwd shadowing the installed package during tests (the reason the Python Packaging Authority recommends it):
```
src/<package>/
tests/
pyproject.toml
```
Moving to `src/` layout requires `package-dir`/`packages.find where = ["src"]` in the manifest and a re-install; it is one of the most common "everything imports fine locally, CI breaks" moves.

**Larger application** — feature packages, shared infrastructure separate:
```
src/<app>/
  core/        # domain, no I/O
  features/<feature>/   or  <feature>/ directly
  infra/       # db, http clients, filesystem
  plugins/<name>/
  cli/
tests/<mirrors src>
```

**Django** — one app per bounded context, project package only holds settings/urls/wsgi; moving an app means updating `INSTALLED_APPS`, the `AppConfig.name`, migration dependencies (`('old_app', '0001')` strings) and any `apps.get_model("old_app", …)` call. Migrations reference app labels, so renaming an app label is a schema change in disguise — avoid it in a structural refactor.

**Plugins / extensions** — one directory per plugin, loaded by a registry; keep the discovery mechanism exactly as it is (entry points, `pkgutil` scan, explicit list) and make sure the new location is what it scans.

## Tooling
- **rope** (`pip install rope`): `move_module`, `rename` update imports across the project. Use `from rope.base.project import Project` in a short script, or `python -m rope` via an editor. Most reliable option for pure-Python moves.
- **LibCST** codemods for mechanical import rewrites when rope cannot handle the layout.
- **Dependency graph**: `pydeps <package> --show-deps --no-output`, `pipdeptree` (third-party only), `import-linter` to assert layering rules after the move (useful to add as a CI check if the user agrees).
- **Cycle detection**: `pydeps --show-cycles`, or `python -X importtime -c "import app"` to see import order.
- **Verification**: `python -c "import <every_top_level_module>"`, `pytest --collect-only -q` (catches import errors in tests without running them), `python -m compileall -q .`, `ruff check` / `flake8` (F401/F811 after re-exports), `mypy` (catches shadowed names).

## Traps
- A re-export shim `old/path.py` containing `from new.path import *` does not re-export names starting with `_` and ignores `__all__` absence subtleties; list names explicitly if the old module had private names used elsewhere.
- Test discovery: pytest collects by `test_*.py` under `testpaths`; moving tests out of the configured path silently drops them — compare `pytest --collect-only | wc -l` before and after.
- Two packages with the same name in `src/` and at the root shadow each other depending on cwd.
- `__init__.py` with side effects (registering plugins, configuring logging): moving modules changes import order and therefore when side effects run.
- Data files next to code (`package_data`) must be re-declared when the package moves.
