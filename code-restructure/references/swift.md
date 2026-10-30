# Swift / Xcode (macOS, iOS, SwiftPM)

## How files and modules resolve
- **SwiftPM**: a target is a directory (`path:`, default `Sources/<name>`); every `.swift` file under it (minus `exclude:`) is compiled into that module. There are no per-file imports inside a module, so moving a file *within* a target changes nothing for the compiler; moving it *between* targets changes module boundaries and requires `public` access control on anything now crossing them.
- **Xcode projects (`.xcodeproj`)**: `project.pbxproj` lists every source file, resource and folder reference explicitly, each with a path relative to a group. A file moved on disk but not in the project is simply missing from the build (or a red "file not found" entry). Projects using synchronized folders (Xcode 16 "folder references / file system synchronized groups") follow the disk; older projects do not. Check which one the project uses before planning.
- Imports are by module name (`import MouseTrapCore`), never by path, so Swift code itself rarely breaks on a move — the build configuration does.

## Files that pin paths — check every one
| File | What it pins |
|---|---|
| `Package.swift` | `path:`, `sources:`, `exclude:` (lists file names — a renamed/moved excluded file silently becomes part of the target and may fail to compile on Linux or in tests), `resources:`, `testTarget path:` |
| `*.xcodeproj/project.pbxproj` | file references and groups, build phases (Sources, Resources, Copy Files), `INFOPLIST_FILE`, `CODE_SIGN_ENTITLEMENTS`, `ASSETCATALOG_COMPILER_APPICON_NAME`, `SWIFT_OBJC_BRIDGING_HEADER`, `PRODUCT_NAME`, test host paths |
| `*.xcscheme` (xcshareddata/xcschemes) | referenced targets by identifier; test plans (`.xctestplan`) paths |
| `*.xcworkspace/contents.xcworkspacedata` | project locations |
| `Info.plist`, `*.entitlements`, `PrivacyInfo.xcprivacy` | referenced by build settings; moving them requires editing pbxproj build settings |
| `Resources/*.lproj/` | localization lookup is by folder name (`en.lproj`, `fr.lproj`) and file name (`Localizable.strings`); `NSLocalizedString` finds them via the bundle, so keep the `.lproj` structure intact |
| `Assets.xcassets` | referenced by name from code (`NSImage(named:)`) and by `ASSETCATALOG_*` settings |
| `.github/workflows/*.yml`, `Scripts/*.sh`, `*.command` | `xcodebuild -project … -scheme …`, `swift test`, archive/export paths, derived data paths |
| `.swiftlint.yml`, `.swiftformat` | `included:` / `excluded:` paths |
| `Documentation/`, README | paths and file names in prose |

## Runtime references to grep for
```
Bundle.main.path(forResource:   Bundle.module   NSImage(named:   NSLocalizedString(
Bundle(for:     #file   #filePath   URL(fileURLWithPath:   FileManager.default.currentDirectoryPath
"…entitlements"   "Info.plist"   NSUserDefaults suite names / app group ids (identifier strings, not paths)
```
Also grep shell scripts for the project/scheme/target names and for `MouseTrap/` style prefixes.

## Idiomatic layouts
- **Small app** (≈ 20–40 files): flat folder per concern is fine — `App/`, `Core/` (logic with no AppKit/UIKit imports, unit-testable), `UI/` or per-feature folders, `Resources/`, `Tests/`. Do not create `Domain/Data/Presentation` layers for this size.
- **Larger app**: feature folders (`Features/<Feature>/{View,ViewModel,Model}`), `Shared/`, `Services/`; extract reusable logic to a local Swift package once two targets need it.
- **Library**: standard SwiftPM `Sources/<Target>/`, `Tests/<Target>Tests/`.
- Keep `Info.plist`, entitlements, privacy manifest and asset catalogs next to the app target's resources; their *content* is what the App Store review tooling checks.

## Tooling
- No scriptable rename tool updates `project.pbxproj` reliably; options: edit in Xcode (drag files, "Show in Finder" consistency), use **xcodeproj** (Ruby gem, `CocoaPods/Xcodeproj`) or **Tuist**/**XcodeGen** if the project is generated from a spec, or edit the pbxproj text by hand for a handful of moves (it is a plist; each file has a `PBXFileReference` and a `PBXBuildFile` entry plus a group child entry). For synchronized-folder projects `git mv` is enough.
- `swift build`, `swift test` for the SwiftPM part; `xcodebuild -list`, `xcodebuild -project X.xcodeproj -scheme S -configuration Debug build CODE_SIGNING_ALLOWED=NO` for the app; `xcodebuild test` for the app tests.
- **These require macOS.** On Linux only the portable SwiftPM subset can be built (and only if the Swift toolchain is installed and the target doesn't import AppKit/UIKit). In a Linux/cloud session, the verification floor is: `check_moves.py` for stale references, a pbxproj consistency check (every referenced path exists, every `.swift` on disk under the app folder is referenced — a small script is enough), and a clear statement that the build was not run. The CI job on a macOS runner is the real verification; recommend pushing the branch and reading the workflow result before merging.

## Traps
- A file present on disk but absent from `project.pbxproj` compiles in SwiftPM (directory-based) and not in Xcode: the two build systems can disagree after a move. Check both.
- `Package.swift` `exclude:` entries are matched relative to the target `path:`; moving an excluded UI file into a subfolder without updating the entry pulls AppKit code into the portable target.
- Moving `Info.plist` or the entitlements file requires updating the build settings in every configuration (Debug and Release) — grep `pbxproj` for all occurrences.
- Localization: renaming `fr.lproj` or moving only one language's strings file leaves silent fallbacks to English.
- Code signing, sandbox and Input-Monitoring permissions are tied to the bundle identifier and entitlements, not to paths — safe to move, unsafe to rename the product.
