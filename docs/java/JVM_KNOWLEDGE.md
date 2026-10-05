# JVM Knowledge Reference for gludd

Comprehensive Java/JVM reference for gludd agents working with JVM languages,
answering Java questions, debugging JVM issues, or reviewing JVM-based projects.

**Maintained by:** gludd agentic system
**Last updated:** 2026-07-25
**Status of:** Java 21 LTS, Java 22, Java 23 (preview), JVM Specification 21

---

## 1. Java Language

### 1.1 JVM Architecture

The JVM executes Java bytecode. Source code (`.java`) compiles to `.class` files
containing platform-independent bytecode verified and executed at runtime.

```text
Source (.java) → javac → Bytecode (.class) → Class Loader → Bytecode Verifier
→ Interpreter + JIT Compiler (C1/C2) → Machine Code
```

**Class Loader Subsystem.** Bootstrap (loads `java.lang.*` from rt.jar/jmods),
Platform (extension mechanism, Java 9+), Application (classpath). Delegation
model: each loader asks its parent first, loads only if parent fails.

```java
// Class loading is lazy — classes load on first access
Class<?> cls = Class.forName("java.util.ArrayList");
System.out.println(cls.getClassLoader()); // null = bootstrap loader

// Custom class loader example (rarely needed)
class NetworkClassLoader extends ClassLoader {
    @Override
    protected Class<?> findClass(String name) throws ClassNotFoundException {
        byte[] bytes = loadFromNetwork(name);
        return defineClass(name, bytes, 0, bytes.length);
    }
}
```

**Bytecode Verifier.** Checks `.class` files before execution: structural
integrity (magic number `0xCAFEBABE`), type safety (stack slot consistency),
control flow (no jumps to middle of instructions), access control (no private
member access from outside).

**JIT Compilation.** HotSpot profiles running code and compiles hot methods to
native code. Tiered compilation: Level 0 (interpreter), Level 1-3 (C1 with
increasing profiling), Level 4 (C2 aggressively optimized). Deoptimization
reverts to interpreter when speculative optimizations prove wrong.

**Garbage Collection.** Automatic memory management via reachability analysis.
GC roots: active stack frames, static fields, JNI references. See §9 for
algorithm details.

### 1.2 Type System

Java's type system separates primitives from reference types.

```java
// Primitives: fixed size, stack-allocated when local, not objects
byte b = 127;           // 8-bit
short s = 32767;        // 16-bit
int i = 2147483647;     // 32-bit, default for integer literals
long l = 9223372036854775807L;  // 64-bit
float f = 3.14f;        // 32-bit IEEE 754
double d = 3.141592653589793;   // 64-bit IEEE 754
char c = 'A';           // 16-bit Unicode (UTF-16 code unit)
boolean flag = true;    // JVM-dependent representation

// Boxed types: heap-allocated object wrappers
Integer boxed = Integer.valueOf(42);  // caches -128 to 127
int unboxed = boxed;                  // auto-unboxing via Integer.intValue()

// WARNING: boxed comparison with == compares REFERENCES
Integer a = 128, b = 128;
System.out.println(a == b);       // false (outside cache range)
System.out.println(a.equals(b));  // true (value comparison)
```

**Generics Erasure.** Java generics are compile-time only. The JVM sees raw
types after erasure.

```java
// Before erasure (source code):
List<String> strings = new ArrayList<>();
strings.add("hello");
String s = strings.get(0);

// After erasure (what the JVM sees):
List strings = new ArrayList();
strings.add("hello");
String s = (String) strings.get(0);  // compiler inserts cast

// Consequences of erasure:
// 1. Cannot overload on generic parameter types
// public void foo(List<String> x) {}   // erases to foo(List)
// public void foo(List<Integer> x) {}  // SAME SIGNATURE — compile error

// 2. Runtime type checks fail on parameterized types
if (strings instanceof List<String>) {}  // COMPILE ERROR (illegal generic instanceof)
if (strings instanceof List<?>) {}       // OK: unbounded wildcard

// 3. Arrays and generics don't mix
// List<String>[] array = new List<String>[10];  // COMPILE ERROR
List<?>[] array = new List<?>[10];  // OK: unbounded wildcard

// Wildcards: ? extends T (covariant / producer), ? super T (contravariant / consumer)
// PECS: Producer Extends, Consumer Super
void copy(List<? extends Number> source, List<? super Number> dest) {
    for (Number n : source) dest.add(n);
}

// Type inference (var) — Java 10+
var list = new ArrayList<String>();  // infers ArrayList<String>
var path = Path.of("/tmp");          // infers Path
// var fields = new ArrayList<>();   // COMPILE ERROR: cannot infer
```

### 1.3 Access Modifiers

| Modifier | Class | Package | Subclass | World |
|----------|-------|---------|----------|-------|
| `private` | Y | N | N | N |
| *(package-private)* | Y | Y | N | N |
| `protected` | Y | Y | Y | N |
| `public` | Y | Y | Y | Y |

```java
package com.example.core;

public class AccessDemo {
    private int priv = 1;         // only this class
    int pkgPrivate = 2;          // this package (com.example.core)
    protected int prot = 3;      // this package + subclasses (any package)
    public int pub = 4;          // everywhere
}

// Java 9+ module system adds another layer:
// module-info.java:
// module com.example.core {
//     exports com.example.core;           // public types visible
//     exports com.example.core.internal to com.example.plugin;  // qualified export
//     opens com.example.core.model to org.hibernate.orm;       // reflection access
// }
```

### 1.4 Interfaces vs Abstract Classes (Java 8+)

```java
// Interface: contract with default implementation
interface Vehicle {
    // abstract — implicitly public abstract
    void accelerate();

    // default method (Java 8+) — interface-provided implementation
    default void honk() {
        System.out.println("Beep!");
    }

    // static method (Java 8+) — utility, called via Interface name
    static Vehicle fromType(String type) {
        return switch (type) {
            case "car" -> new Car();
            case "bike" -> new Bike();
            default -> throw new IllegalArgumentException(type);
        };
    }

    // private method (Java 9+) — shared helper for default methods
    private void log(String msg) {
        System.out.println("[Vehicle] " + msg);
    }
}

// Abstract class: partial implementation with state
abstract class AbstractVehicle {
    protected String vin;  // CAN have instance state (interface CANNOT)

    AbstractVehicle(String vin) { this.vin = vin; }

    abstract void startEngine();  // subclass must implement
    void stopEngine() {           // concrete implementation with shared logic
        System.out.println("Engine stopped: " + vin);
    }
}
```

