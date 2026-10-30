# PHP (Composer, Laravel, Symfony, WordPress)

## How classes resolve
- Modern PHP loads classes through Composer's autoloader. **PSR-4** maps a namespace prefix to a directory in `composer.json` (`"App\\": "src/"` or `"app/"`); the class `App\Services\Mailer` must then live at `<dir>/Services/Mailer.php` with a matching `namespace`. Moving a file without updating its `namespace` (and every `use` of it) breaks at runtime — not at parse time — so a broken move only shows when the code path executes.
- `classmap` and `files` autoload entries pin explicit paths. Legacy code often has `require_once __DIR__ . '/../lib/x.php'` chains instead of autoloading; those are relative paths that move with the file only if both ends move together.
- After editing `composer.json` autoload or moving classmap'd files: `composer dump-autoload` (`-o` for the optimized map). A stale `vendor/composer/autoload_classmap.php` masks broken moves until the next dump.

## Files that pin paths — check every one
| File | Keys |
|---|---|
| `composer.json` | `autoload.psr-4`, `autoload.classmap`, `autoload.files`, `autoload-dev`, `bin`, `scripts`, `extra.laravel.providers` |
| `phpunit.xml(.dist)` | `<testsuite><directory>`, `<source>/<include>`, `bootstrap=` |
| `phpstan.neon`, `psalm.xml`, `.php-cs-fixer.php`, `rector.php`, `ecs.php` | `paths`, `excludePaths`, `ignoreErrors` with path patterns |
| `.env`, `config/*.php` | paths (`storage_path`, `view.paths`), class names as strings |
| Laravel `config/app.php` | `providers` and `aliases` (FQNs), `bootstrap/providers.php` (Laravel 11+), `bootstrap/app.php` route/middleware registration |
| Laravel routes `routes/*.php` | `[Controller::class, 'method']` (safe) vs `'App\Http\Controllers\X@method'` strings (break silently) |
| Laravel `app/Console/Kernel.php` / `routes/console.php` | command class references; `$this->load(__DIR__.'/Commands')` directory scans |
| Symfony `config/services.yaml` | `App\:` resource globs (`resource: '../src/'`, `exclude:`), tagged services by FQN, `config/routes.yaml` controller paths and attribute `resource` dirs |
| `webpack.mix.js`, `vite.config.js` | asset entries, `resources/` paths |
| Blade/Twig templates | `@include('partials.x')` / `{% include 'x.html.twig' %}` resolve against configured view dirs; view namespaces |
| Migrations | class names were global pre-Laravel 9 (anonymous since); `database/migrations` path is configured |
| `Dockerfile`, `nginx.conf`, `.htaccess`, deploy scripts (Forge, Envoyer, Deployer) | `public/` root, `artisan` path, `php bin/console` |
| WordPress `plugin.php` header, `functions.php` | `plugin_dir_path(__FILE__)`, `get_template_directory()`, hook callbacks as string function names |

## Runtime references to grep for
```
require_once    include(    __DIR__ . '    dirname(__FILE__)    base_path(    app_path(
resource_path(  storage_path(   class_exists('     app('App\\     resolve('    make('
'App\\          "App\\\\       ::class     view('      @include(   Route::    config('
glob(           scandir(      is_file(
```
String class names (`'App\Services\Foo'`) are the usual silent breakage; `Foo::class` references are refactor-safe.

## Idiomatic layouts

**Laravel** — the framework's default tree is the convention; organic growth usually means a bloated `app/Http/Controllers` and `app/Models`, and business logic in controllers. Two accepted directions, both inside `app/`:
- *Keep the framework layout, add layers*: `app/Services/`, `app/Actions/`, `app/DTOs/`, `app/Repositories/`, `app/Support/`. Low risk: no autoload change, nothing the framework scans moves.
- *Modular / domain-oriented*: `app/Domain/<Domain>/{Models,Actions,Events}`, `app/Http/<Domain>/…`, or a `modules/<Name>/` tree with its own PSR-4 entry and service provider (what `nwidart/laravel-modules` formalizes). Higher value, higher risk: model namespaces are stored in `morph` maps (`Relation::morphMap`, the `*_type` columns of polymorphic relations hold FQNs — a database content issue, not just code), in queued jobs serialized in the `jobs`/`failed_jobs` tables, in `password_resets`/notification tables, and in cached config. Plan a `Relation::enforceMorphMap` or alias for every moved model with polymorphic use, and flush queues/caches before switching.
Leave in place: `bootstrap/`, `config/`, `database/`, `public/`, `resources/`, `routes/`, `storage/`, `tests/` top-level structure.

