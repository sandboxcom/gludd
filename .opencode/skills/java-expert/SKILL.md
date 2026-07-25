---
name: java-expert
description: Use when writing, debugging, reviewing, or discussing Java, Kotlin, Groovy, Scala, Clojure, J2ME, or any JVM-language code. Covers Maven, Gradle, JAR/WAR/EAR packaging, JVM internals (class format, bytecode, JIT, GC), Spring Boot, concurrency, security patterns, and XML structures. Trigger keywords: Java, JVM, Maven, Gradle, Kotlin, Groovy, Scala, JAR, WAR, EAR, Spring Boot, J2EE, Jakarta, JDBC, JPA, Hibernate, JUnit, Mockito, bytecode, classloader, GC, G1, ZGC, GraalVM, J2ME, MIDlet, XML, POM, JAXB, DOM, SAX.
---

# Java / JVM Expert

The JVM is a stack of layers: a language compiler produces `.class` files, the
class loader assembles them into a runtime namespace, the bytecode interpreter /
JIT compiles them to native code, and the garbage collector reclaims dead
objects. This skill covers every layer and every language that targets the JVM.

## Java Language

### JVM Architecture

The JVM specification defines the class file format, the instruction set, and
the startup lifecycle. Understanding the class loader hierarchy is the
prerequisite for every debugging session that involves `ClassNotFoundException`
or `NoClassDefFoundError`.

```
Bootstrap ClassLoader (loads rt.jar / java.base, written in C++)
    |
    v
Platform ClassLoader (java.desktop, java.sql, java.xml — JDK 9+)
    |              (replaces the old Extension ClassLoader from JDK 8)
    v
Application ClassLoader (your code on --class-path / -cp)
```

Each loader delegates **upward** first (the parent is asked before the child),
then searches its own path. Two classes with the same fully-qualified name but
loaded by different loaders are distinct types to the JVM. This is the root cause
of `ClassCastException: Foo cannot be cast to Foo` — two class loaders loaded
two copies of `Foo.class`.

The linking step (after loading, before initialization) verifies bytecode,
prepares static fields with default values, and optionally resolves symbolic
references (the JVM spec allows lazy resolution). Preparation sets fields to
their typed zero: `0`, `0L`, `0.0f`, `null`, `false`. Static initializer blocks
and static field initializers run during **initialization**, which happens on
the first active use (first `new`, first static field access, first static method
call on a non-constant field).

```java
public class ClassLifecycle {
    static int x = initX();          // runs during initialization
    static final int C = 42;         // compile-time constant, no init

    static int initX() {
        System.out.println("Initializing ClassLifecycle");
        return 1;
    }
}
// Accessing ClassLifecycle.C does NOT trigger initialization — it's a constant.
// Accessing ClassLifecycle.x DOES trigger initialization (and prints the line).
```

### Type System

The JVM type system splits into primitives and references. This split is
fundamental: primitives live on the stack (or are embedded in objects), while
references point to heap objects. The split causes both boxing overhead and
generic-erasure surprise.

**Primitives vs. boxed:**

| Primitive | Wrapper | Size | Default | Cache range |
|-----------|---------|------|---------|-------------|
| `boolean` | `Boolean` | not defined | `false` | `Boolean.TRUE` / `Boolean.FALSE` |
| `byte` | `Byte` | 8 bits | `0` | -128..127 |
| `short` | `Short` | 16 bits | `0` | -128..127 |
| `char` | `Character` | 16 bits (unsigned) | `'\u0000'` | 0..127 |
| `int` | `Integer` | 32 bits | `0` | -128..127 (default, -XX:AutoBoxCacheMax=N) |
| `long` | `Long` | 64 bits | `0L` | -128..127 |
| `float` | `Float` | 32 bits (IEEE 754) | `0.0f` | none |
| `double` | `Double` | 64 bits (IEEE 754) | `0.0d` | none |

```java
Integer a = 127;
Integer b = 127;
assert a == b;                    // true — cached in IntegerCache

Integer c = 128;
Integer d = 128;
assert c != d;                    // true — exceeded cache range, new objects
assert c.equals(d);               // true — .equals compares value

// The autoboxing pit:
Integer e = null;
int f = e;                        // NullPointerException at unbox time
```

**Generics and erasure.** The Java compiler (javac) compiles generics by
erasing type parameters — `List<String>` and `List<Integer>` are the same
`List` at runtime. The compiler inserts casts at the boundary where a generic
value is read from a parameterized container. This has practical consequences:

```java
// At runtime, both are just List:
List<String> strings = new ArrayList<>();
List<Integer> integers = new ArrayList<>();
assert strings.getClass() == integers.getClass();  // true — same ArrayList.class

// Erasure means you cannot:
// - Overload on generic parameter alone: void m(List<String>) vs void m(List<Integer>)
// - Use instanceof on parameterized type: x instanceof List<String>   (compile error)
// - Create arrays of parameterized types: new List<String>[10]        (compile error)
// - Catch generic exceptions: catch (MyException<T>)                  (compile error)
// Reified generics exist ONLY for unbounded wildcards:
List<?> list = new ArrayList<String>();  // wildcard capture works
Object[] array = list.toArray();          // but toArray(T[]) with reflection
```

**Wildcards: PECS (Producer Extends, Consumer Super).** Mnemonic by Joshua Bloch:

```java
// Producer — you read FROM the structure, so it must produce a known type.
// Use extends (upper-bounded):
void copy(List<? extends Number> source, List<? super Number> dest) {
    for (Number n : source) {      // source PRODUCES Number (or subtype cast)
        dest.add(n);               // dest CONSUMES Number
    }
}

List<Integer> ints = List.of(1, 2, 3);
List<Number> nums = new ArrayList<>();
copy(ints, nums);                  // ? extends Number accepts Integer

// Consumer — you write INTO the structure, so it must accept a known type.
// Use super (lower-bounded):
List<Object> objects = new ArrayList<>();
copy(ints, objects);               // ? super Number accepts Object
```

**Type inference (Java 10+ `var`).** The `var` keyword infers the type from the
initializer — it does NOT make Java dynamically typed. The type is fixed at
compile time and cannot change. `var` is legal only for local variables with
initializers.

```java
var list = new ArrayList<String>();    // ArrayList<String>
var stream = list.stream();            // Stream<String>
var entry = Map.entry("k", 1);         // Map.Entry<String, Integer>

// var can hide types, making code harder to read — use when the RHS makes
// the type obvious:
var user = userService.findById(id);   // User — good: method name tells you

// Forbidden: no initializer, null initializer, lambda target, array initializer
// var x;                      // compile error
// var x = null;               // compile error
// var f = s -> s.length();   // compile error — lambda needs target type
// var a = {1, 2, 3};         // compile error — array initializer
```

**Sealed classes (Java 17).** A sealed class restricts which classes may extend
it. Every permitted subclass must be `final`, `sealed`, or `non-sealed`:

```java
public sealed class Shape
    permits Circle, Rectangle, Triangle { }

public final class Circle extends Shape {
    private final double radius;
    public Circle(double radius) { this.radius = radius; }
    public double radius() { return radius; }
}

public non-sealed class Rectangle extends Shape {
    private final double width, height;
    public Rectangle(double width, double height) {
        this.width = width; this.height = height;
    }
}

public final class Triangle extends Shape {
    private final double a, b, c;
    public Triangle(double a, double b, double c) { this.a = a; this.b = b; this.c = c; }
}

// Exhaustive switch with pattern matching (Java 21):
double area(Shape s) {
    return switch (s) {
        case Circle c    -> Math.PI * c.radius() * c.radius();
        case Rectangle r -> r.width() * r.height();
        case Triangle t  -> {
            double semi = (t.a() + t.b() + t.c()) / 2.0;
            yield Math.sqrt(semi * (semi - t.a()) * (semi - t.b()) * (semi - t.c()));
        }
        // No default needed — compiler knows switch is exhaustive over sealed permits
    };
}
```

**Records (Java 14 preview, 16 stable).** A record is a transparent carrier for
immutable data. The compiler generates the constructor, accessors, `equals`,
`hashCode`, and `toString`. Records are implicitly `final`.

```java
public record Point(int x, int y) {
    // Compact constructor — validates inputs before field assignment:
    public Point {
        if (x < 0 || y < 0) {
            throw new IllegalArgumentException("Coordinates must be non-negative");
        }
    }
    // Derived accessor — can override generated accessor:
    public double r() {
        return Math.sqrt(x * x + y * y);
    }
    // Static factory:
    public static Point origin() {
        return new Point(0, 0);
    }
}

// Pattern matching on record:
if (p instanceof Point(int x, int y) && x > 0) {
    System.out.printf("Point at (%d,%d)%n", x, y);
}
```

**Pattern matching.** `instanceof` pattern matching (Java 16) binds a variable
in the same expression, avoiding the cast-after-check boilerplate:

```java
// Old (Java 15-):
if (obj instanceof String) {
    String s = (String) obj;
    System.out.println(s.length());
}
// New (Java 16+):
if (obj instanceof String s && s.length() > 5) {
    System.out.println(s);  // s in scope, already cast
}

// Switch pattern matching (Java 21):
Object obj = // ...
String result = switch (obj) {
    case null           -> "null";
    case Integer i      -> "int: " + i;
    case String s       -> "string length " + s.length();
    case Long l && l > 0 -> "positive long: " + l;  // guarded pattern
    default             -> "unknown type";
};
```

**Text blocks (Java 15).** Multi-line string literals delimited by `"""`.
Leading whitespace is determined by the common leading whitespace of the
content lines. Trailing whitespace is stripped from each line.

```java
String json = """
    {
        "name": "Alice",
        "age": 30,
        "address": {
            "street": "123 Main St",
            "city": "Springfield"
        }
    }
    """;
// Equivalent to: "{\n    \"name\": \"Alice\",\n    ... }\n"

// Trailing backslash suppresses newline:
String single = """
    line 1\
    still line 1""";
// Result: "line 1still line 1"
```

**Try-with-resources (Java 7+, enhanced Java 9+).** Any class implementing
`AutoCloseable` can be used. Resources are closed in reverse order of
declaration. Suppressed exceptions are available via `getSuppressed()`.