### 1.5 Sealed Classes (Java 17+)

Restrict which classes can extend/implement a type.

```java
// Sealed interface: only these three implementations allowed
sealed interface Expr permits Const, Add, Mult {
    int eval();
}

record Const(int value) implements Expr {
    public int eval() { return value; }
}

record Add(Expr left, Expr right) implements Expr {
    public int eval() { return left.eval() + right.eval(); }
}

record Mult(Expr left, Expr right) implements Expr {
    public int eval() { return left.eval() * right.eval(); }
}

// Pattern matching with sealed hierarchy — exhaustive switch
String describe(Expr e) {
    return switch (e) {
        case Const(int v) -> "Constant: " + v;
        case Add(var l, var r) -> "Add: " + l + " + " + r;
        case Mult(var l, var r) -> "Multiply: " + l + " * " + r;
        // no default needed — sealed hierarchy guarantees exhaustiveness
    };
}
```

### 1.6 Records (Java 16+)

Immutable data carriers with auto-generated constructor, accessors, equals,
hashCode, and toString.

```java
// Canonical record — one line for a complete immutable data class
record Point(int x, int y) {
    // Compact canonical constructor — validates before assignment
    Point {
        if (x < 0 || y < 0) throw new IllegalArgumentException("negative coordinates");
    }

    // Additional constructor (must delegate to canonical)
    Point() { this(0, 0); }

    // Derived accessor
    public double distance() {
        return Math.sqrt(x * x + y * y);
    }
}

var p = new Point(3, 4);
System.out.println(p.x());       // accessor, not getX()
System.out.println(p.distance()); // 5.0
System.out.println(p);           // Point[x=3, y=4]
```

### 1.7 Modern Language Features

**Switch Expressions (Java 14+):**

```java
// Expression switch — returns a value, no fall-through
String dayType = switch (dayOfWeek) {
    case MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY -> "Weekday";
    case SATURDAY, SUNDAY -> "Weekend";
};

// With blocks (yield keyword)
int result = switch (value) {
    case 1 -> 10;
    case 2 -> {
        int temp = compute();
        yield temp * 2;  // yield returns value from block
    }
    default -> 0;
};
```

**Text Blocks (Java 15+):**

```java
String json = """
    {
        "name": "%s",
        "age": %d,
        "active": true
    }
    """.formatted(name, age);
// Leading whitespace stripped to leftmost non-whitespace character.
// Trailing \\ suppresses line terminator; \s forces trailing space.
```

**Try-With-Resources (Java 7+, enhanced Java 9+):**

```java
// Java 9+: effectively-final variable works directly
var reader = Files.newBufferedReader(path);
var writer = Files.newBufferedWriter(outPath);
try (reader; writer) {  // can list multiple
    writer.write(reader.readLine());
}
// reader and writer auto-closed in reverse order
```

**Annotations:**

```java
// Built-in annotations
@Override  // compile error if not actually overriding
@Deprecated(since = "2.0", forRemoval = true)  // with metadata
@SuppressWarnings("unchecked")  // suppress compiler warnings
@FunctionalInterface  // compile error if >1 abstract method

// Custom annotation with retention and target
@Retention(RetentionPolicy.RUNTIME)  // available at runtime via reflection
@Target({ElementType.METHOD, ElementType.TYPE})  // where it can be applied
@Repeatable(Schedules.class)  // Java 8+: apply multiple times
@interface Schedule {
    String day();
    int hour() default 0;
}

@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
@interface Schedules { Schedule[] value(); }

// Annotation Processor API — compile-time code generation
// javax.annotation.processing.Processor processes @AutoService, etc.
```

---

## 2. Common Java Patterns

### 2.1 Builder Pattern

```java
// Thread-safe Builder for immutable objects
public final class ConnectionConfig {
    private final String host;
    private final int port;
    private final boolean ssl;
    private final Duration timeout;

    private ConnectionConfig(Builder builder) {
        this.host = builder.host;
        this.port = builder.port;
        this.ssl = builder.ssl;
        this.timeout = builder.timeout;
    }

    public static class Builder {
        private String host = "localhost";
        private int port = 8080;
        private boolean ssl = true;
        private Duration timeout = Duration.ofSeconds(30);

        public Builder host(String host) { this.host = host; return this; }
        public Builder port(int port) { this.port = port; return this; }
        public Builder ssl(boolean ssl) { this.ssl = ssl; return this; }
        public Builder timeout(Duration t) { this.timeout = t; return this; }

        public ConnectionConfig build() {
            if (host == null || host.isBlank())
                throw new IllegalStateException("host required");
            return new ConnectionConfig(this);
        }
    }
}

// Usage:
var config = new ConnectionConfig.Builder()
    .host("api.example.com")
    .port(443)
    .timeout(Duration.ofSeconds(10))
    .build();
```

### 2.2 Singleton — Enum-Based (Thread-Safe)

```java
// Enum singleton: JVM guarantees single instance, serialization-safe
public enum AppConfig {
    INSTANCE;

    private final Properties props;

    AppConfig() {
        props = new Properties();
        try (var in = Files.newInputStream(Path.of("config.properties"))) {
            props.load(in);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public String get(String key) { return props.getProperty(key); }
    public String get(String key, String defaultValue) {
        return props.getProperty(key, defaultValue);
    }
}

// Usage: AppConfig.INSTANCE.get("db.url")
// Compare to eager static field (also valid):
// public static final AppConfig INSTANCE = new AppConfig();
```

### 2.3 Immutable Objects