**Symfony** — `src/` with PSR-4 `App\`; service autowiring scans `src/` with exclusions. Organising by domain inside `src/` (`src/Order/`, `src/Shared/`) is idiomatic and safe as long as `services.yaml` `resource`/`exclude` globs still cover the files; controllers must remain under the attribute-routing `resource` path or be added to it.

**Legacy procedural / WordPress** — explicit `require` graphs; introduce an autoloader only if the user agrees, as it changes load order and can expose previously masked redeclaration errors. Otherwise the restructure is: group includes by concern, keep one entry file, update every `require` path.

## Tooling
- **Rector** (`rector.php` with `RenameClassRector`/`RenameNamespaceRector` or `PSR4` set) rewrites namespaces and `use` statements across the codebase, including string references it can prove. The scriptable option.
- **PhpStorm** "Move class" / "Refactor › Move namespace" handles files, namespaces, uses and many string references, and runs `composer dump-autoload`.
- **phpstan / psalm** at the project's level after the move: unresolved classes become errors. If the project had no static analysis, run `phpstan analyse --level 0 src` just for class-resolution checks.
- **composer validate**, `composer dump-autoload -o` and `composer show --tree` for dependency sanity; **deptrac** to enforce layer/module boundaries afterwards.
- **Verification**: `php -l` on every moved file (`find . -name '*.php' -not -path './vendor/*' -exec php -l {} \;`), `phpunit`, `php artisan route:list`, `php artisan config:cache && php artisan config:clear` (cached config includes FQNs), `php artisan optimize:clear`, `php artisan about` / `bin/console debug:container` / `debug:router` to confirm services and routes are still registered, `php artisan schedule:list`, `php artisan queue:work --once` on a test job if queues serialize moved classes.

## Traps
- PSR-4 is case-sensitive on Linux; a file named `userService.php` for class `UserService` works on macOS and fails in production.
- Laravel caches (`bootstrap/cache/*.php`, `config.php`, `routes-*.php`, `services.php`, `packages.php`) hold pre-move FQNs and paths; clear them in the migration steps and in the deploy checklist.
- Polymorphic `*_type` columns and serialized jobs/notifications store model FQNs — a code move becomes a data migration unless morph maps are in place.
- `$this->load(__DIR__.'/Commands')` and similar directory scans silently stop finding commands moved elsewhere.
- Facade aliases, `config/app.php` providers, `composer.json` `extra.laravel.providers` and `dont-discover` all name classes by FQN.
- Blade view paths map to directories (`resources/views/admin/users.blade.php` ↔ `view('admin.users')`); moving templates renames views, which are referenced from controllers, mail classes and other views.

## Legacy PHP without Composer, tests or a database here
Common case for this skill: an application written for PHP 5.x (mcrypt, `each()`, `$s{0}` string offsets),
no `composer.json`, no tests, entry points that need MySQL. The floor is built from what can run:
- **Syntax**: `baseline.py` runs `php -l` on every `.php` file and reports `N passed, M failed`; files that
  fail only because of the PHP version (string offsets with braces, `each()`) fail identically before and
  after, which is fine as long as the count does not rise.
- **Version gaps**: never edit the repository to make it run on the local PHP. Put shims OUTSIDE the
  repo and apply them to a temporary copy of the tree: constants for removed extensions
  (`MCRYPT_RIJNDAEL_256`), polyfills (`each`), a dummy config file, a SQLite or in-memory stand-in for the
  database class. List every shim in the validation report under "Not verified": what it replaces is
  exactly what was not exercised.
- **Runtime floor**: one PHP process per check, printing `N passed, M failed` at the end: load the config
  and bootstrap, autoload every class (`class_exists` over the classes directory), instantiate each
  module, render one or two real templates through the template engine, run each moved library once
  (write and read a file with the spreadsheet library, compile a stylesheet with the LESS compiler…),
  start each CLI entry point with no arguments. Pass the script to `gate.py` with `--extra`.
- **Deployment-time effects**: compiled template caches, opcode caches, absolute paths written into
  generated files. Reproduce a deployment over the previous version when you can (see
  `frontend-assets.md`, compiled template caches).