```java
// Java 9+ — effectively-final variables can be used:
Connection conn = dataSource.getConnection();
PreparedStatement ps = conn.prepareStatement("SELECT ...");
try (conn; ps) {
    try (ResultSet rs = ps.executeQuery()) {
        while (rs.next()) { /* process */ }
    }
}
// close order: rs -> ps -> conn (reverse of declaration/appearance)
```

**Annotations.** Annotations are metadata that the compiler or runtime
processes. Retention policy determines lifespan:

| Retention | Survives | Typical use |
|-----------|----------|-------------|
| `SOURCE` | Discarded by compiler | `@Override`, `@SuppressWarnings` |
| `CLASS` | Recorded in `.class`, not at runtime | Lombok `@Data`, compile-time code gen |
| `RUNTIME` | Available via reflection at runtime | `@Entity`, `@Test`, `@Autowired` |

```java
@Retention(RetentionPolicy.RUNTIME)
@Target({ElementType.TYPE, ElementType.METHOD})
public @interface Audited {
    String value() default "";
    Severity severity() default Severity.INFO;
    enum Severity { INFO, WARN, ERROR }
}

// Annotation processor — runs during compilation, generates source/class files:
@SupportedAnnotationTypes("com.example.Audited")
@SupportedSourceVersion(SourceVersion.RELEASE_17)
public class AuditProcessor extends AbstractProcessor {
    @Override
    public boolean process(Set<? extends TypeElement> annotations,
                           RoundEnvironment roundEnv) {
        for (Element e : roundEnv.getElementsAnnotatedWith(Audited.class)) {
            Audited a = e.getAnnotation(Audited.class);
            // Generate audit logging code or report ...
        }
        return true;
    }
}
// Register in META-INF/services/javax.annotation.processing.Processor
```

### JPMS: The Java Platform Module System (Java 9+)

Modules partition the JDK and your application into explicit, named units with
declared dependencies. A module is defined by `module-info.java` at the source
root.

```java
// module-info.java in src/main/java/
module com.example.app {
    requires java.sql;
    requires transitive com.example.api;  // re-export — consumers also get api
    requires static com.example.optional; // compile-time only (optional at runtime)

    exports com.example.app.api;
    exports com.example.app.dto to       // qualified export — only named modules
        com.example.client,
        com.example.audit;

    opens com.example.app.model to       // deep reflection access by hibernate
        org.hibernate.orm.core;

    uses com.example.spi.Plugin;         // I consume implementations
    provides com.example.spi.Plugin      // I provide this implementation
        with com.example.impl.MyPlugin;
}
```

Command-line `--add-opens` and `--add-exports` are escape hatches for
libraries that have not yet modularized. Encapsulation violations (illegal
reflective access) became hard errors in Java 17.

```
java --add-opens java.base/java.lang=com.example.lib
     --add-exports java.base/sun.security.x509=com.example.lib
     -p modules/ -m com.example.app/com.example.Main
```

Classpath and module path are mutually exclusive at launch. The module path
(`-p` or `--module-path`) enables the module system; the classpath (`-cp` or
`--class-path`) disables it (everything lands in the unnamed module). The
unnamed module reads every observable module but exports nothing — a one-way
bridge.

---

## Common Patterns

### Builder (Inner Class, Fluent)

```java
public final class HttpRequest {
    private final String method, url;
    private final Map<String, String> headers;
    private final String body;
    private final int timeoutMs;

    private HttpRequest(Builder b) {
        this.method = Objects.requireNonNull(b.method, "method");
        this.url = Objects.requireNonNull(b.url, "url");
        this.headers = Map.copyOf(b.headers);
        this.body = b.body;
        this.timeoutMs = b.timeoutMs > 0 ? b.timeoutMs : 5000;
    }

    public static Builder newBuilder(String method, String url) {
        return new Builder(method, url);
    }

    public static final class Builder {
        private final String method, url;
        private final Map<String, String> headers = new LinkedHashMap<>();
        private String body;
        private int timeoutMs;

        private Builder(String method, String url) {
            this.method = method;
            this.url = url;
        }
        public Builder header(String name, String value) {
            this.headers.put(name, value);
            return this;
        }
        public Builder body(String body) {
            this.body = body;
            return this;
        }
        public Builder timeout(int ms) {
            this.timeoutMs = ms;
            return this;
        }
        public HttpRequest build() {
            return new HttpRequest(this);
        }
    }
}
// Usage: HttpRequest.newBuilder("POST", "/users").header("Auth","Bearer x").build();
```

### Singleton

Three correct approaches, each with different tradeoffs. The enum approach is
the most concise and serialization-safe.

```java
// 1. Enum-based — concise, serialization-safe, thread-safe, reflection-proof:
public enum Config {
    INSTANCE;
    private final Properties props = new Properties();
    Config() { /* load from classpath on first access */ }
    public String get(String key) { return props.getProperty(key); }
}
// Usage: Config.INSTANCE.get("db.url")
// Enum singletons survive serialization (no readResolve needed) and
// reflection attacks (Constructor.newInstance() on enums throws).

// 2. Double-checked locking — lazy init, volatile for safe publication:
public class DatabasePool {
    private static volatile DatabasePool instance;
    private DatabasePool() {
        if (instance != null) throw new IllegalStateException("Already created");
    }
    public static DatabasePool getInstance() {
        DatabasePool result = instance;
        if (result == null) {
            synchronized (DatabasePool.class) {
                result = instance;
                if (result == null) {
                    instance = result = new DatabasePool();
                }
            }
        }
        return result;
    }
}
// The volatile is ESSENTIAL — without it, a thread could see a partially
// constructed object. The local variable 'result' avoids a second volatile read.

// 3. Bill Pugh holder — lazy, no sync after class load:
public class Scheduler {
    private Scheduler() {}
    private static class Holder {
        static final Scheduler INSTANCE = new Scheduler();
    }
    public static Scheduler getInstance() {
        return Holder.INSTANCE;  // triggers Holder class init, which is lazy and thread-safe
    }
}
```

### Factory (Static Factory, Supplier)

```java
// Static factory methods — can return subtypes, cache instances, have descriptive names:
public interface Parser {
    Document parse(InputStream in);
    static Parser newJsonParser()     { return new JsonParserImpl(); }
    static Parser newXmlParser()      { return new XmlParserImpl(); }
    static Parser newCaching(Parser p) { return new CachingParser(p); }
}

// Supplier-based factory — defer creation, useful for lazy init and DI:
public class ServiceRegistry {
    private final Map<Class<?>, Supplier<?>> factories = new ConcurrentHashMap<>();

    public <T> void register(Class<T> type, Supplier<? extends T> factory) {
        factories.put(type, factory);
    }
    @SuppressWarnings("unchecked")
    public <T> T get(Class<T> type) {
        Supplier<? extends T> factory = (Supplier<? extends T>) factories.get(type);
        if (factory == null) throw new IllegalArgumentException("No factory for " + type);
        return factory.get();
    }
}
```

### Dependency Injection (Constructor, Field, Setter)

```java
// Constructor injection — the canonical, testable approach:
@Service
public class OrderService {
    private final OrderRepository repository;
    private final PaymentGateway gateway;
    private final NotificationService notifier;

    // Single constructor — Spring 4.3+ auto-wires without @Autowired:
    public OrderService(OrderRepository repository,
                        PaymentGateway gateway,
                        NotificationService notifier) {
        this.repository = repository;
        this.gateway = gateway;
        this.notifier = notifier;
    }
}

// For optional dependencies, use Optional — never null:
public class ReportGenerator {
    private final Optional<MetricsCollector> metrics;
    public ReportGenerator(Optional<MetricsCollector> metrics) {
        this.metrics = metrics;
    }
}
```

### Strategy and Observer

```java
// Strategy — encapsulate interchangeable algorithms:
@FunctionalInterface
public interface DiscountStrategy {
    BigDecimal apply(BigDecimal price, Customer customer);
}
public class PricingService {
    private final Map<CustomerTier, DiscountStrategy> strategies = Map.of(
        CustomerTier.REGULAR, (p, c) -> p,
        CustomerTier.SILVER,  (p, c) -> p.multiply(new BigDecimal("0.95")),
        CustomerTier.GOLD,    (p, c) -> p.multiply(new BigDecimal("0.90"))
    );
    public BigDecimal calculatePrice(Product product, Customer customer) {
        return strategies.get(customer.tier()).apply(product.basePrice(), customer);
    }
}

// Observer — simplified with java.beans.PropertyChangeSupport:
public class Configuration {
    private final PropertyChangeSupport pcs = new PropertyChangeSupport(this);
    private String databaseUrl;
    public void addListener(PropertyChangeListener l) { pcs.addPropertyChangeListener(l); }
    public void setDatabaseUrl(String newUrl) {
        String old = this.databaseUrl;
        this.databaseUrl = newUrl;
        pcs.firePropertyChange("databaseUrl", old, newUrl);
    }
}
```

### Decorator

```java
// Wrap a component to add behavior without modifying it:
public interface DataSource {
    byte[] read(String key);
    void write(String key, byte[] data);
}
public final class LoggingDataSource implements DataSource {
    private final DataSource delegate;
    public LoggingDataSource(DataSource delegate) { this.delegate = delegate; }
    @Override
    public byte[] read(String key) {
        long start = System.nanoTime();
        byte[] result = delegate.read(key);
        log.info("read key={} took={}us size={}", key, (System.nanoTime()-start)/1000, result.length);
        return result;
    }
    @Override
    public void write(String key, byte[] data) {
        log.info("write key={} size={}", key, data.length);
        delegate.write(key, data);
    }
}
```

### Stream API

The Stream API operates in three phases: a source produces a stream, zero or
more intermediate operations transform it (lazily), and a terminal operation
consumes it. Streams are single-use — consuming one drains it.