```java
// Recipe for true immutability:
// 1. final class (prevent subclass mutation)
// 2. final fields (prevent reassignment)
// 3. No setters
// 4. Defensive copies in constructor and getters for mutable fields
// 5. Or use records (already immutable by default)

public final class Order {
    private final long id;
    private final List<LineItem> items;  // mutable type!

    public Order(long id, List<LineItem> items) {
        this.id = id;
        this.items = List.copyOf(items);  // defensive copy — unmodifiable
    }

    public long id() { return id; }
    public List<LineItem> items() { return items; }  // already unmodifiable
}

// Prefer this where possible:
record Order(long id, List<LineItem> items) {
    Order {
        items = List.copyOf(items);  // compact constructor copies
    }
}
```

### 2.4 Stream API Patterns

```java
// filter → map → reduce/collect pipeline
var activeUsers = users.stream()
    .filter(u -> u.isActive())
    .sorted(Comparator.comparing(User::lastLogin).reversed())
    .limit(10)
    .toList();  // Java 16+: immutable list

// Collectors — the Swiss Army knife
Map<Department, List<Employee>> byDept = employees.stream()
    .collect(Collectors.groupingBy(Employee::department));

Map<Boolean, List<Transaction>> partitioned = transactions.stream()
    .collect(Collectors.partitioningBy(t -> t.amount() > 1000));

// groupingBy with downstream collectors
Map<Department, Double> avgSalary = employees.stream()
    .collect(Collectors.groupingBy(
        Employee::department,
        Collectors.averagingDouble(Employee::salary)
    ));

// flatMap — flatten nested structures
var allTags = articles.stream()
    .flatMap(a -> a.tags().stream())
    .distinct()
    .sorted()
    .toList();

// Parallel streams — use with caution (ForkJoinPool.commonPool())
var sum = numbers.parallelStream()
    .mapToLong(Long::longValue)
    .sum();
```

### 2.5 Optional vs null

```java
// NEVER use Optional for fields or parameters — only for return types
public Optional<User> findById(long id) {
    return Optional.ofNullable(db.get(id));  // null → Optional.empty()
}

// Consume correctly:
userRepo.findById(id)
    .map(User::email)
    .filter(e -> e.contains("@"))
    .ifPresentOrElse(
        email -> send(email),
        () -> log.warn("No valid email for user {}", id)
    );

// Flat-map for chained optionals:
public Optional<Address> getShippingAddress(Order order) {
    return order.getCustomer()
        .flatMap(Customer::getAddress);
}

// When to throw instead:
// Optional.empty() means "no result is valid" (query found nothing)
// Throw NoSuchElementException when "no result is a bug" (invariant violation)
public User getById(long id) {
    return findById(id).orElseThrow(() -> new NoSuchElementException("User " + id));
}
```

### 2.6 Dependency Injection Patterns

```java
// Constructor injection (preferred — immutable, testable)
public class OrderService {
    private final OrderRepository repo;
    private final PaymentGateway gateway;
    private final NotificationService notifier;

    public OrderService(OrderRepository repo, PaymentGateway gateway,
                        NotificationService notifier) {
        this.repo = repo;
        this.gateway = gateway;
        this.notifier = notifier;
    }
}

// Factory method pattern (used by JDK itself)
public interface HttpClient {
    static HttpClient newBuilder() {  // Java 11+
        return new HttpClientBuilderImpl();
    }
}

// SPI (Service Provider Interface) — plugin architecture
// META-INF/services/com.example.spi.Encoder lists implementations
var loaders = ServiceLoader.load(Encoder.class);
for (var encoder : loaders) {
    if (encoder.supports(format)) return encoder;
}
```

---

## 3. Build & Packaging

### 3.1 Maven

POM structure and dependency management.

```xml
<!-- pom.xml — the universal Maven descriptor -->
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.example</groupId>
    <artifactId>my-app</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>  <!-- jar, war, ear, pom -->

    <!-- Dependency scope: compile (default), provided, runtime, test, system, import -->
    <dependencies>
        <dependency>
            <groupId>com.google.guava</groupId>
            <artifactId>guava</artifactId>
            <version>33.3.0-jre</version>
        </dependency>
        <dependency>
            <groupId>org.projectlombok</groupId>
            <artifactId>lombok</artifactId>
            <version>1.18.34</version>
            <scope>provided</scope>  <!-- needed at compile, provided by container -->
        </dependency>
        <dependency>
            <groupId>org.junit.jupiter</groupId>
            <artifactId>junit-jupiter</artifactId>
            <version>5.11.0</version>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <!-- Multi-module: parent POM with <modules> -->
    <modules>
        <module>core</module>
        <module>web</module>
        <module>cli</module>
    </modules>

    <!-- Build plugins — lifecycle bindings -->
    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-compiler-plugin</artifactId>
                <version>3.13.0</version>
                <configuration>
                    <release>21</release>  <!-- --release 21, not -source/-target -->
                </configuration>
            </plugin>
        </plugins>
    </build>
</project>
```

**BOM (Bill of Materials).** Manages dependency versions across modules.

```xml
<!-- Published by framework providers (Spring Boot, Jackson, etc.) -->
<dependencyManagement>
    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-dependencies</artifactId>
            <version>3.3.1</version>
            <type>pom</type>
            <scope>import</scope>  <!-- imports all managed versions -->
        </dependency>
    </dependencies>
</dependencyManagement>
```

### 3.2 Gradle

Groovy DSL vs Kotlin DSL. Build logic in `build.gradle`/`build.gradle.kts`.

```kotlin
// build.gradle.kts — Kotlin DSL (preferred)
plugins {
    id("java")
    id("application")
    id("com.diffplug.spotless") version "6.25.0"
}

group = "com.example"
version = "1.0.0"

java {
    toolchain.languageVersion.set(JavaLanguageVersion.of(21))
}

repositories {
    mavenCentral()
}

dependencies {
    // configurations: implementation, api, compileOnly, runtimeOnly, testImplementation
    implementation("com.google.guava:guava:33.3.0-jre")
    compileOnly("org.projectlombok:lombok:1.18.34")
    annotationProcessor("org.projectlombok:lombok:1.18.34")

    testImplementation("org.junit.jupiter:junit-jupiter:5.11.0")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

application {
    mainClass.set("com.example.Main")
}
```

