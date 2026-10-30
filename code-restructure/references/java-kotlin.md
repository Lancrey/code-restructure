# Java / Kotlin (JVM)

## How packages resolve
- A class's fully-qualified name is `<package>.<Class>`, and the compiler expects the file at `<source root>/<package path>/<Class>.java`. Moving a file without changing its `package` declaration is an error (Java) or a warning-then-confusion (Kotlin allows mismatched package/directory, which produces organically-grown messes). Changing the package changes the FQN, so every `import`, every reflective reference and every config that names the class must follow.
- Source roots are declared by the build (`src/main/java`, `src/main/kotlin`, `src/test/…`, plus any custom `sourceSets`/`<sourceDirectory>`). Moving code to a directory that is not a source root silently drops it from compilation — check `./gradlew compileJava` picks it up.
- Multi-module builds (Gradle subprojects, Maven modules): a module is a dependency boundary; moving classes between modules means editing `build.gradle(.kts)`/`pom.xml` dependencies and watching for cycles (both tools reject them).

## Files that pin paths/FQNs — check every one
| File | Keys |
|---|---|
| `settings.gradle(.kts)` | `include(":module")`, `project(":x").projectDir` |
| `build.gradle(.kts)` / `pom.xml` | `sourceSets`, `mainClass`/`<mainClass>`, `application { mainClass }`, `jar { manifest }`, shadow/assembly config, `<module>` lists, `<sourceDirectory>`, annotation processor paths |
| `MANIFEST.MF` | `Main-Class` |
| `src/main/resources/META-INF/services/*` | **ServiceLoader**: file name = interface FQN, lines = implementation FQNs |
| `META-INF/spring.factories`, `META-INF/spring/*.imports` | Spring Boot auto-config FQNs |
| `application.yml/properties` | `spring.main.sources`, class names in config, `mybatis.type-aliases-package`, `entity-scan` |
| `persistence.xml`, `hibernate.cfg.xml`, `orm.xml` | `<class>` FQNs |
| `logback.xml`, `log4j2.xml` | `<logger name="com.acme.module">` — package-based, silently stops matching after a move |
| `web.xml`, `struts.xml`, `beans.xml`, Spring XML | servlet/bean class FQNs |
| `module-info.java` | `exports`, `opens`, `provides … with …` |
| `proguard-rules.pro`, `consumer-rules.pro` (Android) | `-keep class com.acme.**` |
| `AndroidManifest.xml` | activity/service class names, `package` |
| Dockerfile / CI | `java -cp … com.acme.Main`, module paths in `./gradlew :module:test` |

## Runtime references to grep for
```
Class.forName(       .class.getName()     getResourceAsStream("     getResource("
@ComponentScan(      scanBasePackages     @EntityScan(      @EnableJpaRepositories(basePackages
"com.acme.           Package.getPackage   Reflections(      ServiceLoader.load(
```
**Spring component scanning** is the big one: `@SpringBootApplication` scans the package of the annotated class *and below*. Moving a component to a sibling package outside that root makes it vanish from the context with no compile error. Moving the application class changes the root. Logger configuration keyed by package name degrades silently too.

## Idiomatic layouts

**Package by feature** (recommended by most current guidance over package-by-layer):
```
com.acme.app
  Application
  order/        OrderController, OrderService, OrderRepository, Order
  customer/
  shared/  or  common/   # cross-cutting: config, security, errors
  infrastructure/        # external adapters, if the project separates them
```
Package-by-layer (`controller/`, `service/`, `repository/`, `model/`) is what organically-grown Spring apps usually have; converting it is a legitimate goal but every class changes package, so plan for a large but mechanical diff and a clean component-scan root.

**Multi-module Gradle/Maven**
```
settings.gradle.kts
app/           # the runnable assembly
core/ or domain/
<feature>/ …
buildSrc/ or gradle/ (convention plugins)
```
Split by build-time or ownership boundaries, not by whim; each module adds configuration overhead.

**Android** — `app/` plus feature and library modules; the manifest merger and R class generation are module-scoped. Moving resources (`res/`) between modules changes `R` references.

## Tooling
- **IntelliJ IDEA / Android Studio** "Move class/package" and "Rename package" refactors update imports, FQN strings in Spring/XML configs it recognises, and tests. This is the safest tool for JVM moves; if the user can run it interactively, recommend it over scripting.
- **OpenRewrite** (`org.openrewrite.java.ChangePackage`, `ChangeType`) is the scriptable equivalent: a recipe applied via Maven/Gradle plugin rewrites packages across the whole repo, including string references in known config formats.
- **jdeps** (`jdeps -verbose:class -R build/classes`) for the dependency graph; **ArchUnit** tests to assert the new boundaries (e.g. `noClasses().that().resideInAPackage("..order..").should().dependOnClassesThat().resideInAPackage("..customer..")`) — worth proposing as a permanent guard.
- **Verification**: full build (`./gradlew build` / `mvn verify`), run the application context in tests (`@SpringBootTest` boots and fails on missing beans — the test that catches component-scan regressions), `./gradlew test --rerun-tasks` to defeat the build cache, compare test counts from the reports (`build/test-results`), `jar tf` on the built artifact to confirm resources moved, `ktlint`/`detekt`/`checkstyle` if present.

## Traps
- Kotlin allows package ≠ directory; organically grown code often has it. Decide early whether to fix the mismatch (a real move) or leave it.
- Tests in `src/test/java/<same package>` use package-private access to the class under test; moving the class without moving the test breaks compilation.
- Serialization (`Serializable` with `serialVersionUID`, Jackson default typing, Java serialization in caches/sessions, Kryo) stores FQNs; moving an entity class can invalidate persisted data.
- JPA entity names default to the simple class name but `@Table`/`@Entity(name)` may be absent; the package move is fine, a class rename is not.
- Lombok/annotation-processor config files (`lombok.config`) are directory-scoped.
- Gradle build cache and IDE caches hide broken moves; rebuild from clean (`./gradlew clean build`) for the final verification.