```java
// Core pipeline pattern:
List<String> result = items.stream()              // source
    .filter(x -> x.isActive())                    // intermediate (lazy)
    .map(Item::name)                              // intermediate (lazy)
    .sorted()                                     // intermediate (stateful, lazy)
    .distinct()                                   // intermediate (stateful, lazy)
    .collect(Collectors.toList());                // terminal (eager)

// Collectors:
Map<Department, List<Employee>> byDept = employees.stream()
    .collect(Collectors.groupingBy(Employee::department));

Map<Boolean, List<Transaction>> partitioned = transactions.stream()
    .collect(Collectors.partitioningBy(t -> t.amount().signum() >= 0));

// flatMap — one-to-many flattening:
List<String> allTags = articles.stream()
    .flatMap(a -> a.tags().stream())
    .distinct()
    .collect(Collectors.toList());

// reduce — fold left:
int total = IntStream.rangeClosed(1, 100).reduce(0, Integer::sum);

// Parallel streams — appropriate when source is splittable (ArrayList, arrays),
// not when linked (LinkedList). N > ~10,000 for benefit:
long count = largeList.parallelStream()
    .filter(x -> expensivePredicate(x))
    .count();
// ForkJoinPool.commonPool() is the default; customize parallelism via
// -Djava.util.concurrent.ForkJoinPool.common.parallelism=N
```

### Optional vs null

`Optional` is designed as a method-return type. It was NOT designed as a field
type, a constructor parameter, or a collection element.

```java
// orElse vs orElseGet — the critical distinction:
String name = userRepository.findByName("alice")
    .map(User::displayName)
    .orElse("Unknown");                             // "Unknown" is ALWAYS computed
String name = userRepository.findByName("alice")
    .map(User::displayName)
    .orElseGet(() -> expensiveDefaultCompute());    // only computed if absent

// orElseThrow — explicit exception type, preferred over .get():
User user = userRepository.findById(id)
    .orElseThrow(() -> new NotFoundException("User not found: " + id));

// flatMap — chain Optionals without nesting:
Optional<Address> addr = userRepo.findById(userId)
    .flatMap(u -> accountRepo.findByUserId(u.id()))
    .flatMap(a -> Optional.ofNullable(a.address()));
String zip = addr.map(Address::zip).orElse("00000");

// ifPresentOrElse (Java 9+):
userRepository.findById(id).ifPresentOrElse(
    user -> emailService.sendWelcome(user.email()),
    () -> log.warn("User not found: {}", id)
);

// stream() on Optional (Java 9+):
Set<String> roles = userRepository.findById(id)
    .stream()
    .flatMap(u -> u.roles().stream())
    .collect(Collectors.toSet());
```

### Thread-Safe Patterns

```java
// ConcurrentHashMap — computeIfAbsent for atomic lazy init:
Map<String, CacheEntry> cache = new ConcurrentHashMap<>();
CacheEntry entry = cache.computeIfAbsent(key, k -> expensiveLoad(k));
// computeIfAbsent is atomic: only one thread runs expensiveLoad per unique key.

// CopyOnWriteArrayList — read-heavy, write-rare:
CopyOnWriteArrayList<EventListener> listeners = new CopyOnWriteArrayList<>();
void fireEvent(Event e) {
    for (EventListener l : listeners) { l.onEvent(e); }  // no lock, safe iteration
}

// AtomicInteger — lock-free counter:
AtomicInteger sequence = new AtomicInteger();
int next = sequence.incrementAndGet();

// volatile — visibility, not atomicity:
volatile boolean shutdown = false;
// Writing to shutdown happens-before any subsequent read.
// It does NOT make compound operations (check-then-act) atomic.

// Compound atomic operations need CAS:
AtomicReference<State> stateRef = new AtomicReference<>(State.INIT);
boolean updated = stateRef.compareAndSet(State.INIT, State.RUNNING);
```

---

## Build & Packaging

### Maven

Maven's model is declarative: you describe the project (POM) and the lifecycle
phases do the work. The POM inherits from a parent, either explicit or the
implicit Super POM from the Maven distribution.

**POM structure:**

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
            http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>my-app</artifactId>
    <version>1.0.0-SNAPSHOT</version>
    <packaging>jar</packaging>
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.2.0</version>
    </parent>
    <properties>
        <java.version>21</java.version>
    </properties>
    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>
        <dependency>
            <groupId>com.h2database</groupId>
            <artifactId>h2</artifactId>
            <scope>runtime</scope>
        </dependency>
    </dependencies>
    <dependencyManagement>
        <dependencies>
            <dependency>
                <groupId>io.grpc</groupId>
                <artifactId>grpc-bom</artifactId>
                <version>1.59.0</version>
                <type>pom</type>
                <scope>import</scope>
            </dependency>
        </dependencies>
    </dependencyManagement>
</project>
```

**Maven lifecycle phases.** The default lifecycle has 23 phases; the key ones:

```
validate → compile → test → package → verify → install → deploy
```

Each phase invokes all preceding phases. Plugins bind goals to phases — e.g.
`maven-compiler-plugin:compile` binds to `compile`, `maven-surefire-plugin:test`
binds to `test`.

**Dependency scopes:**

| Scope | Compile CP | Test CP | Runtime CP | Packaged? |
|-------|-----------|---------|------------|-----------|
| `compile` (default) | yes | yes | yes | yes |
| `provided` | yes | yes | no | no (container provides, e.g. servlet-api) |
| `runtime` | no | yes | yes | yes (e.g. JDBC driver) |
| `test` | no | yes | no | no |
| `system` | yes | yes | no | no (explicit path, non-portable — avoid) |
| `import` | -- | -- | -- | BOM only |

**Multi-module.** The parent POM has `<packaging>pom</packaging>` and declares
`<modules>`. Children inherit parent's `<groupId>` and `<version>`:

```xml
<!-- parent pom.xml -->
<groupId>com.example</groupId>
<artifactId>my-project</artifactId>
<version>1.0-SNAPSHOT</version>
<packaging>pom</packaging>
<modules>
    <module>my-project-api</module>
    <module>my-project-impl</module>
    <module>my-project-web</module>
</modules>

<!-- my-project-api/pom.xml -->
<parent>
    <groupId>com.example</groupId>
    <artifactId>my-project</artifactId>
    <version>1.0-SNAPSHOT</version>
</parent>
<artifactId>my-project-api</artifactId>
```

### Gradle (Kotlin DSL)

Gradle's model is imperative: you script the build in Groovy or Kotlin. The
Kotlin DSL (`build.gradle.kts`) is the modern default.

```kotlin
// settings.gradle.kts — defines project name and included subprojects:
rootProject.name = "my-app"
include("api", "impl", "web")

// build.gradle.kts (root):
plugins {
    java
    id("org.springframework.boot") version "3.2.0"
    id("io.spring.dependency-management") version "1.1.4"
}
allprojects {
    group = "com.example"
    version = "1.0.0-SNAPSHOT"
    repositories { mavenCentral() }
}
subprojects {
    apply(plugin = "java")
    java { toolchain { languageVersion.set(JavaLanguageVersion.of(21)) } }
    tasks.withType<Test> { useJUnitPlatform() }
}

// build.gradle.kts (subproject — impl):
plugins { `java-library` }
dependencies {
    api(project(":api"))                // exported to consumers
    implementation("com.google.guava:guava:33.0.0-jre")
    compileOnly("org.projectlombok:lombok:1.18.30")
    annotationProcessor("org.projectlombok:lombok:1.18.30")
    runtimeOnly("com.h2database:h2")
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.1")
    testImplementation("org.assertj:assertj-core:3.25.1")
}

// Version catalog — gradle/libs.versions.toml:
// [versions] spring-boot = "3.2.0"
// [libraries] spring-boot-starter-web = { module = "org.springframework.boot:spring-boot-starter-web", version.ref = "spring-boot" }
// Then in build.gradle.kts: dependencies { implementation(libs.spring.boot.starter.web) }
```

**Dependency configurations:**

| Configuration | Compile CP | Runtime CP | Exported to consumers |
|---------------|-----------|------------|----------------------|
| `api` | yes | yes | yes |
| `implementation` | yes | yes | **no** (faster build, cleaner) |
| `compileOnly` | yes | no | -- |
| `runtimeOnly` | no | yes | -- |
| `testImplementation` | test only | test only | -- |

### JAR, WAR, EAR Packaging

**JAR (Java Archive)** — a ZIP with `META-INF/MANIFEST.MF`. The manifest
declares the main class, classpath, and optional sealing.

```
MANIFEST.MF:
Main-Class: com.example.Main
Class-Path: lib/guava-33.0.0.jar lib/slf4j-api-2.0.9.jar
```

**Fat JAR (uber JAR)** — bundles all dependencies into a single JAR. Spring
Boot's loader nests JARs inside the fat JAR and uses `LaunchedURLClassLoader`.

**WAR (Web Application Archive)** — deployed to a servlet container:

```
myapp.war/
├── META-INF/
│   └── MANIFEST.MF
├── WEB-INF/
│   ├── web.xml
│   ├── classes/
│   └── lib/
├── index.jsp
└── static/
    ├── css/
    └── js/
```

**EAR (Enterprise Application Archive)** — bundles multiple WARs and EJB JARs:

```
myapp.ear/
├── META-INF/
│   ├── application.xml
│   └── MANIFEST.MF
├── myapp-web.war
├── myapp-ejb.jar
└── lib/
```

### JPMS Packaging

`jlink` creates a custom runtime image with only the modules your app needs:

```
jlink --module-path $JAVA_HOME/jmods:target/modules
      --add-modules com.example.app
      --output target/image
      --launcher myapp=com.example.app/com.example.Main
      --compress 2
      --strip-debug
```

`jpackage` (Java 14+) creates platform-native installers:

```
jpackage --name MyApp --input target/image --main-jar myapp.jar
         --main-class com.example.Main --type dmg --app-version 1.0
```

**Classpath vs module path.** On the classpath (`-cp`), everything is in the
unnamed module — no encapsulation, no dependency checking. On the module path
(`-p`), the module system enforces accessibility, detects missing modules at
launch, and prevents split packages.

---

## JVM Languages

### Kotlin

Kotlin compiles to JVM bytecode and is fully interoperable with Java. Its
design eliminates null-pointer exceptions at the type level and adds
coroutines for structured concurrency.

**Null safety.** The type system distinguishes nullable (`T?`) from non-null
(`T`). The safe-call operator (`?.`) and Elvis operator (`?:`) chain null
handling:

```kotlin
data class Address(val street: String, val city: String, val zip: String?)
data class User(val name: String, val address: Address?)