**Convention plugins** (`buildSrc/`). Share build logic across multi-project builds.

**Gradle vs Maven preference:**
- New projects → Gradle (faster incremental builds, cache, Kotlin DSL)
- Large enterprise with deep Maven plugin investment → Maven
- Android → Gradle (required by Android toolchain)

### 3.3 JPMS (Java Platform Module System, Java 9+)

```java
// module-info.java at the root of the module
module com.example.app {
    // What this module exports (public API)
    exports com.example.app.api;

    // What this module needs
    requires java.sql;                       // platform module
    requires com.fasterxml.jackson.databind; // named module
    requires static org.junit.jupiter.api;   // optional (compile-only)

    // Open for reflection (frameworks: Hibernate, Jackson, Spring)
    opens com.example.app.model to org.hibernate.orm;

    // Service provider
    provides com.example.spi.Plugin with com.example.app.MyPlugin;

    // Service consumer
    uses com.example.spi.Plugin;
}
```

**JLink.** Creates custom runtime images with only needed modules.
**JPackage.** Packages self-contained Java applications (native installer).

```bash
# jlink: minimal runtime image (~30MB instead of ~300MB full JDK)
jlink --module-path $JAVA_HOME/jmods:target/classes \
      --add-modules com.example.app \
      --output dist/runtime \
      --strip-debug --compress 2

# jpackage: native installer (.deb, .rpm, .dmg, .msi, .exe)
jpackage --name MyApp --input target --main-jar app.jar --main-class com.example.Main
```

---

## 4. JVM Languages

### 4.1 Kotlin

Android's preferred language. Runs on JVM, JS, and Native.

```kotlin
// Null safety — the type system distinguishes nullable and non-null
var name: String = "hello"   // non-null, can never be null
var maybe: String? = null    // nullable, must handle null

val len = maybe?.length ?: 0                // safe call + elvis
val upper = maybe?.let { it.uppercase() }   // scope function

// Data classes (compiler generates equals/hashCode/toString/copy/componentN)
data class User(val id: Long, val name: String, val email: String)

// Extension functions — add methods to existing types without inheritance
fun String.isEmail(): Boolean = this.contains("@") && this.contains(".")
"user@example.com".isEmail()  // true

// Coroutines — structured concurrency
suspend fun fetchUser(id: Long): User = withContext(Dispatchers.IO) {
    db.userQueries.selectById(id).executeAsOne()
}

// Reified generics — type parameter available at runtime (inline functions only)
inline fun <reified T> jsonTreeToValue(node: JsonNode): T =
    objectMapper.treeToValue(node, T::class.java)

// Object declarations — singleton without boilerplate
object AppConfig {
    val baseUrl: String by lazy { System.getenv("BASE_URL") ?: "http://localhost" }
}

// Companion objects — static members in class scope
class MyClass {
    companion object {
        fun create(): MyClass = MyClass()
    }
}
```

### 4.2 Groovy

Dynamic typing with optional static compilation. Core of the Gradle DSL.

```groovy
// Dynamic by default, @CompileStatic for performance
@CompileStatic
class Calculator {
    def add(int a, int b) { a + b }  // 'def' = Object return
}

// Closures — core Groovy idiom
def doubled = [1, 2, 3].collect { it * 2 }  // [2, 4, 6]

// Builder pattern via closures (Groovy's killer feature)
def xml = new groovy.xml.MarkupBuilder()
xml.people {
    person(id: 1) {
        name "Alice"
        age 30
    }
}

// AST Transformations — compile-time metaprogramming
@ToString(includeNames = true)
@EqualsAndHashCode
@Canonical  // shorthand for @ToString + @EqualsAndHashCode + @TupleConstructor
class Person { String name; int age }
```

### 4.3 Scala

Functional-OOP fusion on the JVM.

```scala
// Case classes — immutable data + pattern matching
case class User(id: Long, name: String, email: Option[String])

// Pattern matching + for-comprehensions
def describe(user: User): String = user match
  case User(_, name, Some(email)) => s"$name <$email>"
  case User(_, name, None)        => s"$name (no email)"

// For-comprehension (flatMap/map sugar)
val result: Option[String] = for
  user <- findUser(id)
  email <- user.email
  domain = email.split("@")(1)
yield domain

// Traits — mixin composition (like Java interfaces with implementation)
trait Logging:
  def log(msg: String): Unit = println(s"[${getClass.getSimpleName}] $msg")

class Service extends Logging:
  def work(): Unit = log("working...")  // inherited implementation

// Given/Using (Scala 3) — replaces implicits
given ordering: Ordering[User] = Ordering.by(_.name)
```

### 4.4 Clojure

Lisp on the JVM. Immutability by default, STM for concurrency.

```clojure
;; Persistent (immutable) data structures
(def users [{:id 1 :name "Alice"} {:id 2 :name "Bob"}])

;; Thread-safe with atoms, refs, agents
(def counter (atom 0))
(swap! counter inc)  ;; atomic increment across all threads

;; Macros — compile-time code generation
(defmacro unless [test & body]
  `(when (not ~test) ~@body))

;; Protocols — polymorphism without inheritance
(defprotocol Cache
  (get [this key])
  (put [this key value]))

;; Java interop — seamless
(.toUpperCase "hello")          ; "HELLO"
(Class/forName "java.util.Map") ; calls static method
```

### 4.5 J2ME / CLDC (Legacy)

Constrained profile for embedded devices. No generics, no reflection beyond
`Class.forName`, no enums, no annotations, no `String.isEmpty()`. Fixed-size
record stores for persistence (`javax.microedition.rms.RecordStore`). MIDlets
as the application model with `startApp()`/`pauseApp()`/`destroyApp()` lifecycle.
Pre-verified bytecode — class verification happens at build time, not on-device.

---

## 5. XML Structures in Java

### 5.1 Common XML Config Formats

**Maven POM.** `pom.xml` at project root. Parent POM for inheritance,
`<dependencyManagement>` for version control, `<pluginManagement>` for plugin
configs, `<profiles>` for environment-specific activation.

**Spring XML (legacy).** `applicationContext.xml` using `<bean>`,
`<constructor-arg>`, `<property>`, `<component-scan>`. Java config
(`@Configuration`, `@Bean`) is the modern replacement.

**web.xml (Jakarta EE).** Servlet container descriptor. Maps servlets to URL
patterns, declares filters and listeners, sets session timeout. Modern Spring
Boot apps use `WebApplicationInitializer` or auto-configuration instead.

**persistence.xml (JPA).** Database connection and entity mapping config.
`<persistence-unit>` with provider, data source, and entity listing.

**log4j2.xml.** Structured logging configuration. Appenders (Console, File,
RollingFile), loggers with levels, filters, pattern layouts.

**AndroidManifest.xml.** Declares activities, services, permissions, intent
filters, and hardware features for Android apps.

### 5.2 XML Processing APIs

```java
// DOM — tree model, loads entire document into memory
DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
Document doc = factory.newDocumentBuilder().parse(file);
NodeList nodes = doc.getElementsByTagName("item");
for (int i = 0; i < nodes.getLength(); i++) {
    Element elem = (Element) nodes.item(i);
    String value = elem.getAttribute("id");
}

// SAX — event-driven, low memory, no random access
SAXParser parser = SAXParserFactory.newInstance().newSAXParser();
parser.parse(file, new DefaultHandler() {
    @Override
    public void startElement(String uri, String local, String qName, Attributes atts) {
        if ("item".equals(qName)) { /* handle */ }
    }
});

// StAX — pull parser, streaming with cursor control
XMLInputFactory f = XMLInputFactory.newInstance();
XMLEventReader reader = f.createXMLEventReader(new FileInputStream(file));
while (reader.hasNext()) {
    XMLEvent event = reader.nextEvent();
    if (event.isStartElement() && event.asStartElement().getName()
            .getLocalPart().equals("item")) {
        // process item
    }
}

// JAXB — XML ↔ POJO binding (Java 8-10, removed in 11+, external dependency)
@XmlRootElement(name = "person")
@XmlAccessorType(XmlAccessType.FIELD)
public class Person {
    @XmlAttribute private long id;
    @XmlElement private String name;
}

JAXBContext ctx = JAXBContext.newInstance(Person.class);
Person p = (Person) ctx.createUnmarshaller().unmarshal(file);
ctx.createMarshaller().marshal(p, System.out);
```

### 5.3 XML Security

**XXE (XML External Entity) Injection.** Default parsers resolve external
entities. Must be disabled.

```java
// Secure parser configuration — THE RIGHT WAY
DocumentBuilderFactory dbf = DocumentBuilderFactory.newInstance();
dbf.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
dbf.setFeature("http://xml.org/sax/features/external-general-entities", false);
dbf.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
dbf.setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false);
dbf.setXIncludeAware(false);
dbf.setExpandEntityReferences(false);

// Same for SAX, StAX, transformer factories
// Billion Laughs attack: recursive entity expansion
// <?xml version="1.0"?>
// <!DOCTYPE lolz [
//   <!ENTITY lol "lol"><!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
//   <!-- ... exponentially expanding ... -->
// ]>
// Mitigated by disallow-doctype-decl above
```

---

## 6. Security Issues

### 6.1 Deserialization Vulnerabilities

The most critical Java vulnerability class. `ObjectInputStream.readObject()`
executes constructors, finalizers, and `readObject`/`readResolve` methods on the
deserialized class — gadget chains can execute arbitrary code.

```java
// VULNERABLE: blind deserialization of untrusted input
ObjectInputStream ois = new ObjectInputStream(untrustedInput);
Object obj = ois.readObject();  // RCE via gadget chain!

// ysoserial — testing tool for deserialization exploits
// Common gadgets: CommonsCollections, Spring, Groovy, JDK7u21

// Defenses:
// 1. Never deserialize untrusted data
// 2. Use a type-safe alternative (JSON, Protocol Buffers)
// 3. If unavoidable, use a whitelist filter:
ObjectInputStream ois = new ValidatingObjectInputStream(untrustedInput);
ois.accept(AllowedClass.class);  // whitelist
```

### 6.2 Injection Attacks

```java
// SQL Injection — use PreparedStatement ALWAYS
// WRONG:
String query = "SELECT * FROM users WHERE name = '" + userInput + "'";
Statement stmt = conn.createStatement();
ResultSet rs = stmt.executeQuery(query);  // SQLi: ' OR '1'='1

// RIGHT:
String query = "SELECT * FROM users WHERE name = ?";
PreparedStatement ps = conn.prepareStatement(query);
ps.setString(1, userInput);  // parameterized — safe
ResultSet rs = ps.executeQuery();

// Command injection via Runtime.exec() — NEVER with user input
// WRONG: Runtime.getRuntime().exec("ping " + userHostname);
// RIGHT: use ProcessBuilder with separate args
new ProcessBuilder("ping", userHostname).start();  // still risky

// Path traversal — normalize and validate
Path requested = baseDir.resolve(userPath).normalize();
if (!requested.startsWith(baseDir))
    throw new SecurityException("Path traversal attempt: " + userPath);

// Log injection (CRLF) — sanitize log input
log.info("User input: {}", userInput.replaceAll("[\r\n]", "_"));
```

### 6.3 Cryptography & Randomness

```java
// Weak randomness — java.util.Random is NOT cryptographically secure
Random weak = new Random();
int token = weak.nextInt();  // predictable — attacker can guess

// SecureRandom — use for keys, tokens, IDs
SecureRandom secure = SecureRandom.getInstanceStrong();
byte[] token = new byte[32];
secure.nextBytes(token);

// Constant-time comparison — prevents timing attacks
boolean valid = MessageDigest.isEqual(expectedHash, actualHash);