fun formatAddress(user: User?): String {
    return user?.address?.let { addr ->
        "${addr.street}, ${addr.city} ${addr.zip ?: "N/A"}"
    } ?: "No address"
}

// lateinit — promise to initialize before use:
lateinit var repository: UserRepository

// lazy — thread-safe, computed once on first access:
val expensive: Data by lazy { loadFromDisk() }

// !! operator throws NPE if null — use only when certain:
val nonNull: String = possiblyNullString!!
```

**Data classes.** `data class` auto-generates `equals`, `hashCode`, `toString`,
`copy`, and `componentN` for destructuring:

```kotlin
data class Money(val amount: BigDecimal, val currency: String) {
    init { require(amount >= BigDecimal.ZERO) }
}
val price = Money(BigDecimal("19.99"), "USD")
val (amt, curr) = price           // destructuring
val discounted = price.copy(amount = price.amount * BigDecimal("0.9"))
```

**Coroutines.** Structured concurrency with suspend functions, scopes, and
structured job hierarchies:

```kotlin
suspend fun fetchUserAndOrders(userId: Long): UserProfile = coroutineScope {
    val userDeferred = async(Dispatchers.IO) { userService.getById(userId) }
    val ordersDeferred = async(Dispatchers.IO) { orderService.findByUserId(userId) }
    UserProfile(userDeferred.await(), ordersDeferred.await())
}
// coroutineScope fails if any child fails — siblings are cancelled.

// Flow — cold reactive stream:
fun priceUpdates(symbol: String): Flow<BigDecimal> = flow {
    while (isActive) {
        emit(exchange.fetchPrice(symbol))
        delay(1_000)
    }
}.flowOn(Dispatchers.IO)
  .catch { e -> emit(BigDecimal.ZERO) }
  .buffer(Channel.CONFLATED)
```

**Extension functions.** Add methods to existing types without inheritance:

```kotlin
fun String.slugify(): String = this.lowercase()
    .replace(Regex("[^a-z0-9]+"), "-")
    .trim('-')
fun <T> List<T>.secondOrNull(): T? = if (size >= 2) get(1) else null
```

**Reified generics.** `inline` functions with `reified` type parameter keep
the type at runtime:

```kotlin
inline fun <reified T> JsonNode.parseAs(): T =
    objectMapper.treeToValue(this, T::class.java)
```

**Sealed hierarchies.** Sealed classes enable exhaustive `when`:

```kotlin
sealed class Result<out T> {
    data class Success<T>(val value: T) : Result<T>()
    data class Error(val message: String, val cause: Throwable? = null) : Result<Nothing>()
    data object Loading : Result<Nothing>()
}
fun <T> Result<T>.fold(
    onSuccess: (T) -> Unit,
    onError: (String, Throwable?) -> Unit,
    onLoading: () -> Unit
) {
    when (this) {
        is Result.Success -> onSuccess(value)
        is Result.Error   -> onError(message, cause)
        is Result.Loading -> onLoading()
    } // No else needed — when is exhaustive
}
```

**Kotlin Multiplatform (KMP).** Share business logic across JVM, JS, Native:

```kotlin
// commonMain:
expect class PlatformContext { fun basePath(): String }

// jvmMain:
actual class PlatformContext {
    actual fun basePath(): String = System.getProperty("user.dir")
}
```

### Groovy

Groovy is a dynamic JVM language with optional static compilation. It
integrates deeply with Java — Groovy classes extend `java.lang.Object`.

**Dynamic dispatch and MOP (Meta-Object Protocol).** Groovy intercepts every
method call through the MOP:

```groovy
class DynamicRouter {
    def methodMissing(String name, args) {
        println "Called $name with ${args}"
        return "routed to $name"
    }
}
def router = new DynamicRouter()
router.anyMethodCall("arg1", 42)
```

**Closures vs lambdas.** A Groovy closure is a full object with a delegate:

```groovy
def config = {
    host = "localhost"
    port = 8080
}
config.delegate = new ServerConfig()
config.resolveStrategy = Closure.DELEGATE_FIRST
config()
```

**Builders — DSL for hierarchical structures:**

```groovy
def writer = new StringWriter()
def xml = new groovy.xml.MarkupBuilder(writer)
xml.person(id: 1) {
    name(first: "Alice", last: "Smith")
    addresses {
        address(type: "home") {
            street("123 Main St")
            city("Springfield")
        }
    }
}

def json = new groovy.json.JsonBuilder()
json {
    id 1
    name "Alice"
    roles(["admin", "user"])
    metadata active: true, since: "2023"
}
```

**AST transformations.** Compile-time code generation:

```groovy
@ToString(includeFields = true, excludes = "password")
@EqualsAndHashCode
@Immutable
class User {
    String name, email, password
    int age
}

@CompileStatic
def calculate(int a, int b) { a + b }  // static type checking

@Grab(group='com.google.guava', module='guava', version='33.0.0-jre')
import com.google.common.collect.ImmutableList
```

**Spock testing.** BDD-style specifications with data-driven testing:

```groovy
class MathSpec extends spock.lang.Specification {
    def "addition returns sum"() {
        given: def calc = new Calculator()
        when:  def result = calc.add(a, b)
        then:  result == expected
        where: a  | b  | expected
               1  | 2  | 3
               0  | 0  | 0
    }

    def "mocked dependency"() {
        given:
        def repo = Mock(UserRepository)
        def service = new UserService(repo)
        when: service.deactivateUser(42L)
        then:
        1 * repo.findById(42L) >> Optional.of(new User(active: true))
        1 * repo.save(_) >> { User u -> u }
    }
}
```

### Scala

Scala combines object-oriented and functional programming on the JVM.

**Case classes.** Immutable data carriers with structural equality:

```scala
case class Person(name: String, age: Int, email: Option[String] = None)

val alice = Person("Alice", 30, Some("alice@example.com"))
val bob = alice.copy(name = "Bob")   // Person("Bob", 30, Some("alice@example.com"))
assert(alice != bob)                 // structural inequality
```

**Givens/Using (Scala 3).** Type class instances and context parameters:

```scala
trait Show[T]:
  def show(value: T): String

given Show[Int] with
  def show(value: Int): String = value.toString

given Show[Person] with
  def show(p: Person): String = s"${p.name} (${p.age})"

def format[T](value: T)(using s: Show[T]): String = s.show(value)
format(42)              // "42"
format(Person("Alice", 30))  // "Alice (30)"
```

**For-comprehensions.** Syntactic sugar over `flatMap`, `map`:

```scala
for
  id      <- userIds
  user    <- userRepo.find(id)
  account <- accountService.get(user.accountId)
  if account.active
yield (user, account)
```

**Pattern matching.** Exhaustive matching:

```scala
sealed trait Expr
case class Num(value: Int) extends Expr
case class Add(left: Expr, right: Expr) extends Expr

def eval(e: Expr): Int = e match
  case Num(v)    => v
  case Add(l, r) => eval(l) + eval(r)
```

**Higher-kinded types:**

```scala
trait Functor[F[_]]:
  def map[A, B](fa: F[A])(f: A => B): F[B]

given Functor[List] with
  def map[A, B](fa: List[A])(f: A => B): List[B] = fa.map(f)

given Functor[Option] with
  def map[A, B](fa: Option[A])(f: A => B): Option[B] = fa.map(f)
```

**ZIO / Cats Effect.** Functional effect systems:

```scala
import zio.*

val program: ZIO[UserRepo & EmailService, Throwable, Unit] = for
  user  <- userRepo.findById(userId).someOrFail(new NotFoundException(...))
  _     <- emailService.sendWelcome(user.email).retry(Schedule.exponential(1.second))