// Never use: MD5, SHA-1, DES, RC4, 3DES, ECB mode
// Use: SHA-256+, AES-GCM, ChaCha20-Poly1305
Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
// GCM requires unique IV per encryption — NEVER reuse
```

### 6.4 Trust Boundary Violations

- **RMI:** Remote Method Invocation with default configuration deserializes
  arbitrary objects. Disable or use authenticated+filtered RMI.
- **JMX:** Java Management Extensions — remote JMX without SSL+auth exposes
  arbitrary MBean invocation. Use `com.sun.management.jmxremote.authenticate=true`.
- **JNDI:** Java Naming and Directory Interface — JNDI injection (Log4Shell)
  allows remote class loading via `ldap://` URLs. Set
  `com.sun.jndi.ldap.object.trustURLCodebase=false` (default since Java 8u191).
- **Reflection:** `setAccessible(true)` bypasses access control. The module
  system (JPMS) mitigates this by requiring `--add-opens` flags.

---

## 7. Debugging & Tooling

### 7.1 Core Diagnostic Tools

```bash
# JDB — command-line debugger
jdb -attach <pid>
jdb -sourcepath src/ -classpath build/ MyClass

# Remote debugging — attach IDE to running process
# JVM flag: -agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=*:5005
jdb -connect com.sun.jdi.SocketAttach:hostname=localhost,port=5005

# Thread dump — all threads with stack traces
jstack <pid>
jcmd <pid> Thread.print
kill -3 <pid>  # sends SIGQUIT, prints thread dump to stdout

# Heap dump — snapshot of all objects in memory
jmap -dump:live,format=b,file=heap.hprof <pid>
jcmd <pid> GC.heap_dump heap.hprof

# Heap histogram — live object counts without full dump
jmap -histo:live <pid> | head -30
jcmd <pid> GC.class_histogram
```

### 7.2 JFR (Java Flight Recorder) & JMC

```bash
# Start JFR recording — low overhead ( <2%), always on in production
java -XX:StartFlightRecording:filename=recording.jfr,duration=60s ...

# Or attach to running process:
jcmd <pid> JFR.start name=profile duration=60s filename=recording.jfr
jcmd <pid> JFR.dump name=profile filename=recording.jfr
jcmd <pid> JFR.stop name=profile

# JFR captures: allocation rates, GC pauses, lock contention,
# socket I/O, thread states, exceptions, method profiling
```

### 7.3 GC Logging

```bash
# Modern GC logging (Java 9+, unified logging)
-Xlog:gc*=info:file=gc.log:time,uptime,level,tags:filecount=10,filesize=10M

# Key GC log patterns to watch:
#   [gc,start] — GC cycle beginning
#   [gc,heap]  — heap usage before/after
#   [gc,phases]— sub-phase timings (remark, cleanup, etc.)
#   pause time  — stop-the-world duration

# GC log analysis:
#   GCeasy (online), gceasy.io
#   GCViewer (offline)
```

### 7.4 Performance Issues & Diagnostics

**Memory leaks:**
- Static collections that grow without bound (`static Map<Object, Key> cache`)
- ThreadLocal values not removed in thread pools (use `remove()` in `finally`)
- Classloader leaks: webapp redeployments where the old classloader hangs on
  via a daemon thread, `ThreadLocal`, or logging framework reference

**Lock contention:**
- Check for `synchronized` on hot methods
- Use `jstack` and look for `BLOCKED` threads waiting on the same monitor
- Replace with `java.util.concurrent` (ConcurrentHashMap, ReadWriteLock,
  LongAdder instead of AtomicLong under heavy contention)

**Metaspace exhaustion:**
- Too many dynamically generated classes (reflection proxies, lambda proxies)
- Set `-XX:MaxMetaspaceSize=<N>m` as a safety limit
- Use `jcmd <pid> VM.metaspace` to inspect usage

**Finalizer queue:**
- `finalize()` is deprecated (Java 9), removed for removal (Java 18+)
- Use `Cleaner` or try-with-resources instead
- Finalizer overload causes memory pressure (objects queue up)

### 7.5 JVM Flags Reference

```bash
# Heap sizing (set both to same value in production)
-Xms2g -Xmx2g                 # initial and max heap

# Metaspace
-XX:MaxMetaspaceSize=256m     # prevent unbounded class metadata growth

# GC selection
-XX:+UseG1GC                  # default since Java 9, balanced latency/throughput
-XX:+UseZGC                   # ultra-low pause (<1ms), Java 15+, terabytes heap
-XX:+UseShenandoahGC          # low pause, concurrent compaction
-XX:+UseParallelGC            # high throughput (batch jobs)
-XX:+UseSerialGC              # single-threaded (small heaps, embedded)

# GC tuning (G1)
-XX:MaxGCPauseMillis=200      # pause target (G1 adapts to meet this)
-XX:G1HeapRegionSize=4m       # region size (1..32MB, power of 2)

# Diagnostics
-XX:+HeapDumpOnOutOfMemoryError
-XX:HeapDumpPath=/var/log/app/heap.hprof
-XX:+PrintCommandLineFlags    # log active JVM flags at startup
-XX:+ExitOnOutOfMemoryError   # restart via supervisor, don't limp
```

---

## 8. Project Structure

### 8.1 Maven Standard Layout

```text
project/
├── pom.xml                         # project descriptor
├── src/
│   ├── main/
│   │   ├── java/                   # application sources
│   │   │   └── com/example/app/
│   │   │       ├── App.java
│   │   │       ├── config/
│   │   │       ├── service/
│   │   │       ├── repository/
│   │   │       └── model/
│   │   └── resources/              # classpath resources
│   │       ├── application.properties
│   │       └── log4j2.xml
│   └── test/
│       ├── java/                   # test sources
│       │   └── com/example/app/
│       │       └── service/
│       │           └── OrderServiceTest.java
│       └── resources/              # test resources
└── target/                         # build output (gitignored)
```

### 8.2 Multi-Module Layout

```text
platform/
├── pom.xml                         # parent POM (packaging=pom)
├── platform-common/                # shared utilities
│   └── src/main/java/...
├── platform-api/                   # REST API module
│   └── src/main/java/...
├── platform-core/                  # business logic
│   └── src/main/java/...
├── platform-cli/                   # command-line tool
│   └── src/main/java/...
└── platform-integration-tests/     # cross-module tests
    └── src/test/java/...
```

### 8.3 Package Naming Conventions

```text
com.company.module.layer

Examples:
com.example.orders.controller.OrderController
com.example.orders.service.OrderService
com.example.orders.repository.OrderRepository
com.example.orders.model.Order
com.example.orders.dto.CreateOrderRequest
com.example.orders.config.OrderConfig
com.example.orders.exception.OrderNotFoundException

Layer conventions:
  controller / resource  — HTTP/REST endpoints
  service / usecase      — business logic
  repository / dao       — data access
  model / entity         — domain objects
  dto / request/response — data transfer objects
  config                 — Spring @Configuration classes
  util / common          — shared utilities
```

### 8.4 Testing Conventions

```text
src/test/java/
├── unit/                  # pure unit tests (mock collaborators)
│   └── .../OrderServiceTest.java
├── integration/           # integration tests (DB, network)
│   └── .../OrderRepositoryIT.java
└── e2e/                   # end-to-end (full stack)
    └── .../OrderApiE2ETest.java

Test framework annotations:
  @Test                   — JUnit 5 test method
  @BeforeEach/@AfterEach  — per-test setup/teardown
  @BeforeAll/@AfterAll    — per-class setup/teardown (must be static)
  @DisplayName            — human-readable test name
  @Nested                 — group related tests
  @ParameterizedTest      — run with multiple inputs
  @ExtendWith(MockitoExtension.class)  — Mockito integration
  @SpringBootTest         — full Spring context
  @WebMvcTest             — Spring MVC layer only
  @DataJpaTest            — JPA repository layer only

Key libraries:
  JUnit 5         — test framework (Jupiter API)
  Mockito         — mocking (when/thenReturn, verify)
  AssertJ         — fluent assertions (assertThat(x).isEqualTo(y))
  Testcontainers  — throwaway Docker containers for integration tests
  ArchUnit        — architecture testing (package dependency rules)
```

---

## 9. JVM Internals

### 9.1 Class File Format

Every `.class` file starts with the magic number `0xCAFEBABE`. Structure:

```text
ClassFile {
    u4             magic;              // 0xCAFEBABE
    u2             minor_version;
    u2             major_version;      // 65 = Java 21, 66 = Java 22
    u2             constant_pool_count;
    cp_info        constant_pool[constant_pool_count-1];
    u2             access_flags;
    u2             this_class;
    u2             super_class;
    u2             interfaces_count;
    u2             interfaces[interfaces_count];
    u2             fields_count;
    field_info     fields[fields_count];
    u2             methods_count;
    method_info    methods[methods_count];
    u2             attributes_count;
    attribute_info attributes[attributes_count];
}
```

```bash
# Inspect class file:
javap -v MyClass.class           # verbose: constant pool, bytecode, attributes
javap -c MyClass.class           # bytecode only
javap -p MyClass.class           # include private members
```

### 9.2 Bytecode Reference

```java
// Source code:
int add(int a, int b) { return a + b; }

// Bytecode:
// 0: iload_1        // push local var 1 (a) onto stack
// 1: iload_2        // push local var 2 (b) onto stack
// 2: iadd           // pop two ints, add, push result
// 3: ireturn        // return int from top of stack

// Key opcodes:
// aload/iload/fload/dload/lload — load reference/int/float/double/long from local vars
// astore/istore/...      — store into local vars
// invokevirtual          — dispatch on receiver's runtime type
// invokestatic           — static methods
// invokeinterface        — interface methods
// invokespecial          — constructors, private, super
// invokedynamic          — lambda, string concat (Java 7+), dynamic dispatch
// getfield/putfield      — instance field access
// getstatic/putstatic    — static field access
// ifeq/ifne/iflt/ifgt    — conditional branches
// goto                   — unconditional jump
// new                    — allocate object (not yet initialized)
// newarray/anewarray     — allocate primitive/object array
// checkcast              — verify cast at runtime
// instanceof             — type check
//
// invokedynamic is used for: lambda expressions, method references,
// string concatenation (+ with invokedynamic since Java 9), and
// record component accessors.
```

### 9.3 JIT Compilation

HotSpot profiles execution and compiles hot methods through tiers:

| Tier | Compiler | Profiling | Optimization |
|------|----------|-----------|-------------|
| 0 | Interpreter | No | None |
| 1 | C1 (client) | No | Basic (no profiling) |
| 2 | C1 | Light | Basic with invocation/backedge counters |
| 3 | C1 | Full | Full profiling (branches, types) |
| 4 | C2 (server) | No | Aggressive (inlining, escape analysis, loop unrolling) |