yield ()
```

### Clojure

Clojure is a Lisp on the JVM. Immutable data structures are the default.

**Immutable data structures:**

```clojure
(def data {:name "Alice" :roles #{"admin" "user"} :scores [95 87 92]})

;; Threading macro:
(->> data :scores (map #(* % 2)) (filter even?))

;; Update nested structures:
(update-in data [:settings :notifications :email] not)

;; Records:
(defrecord User [id name email])
(def alice (->User 1 "Alice" "alice@example.com"))
(:name alice) ; "Alice"
```

**STM (Software Transactional Memory):**

```clojure
(def account-a (ref 1000))
(def account-b (ref 500))

(defn transfer [from to amount]
  (dosync
    (alter from - amount)
    (alter to + amount)))
(transfer account-a account-b 200)
@account-a  ; 800 — both changed atomically, or neither
```

**Atoms and Agents:**

```clojure
(def counter (atom 0))
(swap! counter inc)                    ; thread-safe

(def log-agent (agent []))
(send log-agent conj {:event "startup" :ts (System/currentTimeMillis)})
```

**Macros:**

```clojure
(defmacro unless [test & body]
  `(if (not ~test) (do ~@body)))
(unless (< 5 3) (println "This prints"))
```

**core.async:**

```clojure
(require '[clojure.core.async :as a])
(def messages (a/chan 10))
(a/go (a/>! messages "hello") (a/>! messages "world") (a/close! messages))
(a/go (loop [] (when-let [msg (a/<! messages)] (println "Received:" msg) (recur))))
```

### J2ME (Java 2 Micro Edition)

J2ME targets resource-constrained devices (feature phones, embedded systems)
with a severely restricted JVM. CLDC 1.1 is the most widespread configuration.

**MIDlet lifecycle:**

```java
import javax.microedition.midlet.MIDlet;
import javax.microedition.lcdui.*;

public class MyMIDlet extends MIDlet {
    private Display display;
    private Form form;

    public MyMIDlet() {
        display = Display.getDisplay(this);
        form = new Form("Hello J2ME");
        form.append("Welcome to J2ME!");
    }
    protected void startApp() { display.setCurrent(form); }
    protected void pauseApp() { }
    protected void destroyApp(boolean unconditional) {
        if (form != null) { form = null; }
    }
}
```

**RMS (Record Management System):**

```java
import javax.microedition.rms.*;

public class GameScores {
    private RecordStore store;

    public void open() throws RecordStoreException {
        store = RecordStore.openRecordStore("GameScores", true);
    }

    public int saveScore(String player, int score) throws RecordStoreException {
        byte[] data = (player + ":" + score).getBytes();
        return store.addRecord(data, 0, data.length);
    }

    public void listScores() throws RecordStoreException {
        RecordEnumeration re = store.enumerateRecords(null, null, false);
        while (re.hasNextElement()) {
            int id = re.nextRecordId();
            byte[] data = store.getRecord(id);
            System.out.println("Record " + id + ": " + new String(data));
        }
        re.destroy();
    }

    public void close() throws RecordStoreException {
        if (store != null) { store.closeRecordStore(); }
    }
}
```

**J2ME limitations (CLDC 1.1):**

| Feature | Java SE | J2ME CLDC 1.1 |
|---------|---------|---------------|
| Generics | yes | **no** |
| Reflection | full | **none** |
| Enums | `enum` keyword | **no** |
| Collections | full `java.util.*` | `Vector`, `Hashtable`, `Stack` only |
| Floating point | `float`, `double` | optional in 1.0, standard in 1.1 |
| Serialization | full | **none** |
| String methods | `split`, `format`, `join` | `indexOf`, `substring`, `trim` only |
| Networking | `java.net.*` | GCF `Connector.open()` |

**HTTP via GCF (Generic Connection Framework):**

```java
import javax.microedition.io.*;
import java.io.*;

public String fetchUrl(String url) throws IOException {
    HttpConnection conn = null;
    InputStream is = null;
    try {
        conn = (HttpConnection) Connector.open(url);
        conn.setRequestMethod(HttpConnection.GET);
        if (conn.getResponseCode() != HttpConnection.HTTP_OK) {
            throw new IOException("HTTP " + conn.getResponseCode());
        }
        is = conn.openInputStream();
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        int ch;
        while ((ch = is.read()) != -1) { baos.write(ch); }
        return baos.toString();
    } finally {
        if (is != null) { try { is.close(); } catch (IOException ignored) {} }
        if (conn != null) { try { conn.close(); } catch (IOException ignored) {} }
    }
}
```

---

## XML Structures

XML is the substrate of Java enterprise configuration. Understanding the
parser APIs and their tradeoffs is essential for configuration, debugging,
and security.

### XML Parser APIs

| API | Model | Memory | Use case |
|-----|-------|--------|----------|
| **DOM** | Tree (loads entire doc) | High (10x file size) | Small docs, random access, XPath |
| **SAX** | Event-driven (push, callbacks) | Low (constant) | Large docs, streaming, validation |
| **StAX** | Pull parser (cursor, you control) | Low (constant) | Large docs, partial parsing |

**DOM:**

```java
DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
DocumentBuilder builder = factory.newDocumentBuilder();
Document doc = builder.parse(new File("config.xml"));

Element root = doc.getDocumentElement();
NodeList users = root.getElementsByTagName("user");
for (int i = 0; i < users.getLength(); i++) {
    Element user = (Element) users.item(i);
    String id = user.getAttribute("id");
    String name = user.getElementsByTagName("name").item(0).getTextContent();
}

// XPath:
XPath xpath = XPathFactory.newInstance().newXPath();
String email = xpath.evaluate("/config/user[@id='1']/email", doc);
```

**SAX (push-parser):**

```java
SAXParserFactory factory = SAXParserFactory.newInstance();
factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
SAXParser parser = factory.newSAXParser();

parser.parse(new File("large.xml"), new DefaultHandler() {
    private final StringBuilder text = new StringBuilder();
    @Override
    public void startElement(String uri, String local, String qName, Attributes attrs) {
        text.setLength(0);
        if ("user".equals(qName)) {
            System.out.println("User id=" + attrs.getValue("id"));
        }
    }
    @Override
    public void characters(char[] ch, int start, int length) {
        text.append(ch, start, length);  // SAX may deliver text in chunks
    }
    @Override
    public void endElement(String uri, String local, String qName) {
        if ("name".equals(qName)) {
            System.out.println("  name=" + text.toString().trim());
        }
    }
});
```

**StAX (pull-parser):**

```java
XMLInputFactory factory = XMLInputFactory.newInstance();
factory.setProperty(XMLInputFactory.SUPPORT_DTD, false);
XMLStreamReader reader = factory.createXMLStreamReader(new FileInputStream("data.xml"));

while (reader.hasNext()) {
    int event = reader.next();
    if (event == XMLStreamConstants.START_ELEMENT) {
        String tag = reader.getLocalName();
        for (int i = 0; i < reader.getAttributeCount(); i++) {
            System.out.printf("  %s.%s = %s%n", tag,
                reader.getAttributeLocalName(i), reader.getAttributeValue(i));
        }
    } else if (event == XMLStreamConstants.CHARACTERS) {
        String text = reader.getText().trim();
        if (!text.isEmpty()) { System.out.println("  text: " + text); }
    }
}
reader.close();
```

### JAXB — Java Architecture for XML Binding

XML to POJO mapping. Removed from JDK in Java 11 (use `jakarta.xml.bind`
dependency).

```java
@XmlRootElement(name = "order")
@XmlAccessorType(XmlAccessType.FIELD)
public class Order {
    @XmlAttribute(required = true)
    private long id;

    @XmlElement(name = "customer-name")
    private String customer;

    @XmlElementWrapper(name = "items")
    @XmlElement(name = "item")
    private List<OrderItem> items;

    @XmlTransient
    private LocalDateTime processedAt;

    @XmlElement
    private BigDecimal total;

    public Order() { }  // JAXB requires no-arg constructor

    public Order(long id, String customer, List<OrderItem> items) {
        this.id = id;
        this.customer = customer;
        this.items = items;
        this.total = items.stream()
            .map(i -> i.price().multiply(BigDecimal.valueOf(i.quantity())))
            .reduce(BigDecimal.ZERO, BigDecimal::add);
    }
}

// Marshalling (Java -> XML):
JAXBContext ctx = JAXBContext.newInstance(Order.class);
Marshaller m = ctx.createMarshaller();
m.setProperty(Marshaller.JAXB_FORMATTED_OUTPUT, true);
m.marshal(order, System.out);

// Unmarshalling (XML -> Java):
Unmarshaller um = ctx.createUnmarshaller();
Order order = (Order) um.unmarshal(new File("order.xml"));
```

### XXE (XML External Entity) Prevention

**THE most common Java XML security vulnerability.** XXE allows reading local
files, SSRF, or denial of service.

```java
// VULNERABLE — DO NOT USE:
DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
DocumentBuilder builder = factory.newDocumentBuilder();
Document doc = builder.parse(attackerControlledXml);
// Attacker: <!ENTITY xxe SYSTEM "file:///etc/passwd">

// SECURE — apply to EVERY XML parser factory:
String DISALLOW_DTD = "http://apache.org/xml/features/disallow-doctype-decl";
dbf.setFeature(DISALLOW_DTD, true);
dbf.setFeature("http://xml.org/sax/features/external-general-entities", false);
dbf.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
dbf.setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false);
dbf.setXIncludeAware(false);
dbf.setExpandEntityReferences(false);

// Billion Laughs — exponential entity expansion DoS. Prevention: disable DTDs entirely.

// SAX equivalent:
SAXParserFactory spf = SAXParserFactory.newInstance();
spf.setFeature(DISALLOW_DTD, true);

// StAX equivalent:
XMLInputFactory xif = XMLInputFactory.newInstance();
xif.setProperty(XMLInputFactory.SUPPORT_DTD, false);

// TransformerFactory, XPathFactory, SchemaFactory — all need FEATURE_SECURE_PROCESSING:
TransformerFactory tf = TransformerFactory.newInstance();
tf.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
```

### Spring XML Configuration (legacy)

```xml
<beans xmlns="http://www.springframework.org/schema/beans"
       xmlns:context="http://www.springframework.org/schema/context">

    <context:component-scan base-package="com.example" />

    <bean id="dataSource" class="com.zaxxer.hikari.HikariDataSource"
          destroy-method="close">
        <property name="jdbcUrl" value="${db.url}" />
        <property name="username" value="${db.username}" />
    </bean>

    <bean id="userService" class="com.example.UserService">
        <constructor-arg ref="userRepository" />
        <property name="notificationService" ref="notificationService" />
    </bean>

    <bean id="settings" class="com.example.Settings">
        <property name="roles">
            <list><value>ADMIN</value><value>USER</value></list>
        </property>
        <property name="emailConfig">
            <map>
                <entry key="smtp.host" value="smtp.example.com" />
                <entry key="smtp.port" value="587" />
            </map>
        </property>
    </bean>
</beans>
```

### web.xml

```xml
<web-app xmlns="https://jakarta.ee/xml/ns/jakartaee" version="6.0">
    <servlet>
        <servlet-name>dispatcher</servlet-name>
        <servlet-class>org.springframework.web.servlet.DispatcherServlet</servlet-class>
        <load-on-startup>1</load-on-startup>
    </servlet>
    <servlet-mapping>
        <servlet-name>dispatcher</servlet-name>
        <url-pattern>/</url-pattern>
    </servlet-mapping>
    <filter>
        <filter-name>cors</filter-name>
        <filter-class>com.example.web.CorsFilter</filter-class>
    </filter>
    <filter-mapping>
        <filter-name>cors</filter-name>
        <url-pattern>/api/*</url-pattern>
    </filter-mapping>
    <session-config>
        <session-timeout>30</session-timeout>
        <cookie-config>
            <http-only>true</http-only>
            <secure>true</secure>
        </cookie-config>
    </session-config>
    <error-page>
        <error-code>404</error-code>
        <location>/error/404</location>
    </error-page>
</web-app>
```

### persistence.xml

```xml
<persistence version="3.0"
             xmlns="https://jakarta.ee/xml/ns/persistence">
    <persistence-unit name="myapp" transaction-type="JTA">
        <provider>org.hibernate.jpa.HibernatePersistenceProvider</provider>
        <jta-data-source>java:/jdbc/MyAppDS</jta-data-source>
        <class>com.example.entity.User</class>
        <exclude-unlisted-classes>true</exclude-unlisted-classes>
        <properties>
            <property name="hibernate.dialect"
                      value="org.hibernate.dialect.PostgreSQLDialect" />
            <property name="hibernate.hbm2ddl.auto" value="validate" />
            <property name="hibernate.cache.use_second_level_cache" value="true" />
        </properties>
    </persistence-unit>
</persistence>
```

---

## Security

### OWASP Top 10 in Java

**1. SQL Injection — ALWAYS use PreparedStatement:**

```java
// WRONG:
String sql = "SELECT * FROM users WHERE id = " + userId;
Statement stmt = conn.createStatement();
ResultSet rs = stmt.executeQuery(sql);

// CORRECT — parameterized query:
String sql = "SELECT * FROM users WHERE id = ?";
PreparedStatement ps = conn.prepareStatement(sql);
ps.setLong(1, userId);            // type-safe, escaped, no injection possible
ResultSet rs = ps.executeQuery();

// Dynamic ORDER BY — whitelist-validate column name:
private static final Set<String> ALLOWED_COLUMNS = Set.of("id", "name", "email", "created_at");
public List<User> findSorted(String column) {
    if (column == null || !ALLOWED_COLUMNS.contains(column)) {
        throw new IllegalArgumentException("Invalid column: " + column);
    }
    String sql = "SELECT * FROM users ORDER BY " + column;
    // Safe — column value is from our whitelist, not user input
}

// JPA parameter binding:
TypedQuery<User> q = em.createQuery(
    "SELECT u FROM User u WHERE u.name = :name", User.class);
q.setParameter("name", name);
```

**2. Command Injection — ProcessBuilder, not Runtime.exec:**

```java
// WRONG — Runtime.exec(String) splits on whitespace, passes to shell:
Runtime.getRuntime().exec("ping -c 1 " + userInput);

// CORRECT — ProcessBuilder with separate arguments (no shell):
ProcessBuilder pb = new ProcessBuilder("ping", "-c", "1", userInput);
Process p = pb.start();
// Each argument is passed directly; no shell interpretation.
```

**3. Sensitive Data Exposure — char[] not String for passwords:**

```java
// String is immutable — password stays in string pool until GC.
// char[] can be zeroed out immediately after use.
public boolean authenticate(String username, char[] password) {
    try {
        return checkCredentials(username, password);
    } finally {
        Arrays.fill(password, '0');
    }
}

// AES-GCM (authenticated encryption):
Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
GCMParameterSpec gcmSpec = new GCMParameterSpec(128, nonce);
cipher.init(Cipher.ENCRYPT_MODE, key, gcmSpec);
byte[] ciphertext = cipher.doFinal(plaintext);
// NEVER use ECB mode — patterns visible in ciphertext.
// NEVER hardcode keys. NEVER use DES (cracked).
```

**4. Insecure Deserialization:**

```java
// Java serialization is fundamentally unsafe for untrusted data.
// GLOBAL filter at JVM startup:
// java -Djdk.serialFilter='maxbytes=102400;com.example.**;!*'

// Per-stream filter (Java 17+):
ObjectInputFilter filter = ObjectInputFilter.Config.createFilter(
    "com.example.model.*;!*");
try (ObjectInputStream ois = new ObjectInputStream(new FileInputStream("data.bin"))) {
    ois.setObjectInputFilter(filter);
    Object obj = ois.readObject();
}

// Prefer safer alternatives: JSON with schema validation, Protocol Buffers, Avro.
// ysoserial is the canonical gadget-chain exploit — every class on the classpath
// is a potential gadget when deserializing untrusted input.
```

**5. Broken Access Control — method-level security (Spring Security):**

```java
@Service
public class DocumentService {
    @PreAuthorize("hasRole('ADMIN') or #document.owner == authentication.name")
    public void update(Document document, String content) {
        document.setContent(content);
        documentRepository.save(document);
    }

    @PostAuthorize("returnObject.owner == authentication.name or hasRole('ADMIN')")
    public Document findById(long id) {
        return documentRepository.findById(id).orElseThrow();
    }

    @PreFilter("filterObject.owner == authentication.name")
    public void deleteAll(List<Document> documents) {
        documentRepository.deleteAll(documents);
    }

    @PostFilter("filterObject.public or filterObject.owner == authentication.name")
    public List<Document> search(String query) {
        return documentRepository.findByQuery(query);
    }
}

@Configuration
@EnableMethodSecurity  // enables @PreAuthorize, @PostAuthorize, etc.
public class SecurityConfig { }
```

**6. XSS — output encoding by context:**

```java
// HTML context:
String safe = HtmlUtils.htmlEscape(userInput);

// JavaScript context:
String safe = JavaScriptUtils.javaScriptEscape(userInput);

// URL context:
String safe = URLEncoder.encode(userInput, StandardCharsets.UTF_8);

// Content-Security-Policy header:
response.setHeader("Content-Security-Policy",
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'");
```

**7. Timing Attacks — constant-time comparison:**

```java
// WRONG — String.equals short-circuits:
if (password.equals(storedHash)) { ... }

// CORRECT:
if (MessageDigest.isEqual(
        password.getBytes(StandardCharsets.UTF_8),
        storedHash.getBytes(StandardCharsets.UTF_8))) { ... }
```

**8. Path Traversal — normalize + validate:**

```java
public Path resolveSafe(Path baseDir, String userPath) {
    Path resolved = baseDir.resolve(userPath).normalize().toAbsolutePath();
    Path base = baseDir.toAbsolutePath().normalize();
    if (!resolved.startsWith(base)) {
        throw new SecurityException("Path traversal: " + userPath);
    }
    Path realBase = base.toRealPath();
    Path realResolved = resolved.toRealPath();
    if (!realResolved.startsWith(realBase)) {
        throw new SecurityException("Path traversal (symlink): " + userPath);
    }
    return realResolved;
}
```

**9. Log Injection — strip CRLF:**

```java
// Attacker input: "alice\nINFO admin logged in"
// Results in TWO log lines; the second is forged.
String sanitized = userInput.replaceAll("[\r\n]", "_");
log.info("User input: {}", sanitized);
```

**10. Security Misconfiguration — no stack traces to clients:**

```java
@RestControllerAdvice
public class GlobalExceptionHandler {
    @ExceptionHandler(Exception.class)
    @ResponseStatus(HttpStatus.INTERNAL_SERVER_ERROR)
    public ErrorResponse handle(Exception e) {
        log.error("Unhandled exception", e);  // log the full stacktrace
        return new ErrorResponse("INTERNAL_ERROR", "An unexpected error occurred");
        // NEVER return e.getMessage() or e.getStackTrace() to the client.
    }
}
```

---

## Debugging & Tooling

### Remote Debugging

```
# JVM startup flags:
-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=*:5005

# suspend=y: JVM waits for debugger before running main()
# suspend=n: JVM starts normally, debugger attaches later

# Connect: jdb -attach localhost:5005
```

### Thread Dumps

A thread dump captures every thread's stack trace plus lock information.

```
# From outside:
jstack <PID> > threaddump.txt
kill -3 <PID>                    # SIGQUIT — JVM prints dump to stdout

# From inside:
Thread.getAllStackTraces().forEach((thread, stack) -> {
    System.out.println(thread.getName() + " (" + thread.getState() + ")");
    for (StackTraceElement frame : stack) {
        System.out.println("    at " + frame);
    }
});
```

**Thread dump analysis:**

| State | Meaning | Action |
|-------|---------|--------|
| `BLOCKED` | Waiting to enter synchronized block | Find the lock holder |
| `WAITING` (object monitor) | `Object.wait()` | Find who should `notify()` |
| `WAITING` (parking) | `LockSupport.park()` | Check condition being awaited |
| `TIMED_WAITING` (sleep) | `Thread.sleep()` | Fine — it'll wake up |
| `TIMED_WAITING` (parking) | `Lock.tryLock(timeout)` | Check timeout reasonableness |
| `RUNNABLE` | Actually running OR I/O wait | CPU pegged = busy loop; CPU idle = I/O |

### Heap Dumps

```
# Capture:
jmap -dump:live,format=b,file=heap.bin <PID>

# On OOM:
-XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/var/dumps/heap.hprof
```

**Eclipse MAT — key analyses:**
- **Leak Suspects report** — auto-generated pie chart of biggest objects
- **Dominator Tree** — objects by retained size; sort by retained to find leaks
- **Histogram** — class counts; look for unexpected counts (millions of `HashMap$Node`)
- **Path to GC Roots** — shows WHY an object is alive

**Common leak patterns:**
- `ThreadLocal` accumulation (thread pool + uncleaned ThreadLocals)
- Static `HashMap` growing without bound (cache without eviction)
- `InputStream` / `Connection` never closed (finalizer backlog)
- ClassLoader leaks (reloadable WAR without proper cleanup)

### Java Flight Recorder (JFR)

Low-overhead (<2% CPU) profiling built into the JDK.

```
# Start at JVM launch:
java -XX:StartFlightRecording:filename=recording.jfr,duration=60s ...

# Control with jcmd:
jcmd <PID> JFR.start name=profiling duration=300s filename=profile.jfr
jcmd <PID> JFR.dump  name=profiling filename=profile.jfr
jcmd <PID> JFR.stop  name=profiling

# Analyze with JDK Mission Control (JMC):
# jmc &  -> open profile.jfr -> Automated Analysis Results
```

**Custom JFR events:**

```java
import jdk.jfr.*;

@Name("com.example.Payment")
@Label("Payment Transaction")
public class PaymentEvent extends Event {
    @Label("Order ID") private long orderId;
    @Label("Amount") private BigDecimal amount;

    public PaymentEvent(long orderId, BigDecimal amount) {
        this.orderId = orderId;
        this.amount = amount;
    }
}

// Usage:
PaymentEvent event = new PaymentEvent(orderId, amount);
event.begin();
try { processPayment(orderId, amount); }
finally { event.commit(); }
```

### GC Logging

```
# Unified -Xlog framework (Java 9+):
-Xlog:gc*:file=gc.log:time,uptime,level,tags

# Detailed with rotation:
-Xlog:gc*=info,gc+heap=debug:file=gc.log::filecount=5,filesize=50M

# Analyze with GCeasy (https://gceasy.io):
#   - GC pause time distribution
#   - Throughput %
#   - Allocation rate
#   - Tenuring summary
```

### JVM Flags Reference

| Category | Flag | Effect |
|----------|------|--------|
| **Heap** | `-Xms2g` / `-Xmx4g` | Initial / Max heap |
| | `-XX:NewRatio=2` | Old:Young ratio |
| **Metaspace** | `-XX:MaxMetaspaceSize=256m` | Cap class metadata (always set!) |
| **Direct** | `-XX:MaxDirectMemorySize=512m` | Cap off-heap memory |
| **GC** | `-XX:+UseG1GC` | G1 — default since Java 9 |
| | `-XX:+UseZGC` | ZGC — sub-millisecond pause |
| | `-XX:+UseShenandoahGC` | Shenandoah — concurrent compaction |
| | `-XX:+UseSerialGC` | Serial — small heaps |
| **G1 tune** | `-XX:MaxGCPauseMillis=200` | Soft target max pause |
| | `-XX:InitiatingHeapOccupancyPercent=45` | Start concurrent cycle % |
| **Runtime** | `-XX:+AlwaysPreTouch` | Touch heap pages at startup |
| | `-XX:+UseContainerSupport` | Detect container limits (Java 10+) |
| | `-XX:+ExitOnOutOfMemoryError` | Kill on OOM |
| | `-XX:+HeapDumpOnOutOfMemoryError` | Write .hprof on OOM |
| **String** | `-XX:+UseStringDeduplication` | G1: dedup char[] arrays |

### JMX (Java Management Extensions)

```java
// JVM startup: -Dcom.sun.management.jmxremote.port=9999

// Programmatic MBean:
public interface CacheStatsMBean {
    long getHitCount();
    long getMissCount();
    double getHitRatio();
    void reset();
}

public class CacheStats extends NotificationBroadcasterSupport
        implements CacheStatsMBean {
    private final AtomicLong hits = new AtomicLong();
    private final AtomicLong misses = new AtomicLong();

    public long getHitCount() { return hits.get(); }
    public long getMissCount() { return misses.get(); }
    public double getHitRatio() {
        long total = hits.get() + misses.get();
        return total == 0 ? 0.0 : (double) hits.get() / total;
    }
    public void reset() { hits.set(0); misses.set(0); }

    public void register() throws Exception {
        ManagementFactory.getPlatformMBeanServer().registerMBean(
            this, new ObjectName("com.example:type=CacheStats"));
    }
}
```

---

## JVM Internals

### Class File Format

Every `.class` file starts with `0xCAFEBABE`. The structure:

```
ClassFile {
    u4 magic;                // 0xCAFEBABE
    u2 minor_version;
    u2 major_version;        // 65 = Java 21, 61 = Java 17, 55 = Java 11, 52 = Java 8
    u2 constant_pool_count;  // 1-indexed
    cp_info constant_pool[constant_pool_count-1];
    u2 access_flags;         // ACC_PUBLIC=0x0001, ACC_FINAL=0x0010, etc.
    u2 this_class;
    u2 super_class;
    u2 interfaces_count;
    u2 interfaces[];
    u2 fields_count;
    field_info fields[];
    u2 methods_count;
    method_info methods[];
    u2 attributes_count;
    attribute_info attributes[];
}
```

**Version mapping:**

| Major | Java |
|-------|------|
| 52 | 8 |
| 55 | 11 |
| 61 | 17 |
| 65 | 21 |

**Access flags:**

| Flag | hex | Meaning |
|------|-----|---------|
| `ACC_PUBLIC` | 0x0001 | Visible everywhere |
| `ACC_FINAL` | 0x0010 | Cannot be subclassed |
| `ACC_SUPER` | 0x0020 | Invokespecial uses "new" semantics |
| `ACC_INTERFACE` | 0x0200 | Is an interface |
| `ACC_ABSTRACT` | 0x0400 | Cannot be instantiated |
| `ACC_SYNTHETIC` | 0x1000 | Generated by compiler |
| `ACC_ANNOTATION` | 0x2000 | Is an annotation type |
| `ACC_ENUM` | 0x4000 | Is an enum |

### Bytecode Instructions

The JVM instruction set is stack-based. Most instructions are typed.

```
Load/Store:
  iload_0          — push int from local var 0
  istore_1         — pop int into local var 1
  aload_2          — push reference from local var 2
  bipush 42        — push byte as int
  sipush 1000      — push short as int

Object creation:
  new #5           — allocate object, push reference
  dup              — duplicate top of stack
  invokespecial #8 — call <init>, private, or super

Method invocation:
  invokevirtual #12    — virtual dispatch (vtable)
  invokestatic  #15    — static method
  invokespecial #18    — constructor, private, super
  invokeinterface #21  — interface method (itable)
  invokedynamic #24    — dynamic call site (lambdas, string concat)

Field access:
  getfield #27     — push instance field
  putfield #30     — set instance field
  getstatic #33    — push static field

Control flow:
  ifeq 10          — branch if int == 0
  if_icmpeq 10     — branch if two ints equal
  goto 20          — unconditional branch
  tableswitch      — contiguous case values
  lookupswitch     — sparse case values (binary search)

Returns:
  ireturn, areturn, return

Stack management:
  dup, pop, swap
```

**Invoke comparison:**

| Instruction | Target | Dispatch | Use |
|-------------|--------|----------|-----|
| `invokestatic` | Static method | Static | Static calls |
| `invokespecial` | Constructors, private, super | Exact | `new`, `super.method()` |
| `invokevirtual` | Instance method | Virtual (vtable) | `obj.method()` |
| `invokeinterface` | Interface method | itable | `list.add()` |
| `invokedynamic` | Bootstrap decides | Dynamic, then linked | Lambdas, string concat |

**Exception tables.** Each method has an exception table mapping bytecode ranges
(`start_pc` to `end_pc`) to handlers (`handler_pc`) for specific exception types.
`finally` blocks are compiled by copying the body before every exit point.

### JIT Compilation

**Tiered compilation levels (0-4):**

| Level | Compiler | Characteristics |
|-------|----------|----------------|
| 0 | Interpreter | Always active. Collects profiling data. |
| 1 | C1, no profiling | Simple methods, fast compilation |
| 2 | C1, limited profiling | Lightweight counters |
| 3 | C1, full profiling | Type profiles, branch counts (feeds C2) |
| 4 | C2 (server) | Aggressive optimizations: inlining, escape analysis, lock elision |

**C2 optimizations:** method inlining, loop unrolling, escape analysis (heap ->
stack), lock elision, dead code elimination, constant folding, branch prediction
from profile data.

**OSR (On-Stack Replacement).** When a loop iterates many times, the JIT compiles
the method at the loop entry and replaces the running frame with a compiled one.

**Deoptimization.** When an assumption the JIT made becomes invalid (class hierarchy
change, wrong type profile), the compiled frame is rolled back to interpreter state.

**Intrinsics.** The JVM recognizes certain methods (`System.arraycopy`,
`String.indexOf`, `Math.sqrt`, `Object.hashCode`) and replaces them with
hand-written assembly in the JIT.

### Garbage Collection

**GC algorithm selection:**

| Algorithm | Pause style | Best for |
|-----------|------------|----------|
| **Serial** | Stop-the-world, single-threaded | Small heaps (<100MB), containers |
| **Parallel** | Stop-the-world, multi-threaded | Throughput, batch processing |
| **G1** | Mostly concurrent, region-based | Low-pause, default since Java 9 |
| **ZGC** | Sub-millisecond, colored pointers | Multi-TB heaps, low-latency |
| **Shenandoah** | Concurrent compaction, Brooks pointers | Low-pause, lower CPU than ZGC |
| **Epsilon** | No-op (allocates only) | Testing, short-lived JVMs |

**G1 GC key concepts:**
- **Regions** (1-32MB) — heap divided into equal-sized regions (Eden, Survivor, Old, Humongous)
- **Remembered Sets (RSets)** — each region tracks references INTO it from other regions
- **SATB (Snapshot-At-The-Beginning)** — concurrent marking algorithm
- **Mixed collections** — young + some old regions
- **Humongous objects** — >= 50% of region size, allocated contiguously

**ZGC key concepts:**
- **Colored pointers** — metadata in unused bits of 64-bit pointers (marked0, marked1, remapped, finalizable)
- **Load barriers** — every heap reference load checks color bits; ~4% throughput overhead
- **Concurrent everything** — marking, relocation, remapping all concurrent
- **Generational ZGC (Java 21+)** — young and old generations for better throughput

### Object Layout

```
|-------------|-------------|--------------|----------|
| Mark Word   | Klass Ptr   | Fields       | Padding  |
| (8/4 bytes) | (8/4 bytes) | (variable)   | (align)  |
|-------------|-------------|--------------|----------|
<--- object header (12/16 bytes) --->  <-- data -->
```

**Mark Word (64-bit, simplified):** The last 2 bits encode lock state:
`01` = unlocked/biased, `00` = thin-locked, `10` = inflated, `11` = GC marking.

**Klass pointer.** Points to the class's `Klass` structure in Metaspace.
Compressed Oops (`-XX:+UseCompressedOops`, on for heaps <32GB) shrink from 8 to 4 bytes.

**Object alignment.** Default 8-byte alignment. `new Object()` = 16 bytes on 64-bit
with compressed oops: 8-byte mark + 4-byte klass + 4-byte padding.

### Locking (Lock Inflation)

1. **Biased locking** (removed in Java 15): first thread stamps its ID in mark word
2. **Thin locking (CAS):** pointer to stack-frame lock record stored in mark word
3. **Inflation:** native `ObjectMonitor` allocated; entry list, wait set, owner

**ReentrantLock** adds features over `synchronized`:

```java
ReentrantLock lock = new ReentrantLock(true);
lock.lock();
try { /* critical section */ }
finally { lock.unlock(); }
// Extras: tryLock(timeout), lockInterruptibly(), getHoldCount(), newCondition()
```

### Virtual Threads (Java 21, Project Loom)

Virtual threads are lightweight threads managed by the JVM. When a virtual
thread blocks, its carrier platform thread is released for other work.

```java
// Create virtual threads:
Thread vt = Thread.ofVirtual().name("worker-1").start(() -> {
    var result = httpClient.send(request, BodyHandlers.ofString());
    System.out.println(result.body());
});
vt.join();

// ExecutorService with virtual threads:
try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
    var futures = tasks.stream()
        .map(task -> executor.submit(() -> process(task)))
        .toList();
    for (var future : futures) { future.get(); }
}

// Structured concurrency:
try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
    Future<User> user = scope.fork(() -> userService.get(id));
    Future<List<Order>> orders = scope.fork(() -> orderService.findByUser(id));
    scope.join();
    scope.throwIfFailed();
    return new UserProfile(user.resultNow(), orders.resultNow());
}
```

**Pinning (when virtual threads DO block the carrier):**
- `synchronized` blocks/methods — use `ReentrantLock` instead
- JNI native code
- `Object.wait()` — use `Lock` and `Condition` instead
- Detect with `-Djdk.tracePinnedThreads=full`

**Continuation.** Every virtual thread is backed by a `Continuation` object.
The call stack is saved to heap on yield and restored on resume — enabling
millions of concurrent operations on a few OS threads.

---

## Frameworks

### Spring Boot

```java
@SpringBootApplication  // = @Configuration + @EnableAutoConfiguration + @ComponentScan
public class Application {
    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
```

**application.yml:**

```yaml
server:
  port: 8080

spring:
  datasource:
    url: jdbc:postgresql://localhost:5432/myapp
    username: ${DB_USERNAME}
    password: ${DB_PASSWORD}
    hikari:
      maximum-pool-size: 10
      connection-timeout: 5000
  jpa:
    hibernate:
      ddl-auto: validate   # none, validate, update, create, create-drop
    show-sql: false

management:
  endpoints:
    web:
      exposure:
        include: health,info,metrics,prometheus
  endpoint:
    health:
      show-details: when-authorized
```

**REST controller:**

```java
@RestController
@RequestMapping("/api/users")
public class UserController {
    private final UserService userService;

    public UserController(UserService userService) {
        this.userService = userService;
    }

    @GetMapping("/{id}")
    public ResponseEntity<UserResponse> getById(@PathVariable long id) {
        return ResponseEntity.ok(userService.getById(id));
    }

    @PostMapping
    public ResponseEntity<UserResponse> create(
            @Valid @RequestBody CreateUserRequest request) {
        UserResponse created = userService.create(request);
        URI location = ServletUriComponentsBuilder.fromCurrentRequest()
            .path("/{id}").buildAndExpand(created.id()).toUri();
        return ResponseEntity.created(location).body(created);
    }
}
```

### JPA / Hibernate

```java
@Entity
@Table(name = "orders")
public class Order {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "customer_id")
    private Customer customer;

    @OneToMany(mappedBy = "order", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<OrderLineItem> items = new ArrayList<>();

    @Enumerated(EnumType.STRING)
    private OrderStatus status;

    @Version  // optimistic locking
    private Long version;

    protected Order() { }  // JPA requires no-arg constructor

    public Order(Customer customer, List<OrderLineItem> items) {
        this.customer = customer;
        this.items.addAll(items);
        items.forEach(i -> i.setOrder(this));  // sync bidirectional
        this.status = OrderStatus.PENDING;
    }
}

// N+1 problem and fixes:

// FIX 1: Fetch join in JPQL:
List<Order> orders = em.createQuery(
    "SELECT o FROM Order o JOIN FETCH o.customer JOIN FETCH o.items", Order.class)
    .getResultList();

// FIX 2: EntityGraph:
@EntityGraph(attributePaths = {"customer", "items"})
@Query("SELECT o FROM Order o WHERE o.status = :status")
List<Order> findByStatus(@Param("status") OrderStatus status);
```

### Testing: JUnit 5, Mockito, AssertJ

**JUnit 5:**

```java
class UserServiceTest {
    @Test
    @DisplayName("should create user with valid data")
    void createUser_validData_returnsUser() {
        var request = new CreateUserRequest("alice", "alice@example.com");
        var user = userService.create(request);
        assertNotNull(user.id());
        assertEquals("alice", user.username());
    }

    @ParameterizedTest
    @CsvSource({
        " , email@test.com",
        "alice, invalid"
    })
    void createUser_invalidInput_throws(String username, String email) {
        var request = new CreateUserRequest(username, email);
        assertThrows(ValidationException.class, () -> userService.create(request));
    }
}
```

**Mockito:**

```java
@ExtendWith(MockitoExtension.class)
class OrderServiceTest {
    @Mock private OrderRepository orderRepository;
    @Mock private PaymentGateway paymentGateway;
    @InjectMocks private OrderService orderService;

    @Test
    void placeOrder_successfulPayment_returnsCompletedOrder() {
        when(paymentGateway.charge(any(BigDecimal.class)))
            .thenReturn(new PaymentResult("txn_123", PaymentStatus.SUCCESS));
        when(orderRepository.save(any(Order.class)))
            .thenAnswer(invocation -> invocation.getArgument(0));

        var result = orderService.placeOrder(new Order(...));
        assert result.status() == OrderStatus.COMPLETED;
        verify(paymentGateway).charge(any());
        verify(orderRepository).save(any());
    }

    @Test
    void placeOrder_failedPayment_throws() {
        when(paymentGateway.charge(any()))
            .thenThrow(new PaymentFailedException("Insufficient funds"));
        assertThrows(OrderFailedException.class,
            () -> orderService.placeOrder(new Order(...)));
        verify(orderRepository, never()).save(any());
    }
}
```

**AssertJ:**

```java
assertThat(result)
    .hasSize(1)
    .extracting(User::name, User::email)
    .containsExactly(tuple("Alice Smith", "alice@example.com"));

// Soft assertions:
var softly = new SoftAssertions();
softly.assertThat(address.street()).isNotBlank();
softly.assertThat(address.city()).isNotBlank();
softly.assertThat(address.zip()).matches("\\d{5}(-\\d{4})?");
softly.assertAll();
```

**Testcontainers:**

```java
@SpringBootTest
@Testcontainers
class UserRepositoryIntegrationTest {
    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16")
        .withDatabaseName("testdb")
        .withUsername("test")
        .withPassword("test");

    @DynamicPropertySource
    static void configureProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
    }

    @Autowired private UserRepository repository;

    @Test
    void shouldPersistAndRetrieveUser() {
        var user = new User("alice", "alice@example.com");
        var saved = repository.save(user);
        var found = repository.findById(saved.id());
        assertThat(found).isPresent();
        assertThat(found.get().name()).isEqualTo("alice");
    }
}
```

**WireMock:**

```java
@WireMockTest(httpPort = 8089)
class PaymentGatewayTest {
    @Test
    void shouldRetryOnTransientFailure() {
        stubFor(post(urlEqualTo("/charge"))
            .inScenario("Retry")
            .whenScenarioStateIs(Scenario.STARTED)
            .willReturn(aResponse().withStatus(503))
            .willSetStateTo("Second Attempt"));

        stubFor(post(urlEqualTo("/charge"))
            .inScenario("Retry")
            .whenScenarioStateIs("Second Attempt")
            .willReturn(aResponse()
                .withStatus(200)
                .withBody("{\"transaction_id\":\"txn_123\"}")));

        var result = gateway.charge(new BigDecimal("99.99"));
        assertThat(result.transactionId()).isEqualTo("txn_123");
    }
}
```

---

## Project Structure

### Maven Standard Layout

```
my-app/
├── pom.xml
├── my-app-api/
│   ├── pom.xml
│   └── src/
│       ├── main/java/com/example/api/
│       │   ├── UserService.java
│       │   └── dto/
│       │       └── UserResponse.java
│       └── test/java/com/example/api/
├── my-app-impl/
│   ├── pom.xml
│   └── src/
│       ├── main/java/com/example/impl/
│       │   └── UserServiceImpl.java
│       ├── main/resources/
│       │   └── application.properties
│       └── test/java/com/example/impl/
└── my-app-web/
    ├── pom.xml
    └── src/
        ├── main/java/com/example/web/
        │   ├── Application.java
        │   └── controller/
        └── test/java/com/example/web/
```

### Gradle Standard Layout

```
my-app/
├── build.gradle.kts
├── settings.gradle.kts
├── gradle/
│   └── libs.versions.toml
├── gradlew
├── api/
│   ├── build.gradle.kts
│   └── src/main/java/...
├── impl/
│   ├── build.gradle.kts
│   └── src/
│       ├── main/java/...
│       ├── main/kotlin/...
│       ├── main/resources/...
│       └── test/java/...
└── web/
    ├── build.gradle.kts
    └── src/...
```

### Package Naming

```
com.company.[module].[layer]

Examples:
  com.example.user.api.UserService
  com.example.user.impl.UserServiceImpl
  com.example.user.web.UserController
  com.example.user.entity.User
  com.example.user.dto.UserResponse
  com.example.user.repo.UserRepository
```

### Spring Boot Package Layout

```
com.example.app/
├── Application.java              // main class, @SpringBootApplication
├── config/
│   ├── SecurityConfig.java
│   └── WebConfig.java
├── controller/
│   ├── UserController.java
│   └── OrderController.java
├── service/
│   ├── UserService.java
│   └── impl/
│       └── UserServiceImpl.java
├── repository/
│   └── UserRepository.java       // Spring Data interface
├── entity/
│   ├── User.java                 // JPA @Entity
│   └── Order.java
├── dto/
│   ├── UserResponse.java
│   └── CreateUserRequest.java
├── exception/
│   └── GlobalExceptionHandler.java
└── mapper/
    └── UserMapper.java           // entity <-> dto mapping
```

### Testing Package Layout

```
src/
├── main/java/com/example/...
└── test/
    ├── java/com/example/
    │   ├── controller/
    │   │   └── UserControllerTest.java
    │   ├── service/
    │   │   └── UserServiceImplTest.java
    │   ├── repository/
    │   │   └── UserRepositoryTest.java   // @DataJpaTest
    │   └── integration/
    │       └── UserFlowIntegrationTest.java
    └── resources/
        ├── application-test.properties
        ├── test-data.sql
        └── fixtures/
            └── sample-user.json
```