Methods start at tier 0. Hot methods (invocation counter + back-edge counter)
promote through tiers. C2 optimizations: inlining, escape analysis
(stack allocation of objects that don't escape), lock elision, loop
unrolling, branch prediction, dead code elimination, constant folding.

```bash
# Print compilation activity:
-XX:+PrintCompilation                    # method name + tier + size
-XX:+UnlockDiagnosticVMOptions
-XX:+PrintInlining                        # show inlining decisions
-XX:+LogCompilation                       # comprehensive XML log
```

### 9.4 Garbage Collectors

| Collector | Algorithm | Pause | Throughput | Heap | Best For |
|-----------|-----------|-------|------------|------|----------|
| Serial | Mark-sweep-compact, single-threaded | High | Low | <100MB | Embedded, small apps |
| Parallel | Mark-sweep-compact, multi-threaded | Medium | High | Any | Batch jobs, data processing |
| G1 | Regional, concurrent mark, STW young | Low (target <200ms) | Medium | 4GB-64GB | Latency-sensitive servers |
| ZGC | Colored pointers, concurrent everything | <1ms | High | 8MB-16TB | Ultra-low latency |
| Shenandoah | Brooks pointers, concurrent compaction | Low | Medium | Up to large | Low pause, Red Hat ecosystem |
| Epsilon | No-op (never collects) | N/A | N/A | Any | Testing, short-lived jobs |

```bash
# G1: default since Java 9, targets pause times via adaptive region sizing
-XX:+UseG1GC -XX:MaxGCPauseMillis=100

# ZGC: concurrent, single-generation (Java 15+), generational (Java 21+)
-XX:+UseZGC -XX:+ZGenerational

# Log GC:
-Xlog:gc*=info:file=gc.log:time,uptime:filecount=10,filesize=10M
```

### 9.5 Object Layout

In HotSpot, every object has:

```text
|-----------------------------|
| Mark Word (8 bytes on 64-bit) |
|  - identity hash code       |
|  - GC age (4 bits)          |
|  - biased lock bits         |
|  - lock state (2 bits)      |
|-----------------------------|
| Klass Pointer (4 bytes with |  ← CompressedOops (-XX:+UseCompressedOops, default on)
|  compressed class pointers) |
|-----------------------------|
| Instance Fields             |  ← fields in declaration order, aligned
|  (primitives inlined,       |
|   references as pointers)   |
|-----------------------------|
| Padding (to 8-byte boundary)|
|-----------------------------|

Array objects add a 4-byte length field after the klass pointer.

-XX:+UseCompressedOops compresses 64-bit pointers to 32 bits for heaps <32GB
-XX:+UseCompressedClassPointers compresses klass pointers (default on)
```

### 9.6 Locking

HotSpot uses a three-tier locking scheme (biased → lightweight → inflated):

1. **Biased locking** (disabled by default since Java 21): first thread to
   acquire the lock biases it to itself — no atomic operations needed on
   re-entry. Good for single-threaded usage of synchronized.

2. **Lightweight locking** (thin lock): CAS on the mark word to set a pointer
   to the lock record on the stack. No OS-level mutex. Contention → inflates.

3. **Inflated locking** (heavyweight): OS mutex (`pthread_mutex`). Contending
   threads block in the kernel. Highest overhead.

```bash
# Lock profiling:
-XX:+PrintBiasedLockingStatistics  # deprecated in 21, removed in 22
```

### 9.7 Threads

**Platform threads** (traditional): 1:1 mapping to OS threads. Each thread has
a fixed-size stack (~1MB default). `Thread.start()` creates an OS thread.

**Virtual threads** (Java 21+, final): lightweight threads managed by the JVM,
M:N scheduling onto a small pool of OS carrier threads. Cheap to create
(millions possible). Blocking I/O in a virtual thread unmounts it from the
carrier, freeing the carrier for other virtual threads.

```java
// Virtual threads — structured concurrency
try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
    var future1 = executor.submit(() -> fetchFromDB(id));
    var future2 = executor.submit(() -> fetchFromCache(id));
    // Both run concurrently on carrier threads
    return Stream.of(future1, future2)
        .map(f -> {
            try { return f.get(); }
            catch (Exception e) { throw new RuntimeException(e); }
        })
        .filter(Objects::nonNull)
        .findFirst()
        .orElseThrow();
}

// Thread.ofVirtual() — direct creation
Thread vthread = Thread.ofVirtual()
    .name("worker")
    .start(() -> processJob(job));
```

### 9.8 Ahead-of-Time Compilation

**GraalVM native-image.** Compiles Java to a standalone native executable
(no JVM required at runtime). Benefits: instant startup, lower memory.
Costs: closed-world analysis (no dynamic class loading), slower peak throughput
(no JIT profiling), longer build times.

```bash
# Build native image:
native-image -jar app.jar --no-fallback -o app

# Generate configuration for reflection, resources, etc.
java -agentlib:native-image-agent=config-output-dir=META-INF/native-image -jar app.jar
```

**Class Data Sharing (CDS).** Share class metadata across JVM processes.

```bash
# Create shared archive:
java -Xshare:dump -XX:SharedArchiveFile=app-cds.jsa -cp app.jar

# Use archive:
java -Xshare:on -XX:SharedArchiveFile=app-cds.jsa -jar app.jar
```

**AppCDS** (Application CDS): extends CDS to application classes, not just JDK.
**Leyden** project: aims to shift computation from runtime to build time
(constraining dynamism for faster startup, static images).

---

## Appendix: Quick Reference Cards

### JVM Flags Quick Reference

| Flag | Purpose |
|------|---------|
| `-Xms<N>g/m` | Initial heap size |
| `-Xmx<N>g/m` | Maximum heap size |
| `-Xss<N>k/m` | Thread stack size |
| `-XX:MaxMetaspaceSize=<N>m` | Max class metadata |
| `-XX:+UseG1GC` | G1 garbage collector |
| `-XX:+UseZGC` | ZGC (low latency) |
| `-XX:MaxGCPauseMillis=<N>` | G1 pause target |
| `-XX:+HeapDumpOnOutOfMemoryError` | Dump heap on OOM |
| `-XX:HeapDumpPath=<path>` | Heap dump location |
| `-XX:OnOutOfMemoryError=<cmd>` | Execute on OOM |
| `-XX:+PrintGCDetails` | Legacy GC logging (Java 8) |
| `-Xlog:gc*:file=gc.log:time,uptime` | Unified GC logging (Java 9+) |
| `-XX:StartFlightRecording=<opts>` | Start JFR on boot |
| `-XX:+AlwaysPreTouch` | Pre-touch heap pages (less runtime latency) |
| `-Djava.security.egd=file:/dev/urandom` | Fast entropy (non-critical) |

### Maven Dependency Scopes

| Scope | Compile classpath | Test classpath | Runtime classpath | Shipped |
|-------|-------------------|----------------|-------------------|---------|
| `compile` (default) | Y | Y | Y | Y |
| `provided` | Y | Y | N | N |
| `runtime` | N | Y | Y | Y |
| `test` | N | Y | N | N |
| `system` | Y | Y | N | N |
| `import` | (dependencyManagement only) | | | |

### Gradle Configurations

| Configuration | Compile classpath | Runtime classpath | Published |
|---------------|-------------------|-------------------|-----------|
| `api` | Y | Y | Y |
| `implementation` | Y | Y | N |
| `compileOnly` | Y | N | N |
| `runtimeOnly` | N | Y | N |
| `testImplementation` | Y (test) | Y (test) | N |
| `annotationProcessor` | Y (annotation processing) | N | N |
