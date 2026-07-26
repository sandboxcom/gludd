---
name: go-expert
description: "Use when writing, debugging, reviewing, or discussing Go code, goroutines, channels, Go modules, gRPC, Go tooling, profiling, or any Go ecosystem concern. Covers concurrency, standard library, testing, performance, security, cryptography, frameworks, project layout, platform-specific builds, cgo, and common pitfalls. Trigger keywords: Go, golang, goroutine, channel, go mod, go build, go test, go vet, gRPC, protobuf, delve, pprof, TinyGo, cgo, net/http, Go module, GOPATH, GOOS, GOARCH."
---

# Go Expert

A comprehensive Go reference for writing, reviewing, debugging, and reasoning
about Go code. This skill IS the knowledge — every section carries executable
code that demonstrates correct idioms, common pitfalls, and the library
surface you reach for in practice.

Every code block is runnable Go (unless marked as pseudo-code). Prefer the
pattern shown here over inventing your own — Go's ecosystem has converged on
these conventions through a decade of production use.

---

## 1. Go Language Core

### 1.1 Zero Values

Every type has a zero value — the default when a variable is declared but not
initialized. Go never leaves memory uninitialized, and this property is the
foundation of many patterns (nil channels in select, nil slices as empty,
zero-valued structs as usable defaults).

```go
var i int           // 0
var f float64       // 0.0
var b bool          // false
var s string        // "" (empty string, never nil)
var p *int          // nil
var sl []int        // nil, len=0, cap=0 — append, range, len() all work
var m map[string]int // nil — READ returns zero value; WRITE panics
var ch chan int     // nil — blocks forever on send and receive
var fn func()       // nil — calling it panics
var iface io.Reader // nil interface: both type and value are nil
var st MyStruct     // all fields zeroed recursively
```

Nil-slice and nil-map behaviour is a frequent source of bugs:

```go
var m map[string]int
v := m["missing"]   // 0 (zero value), no panic
m["key"] = 1        // PANIC: assignment to entry in nil map

var sl []int
sl = append(sl, 1)  // OK: append creates backing array
for _, v := range sl {} // OK: zero iterations
n := len(sl)        // 0, no panic
```

Nil channels are deliberate design tools:

```go
var ch chan int
select {
case ch <- 1:  // never selected — nil channel blocks forever
default:
    fmt.Println("nil channel disables the send case")
}
// Disabling a select case dynamically:
var done chan struct{} // nil, so <-done never fires
select {
case <-done:     // disabled
case <-ticker.C:
    fmt.Println("tick")
}
```

### 1.2 Short Variable Declarations

`:=` declares and initializes in one statement. Re-declaration is legal in
multi-variable `:=` when at least one variable is new _in the same scope_.

```go
// Basic
x := 42               // var x int = 42
name := r.FormValue("name")

// Multi-variable — at least one must be new in this scope
f, err := os.Open("f1")
g, err := os.Open("f2")  // err is re-declared, g is new; LEGAL

// Shadowing is a common bug — := creates a NEW variable in inner scope
var client *http.Client
if debug {
    client, err := newDebugClient() // shadows outer client!
    _ = client
}
fmt.Println(client) // nil — the outer client was never assigned
```

Use `var` when the zero value is the correct starting state, or when you need
to declare without initializing. Use `:=` for initialization. Never use `var x
= expr` — that is `x := expr`.

```go
// Prefer var for zero-value initialization
var buf bytes.Buffer    // zero value is ready to use
var mu sync.Mutex       // zero value is ready to use
var wg sync.WaitGroup   // zero value is ready to use

// Prefer := for expression initialization
start := time.Now()
items := fetchItems(ctx)
```

### 1.3 Named Return Values

Named return values act as local variables in the function body and are
initialized to their zero values. A bare `return` returns them as-is.

```go
func split(sum int) (x, y int) {
    x = sum * 4 / 9
    y = sum - x
    return  // bare return: returns x, y
}
```

Named returns interact with `defer` in subtle but useful ways:

```go
func readFile(path string) (content []byte, err error) {
    f, err := os.Open(path)
    if err != nil {
        return nil, err  // explicit return
    }
    defer func() {
        if closeErr := f.Close(); closeErr != nil && err == nil {
            err = closeErr  // modify named return; caller sees this error
        }
    }()
    return io.ReadAll(f)
}
```

Pitfalls:

```go
// Shadowing — the inner err hides the named return
func bad() (result int, err error) {
    result, err := compute()  // CREATES new result and err, shadows named ones
    return  // bare return uses the NAMED ones — always (0, nil)!
}

// Fixed — don't shadow
func good() (result int, err error) {
    var e error
    result, e = compute() // e is local; result and err are named returns
    if e != nil {
        err = e
    }
    return
}
```

### 1.4 Blank Identifier

`_` discards values the compiler requires but you don't need.

```go
// Unused import with side effects
import _ "net/http/pprof"  // registers handlers via init()

// Unused variable
for _, v := range items {  // index discarded
    process(v)
}

// Compile-time interface check (preferred over runtime panic)
var _ http.Handler = (*MyHandler)(nil)  // MyHandler implements http.Handler
var _ json.Marshaler = (*MyType)(nil)

// Discard return values you genuinely don't need
f, _ := os.Open("file")  // only need the file, not the error
// ⚠️ Never discard errors in production code — use for throwaway scripts only
```

### 1.5 Iota

`iota` is a predeclared identifier in const declarations. It increments by 1
on each `const` spec, resetting to 0 at each `const` keyword.

```go
// Basic enumeration
type Priority int
const (
    PriorityLow    Priority = iota // 0
    PriorityMedium                  // 1
    PriorityHigh                    // 2
)

// Bitmask with iota
type Permission uint8
const (
    PermRead  Permission = 1 << iota // 1  (0001)
    PermWrite                        // 2  (0010)
    PermExec                         // 4  (0100)
    PermAdmin                        // 8  (1000)
)
func (p Permission) Has(flag Permission) bool {
    return p&flag != 0
}

// Skip values
const (
    _   = iota // skip 0
    KB  = 1 << (10 * iota) // 1 << 10 = 1024
    MB                     // 1 << 20 = 1048576
    GB                     // 1 << 30
)

// Multiple iota expressions per line
const (
    a, b = iota, iota + 1 // 0, 1
    c, d                  // 1, 2
    e, f                  // 2, 3
)
```

### 1.6 Defer

`defer` schedules a function call to execute when the surrounding function
returns. Arguments to the deferred function are evaluated at the `defer`
statement — NOT when the function executes.

```go
// Argument evaluation time
func example() {
    i := 0
    defer fmt.Println(i) // i evaluated NOW (0), prints "0" at return
    i++
    // returns, prints 0
}
// Fix with closure if you need the final value:
func fixed() {
    i := 0
    defer func() { fmt.Println(i) }() // reads i when function executes
    i++
    // returns, prints 1
}
```

Execution order is LIFO (stack).

```go
func lifo() {
    defer fmt.Println("1")
    defer fmt.Println("2")
    defer fmt.Println("3")
    // prints: 3, 2, 1
}
```

Common patterns:

```go
// Resource cleanup
func processFile(path string) error {
    f, err := os.Open(path)
    if err != nil {
        return err
    }
    defer f.Close()
    return process(f)
}

// Mutex unlock
func (c *Cache) Get(key string) (Value, bool) {
    c.mu.Lock()
    defer c.mu.Unlock()
    return c.data[key]
}

// Recovery in goroutines
go func() {
    defer func() {
        if r := recover(); r != nil {
            log.Printf("goroutine panicked: %v", r)
        }
    }()
    doDangerousWork()
}()

// Measuring function duration
func trace(name string) func() {
    start := time.Now()
    log.Printf("enter %s", name)
    return func() { log.Printf("exit %s (%s)", name, time.Since(start)) }
}
func myFunc() {
    defer trace("myFunc")()  // note the extra () — trace returns a func
    // ...
}
```

### 1.7 Panic and Recover

`recover` only works inside a deferred function; it returns the value passed to
`panic`. It returns `nil` when no panic is in progress.

```go
func safeCall(fn func()) (err error) {
    defer func() {
        if r := recover(); r != nil {
            err = fmt.Errorf("panic: %v", r)
        }
    }()
    fn()
    return nil
}
```

**Recover does not propagate across goroutines.** A panic in a goroutine kills
the entire program if not recovered inside that goroutine.

```go
// WRONG — the main goroutine's defer cannot catch this panic
defer func() { recover() }()
go func() {
    panic("boom") // kills the program
}()

// RIGHT — recover inside the goroutine
go func() {
    defer func() { recover() }()
    panic("contained")
}()
```

Sentinel panics — prefer errors for expected failures, reserve panics for
programmer errors (invariant violations, impossible states):

```go
// Panic for invariant violations — things that should never happen
func divide(a, b int) int {
    if b == 0 {
        panic("divide by zero: invariant violation")
    }
    return a / b
}

// Error for expected failures — things that can happen in normal operation
func readConfig(path string) (Config, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return Config{}, fmt.Errorf("readConfig %s: %w", path, err)
    }
    return parse(data)
}
```

### 1.8 Init Functions

Each file can have multiple `init()` functions. Within a package, they run in
source-file order (lexicographic by filename). Across packages, they run in
dependency order: a package's deps' `init()` runs first, then its own.

```go
// package a imports package b
// order: b.init(), a.init()

var globalMap map[string]int

func init() {
    globalMap = make(map[string]int)
    globalMap["initialized"] = 1
}
```

Avoid `init()` except for: registering database drivers, registering HTTP
handlers (pprof), initializing package-level state that cannot be expressed as
a `var` declaration. Prefer explicit `New()` constructors over init-side-effects.

### 1.9 Build Tags

Go 1.17+ uses `//go:build` syntax. The old `// +build` syntax is still
supported but deprecated.

```go
//go:build linux && amd64
// +build linux,amd64

package mypkg

//go:build !windows
package mypkg

//go:build go1.18
package mypkg

//go:build integration
package mypkg // test file using //go:build integration

//go:build ignore
package main // file excluded from build entirely
```

Common `GOOS` values: `linux`, `darwin`, `windows`, `freebsd`, `openbsd`,
`netbsd`, `dragonfly`, `solaris`, `illumos`, `plan9`, `aix`, `js`, `wasip1`,
`android`, `ios`.

Common `GOARCH` values: `amd64`, `386`, `arm`, `arm64`, `mips`, `mips64`,
`ppc64`, `ppc64le`, `riscv64`, `s390x`, `wasm`.

### 1.10 Structs

```go
type User struct {
    ID        int64     `json:"id" db:"user_id"`
    Name      string    `json:"name" db:"name"`
    Email     string    `json:"email,omitempty" db:"email"`
    CreatedAt time.Time `json:"created_at" db:"created_at"`
    password  string    // unexported: invisible to encoding/json, encoding/xml, etc.
}
```

Struct tags are arbitrary string metadata accessed via `reflect`:

```go
type Config struct {
    Host string `env:"HOST" default:"localhost"`
    Port int    `env:"PORT" default:"8080"`
}
func parseEnv(v any) {
    t := reflect.TypeOf(v).Elem()
    for i := 0; i < t.NumField(); i++ {
        field := t.Field(i)
        envKey := field.Tag.Get("env")
        def := field.Tag.Get("default")
        _ = envKey
        _ = def
    }
}
```

Embedding (not inheritance — composition):

```go
type Logger struct{}

func (l Logger) Info(msg string) { fmt.Println("INFO:", msg) }

type Server struct {
    Logger                    // promoted: Server.Info(...) calls Logger.Info
    addr   string
}
s := Server{addr: ":8080"}
s.Info("starting")  // promoted method call

// Embedding an interface in a struct creates a decorator pattern
type ReadWriter struct {
    io.Reader
    io.Writer
}
// ReadWriter satisfies io.ReadWriter if both fields are set
```

### 1.11 Interfaces

Go interfaces are satisfied implicitly. Any type that has the methods satisfies
the interface — no `implements` keyword.

```go
type Writer interface {
    Write([]byte) (int, error)
}

// os.File, bytes.Buffer, net.Conn all implement Writer — no declaration needed

// Interface segregation — small interfaces composed
type Reader interface{ Read([]byte) (int, error) }
type Closer interface{ Close() error }
type ReadCloser interface {
    Reader
    Closer
}
```

The nil-interface vs nil-concrete-value distinction — a classic Go bug:

```go
type MyError struct{ msg string }
func (e *MyError) Error() string { return e.msg }

func doWork() error {
    var err *MyError  // nil concrete value
    return err        // returns a NON-nil error interface!
    // The interface has type=*MyError, value=nil — not equal to nil
}

func main() {
    err := doWork()
    if err != nil {
        fmt.Println("error:", err) // THIS PRINTS — err is not nil!
    }
}

// Fix: return nil, not a typed nil pointer
func doWorkFixed() error {
    return nil  // explicitly nil
}
// Or:
func doWorkFixed2() error {
    var err *MyError
    if err != nil {
        return err
    }
    return nil
}
```

Type assertions and type switches:

```go
// Type assertion (panics if wrong type)
w := v.(io.Writer)

// Safe type assertion (ok pattern)
w, ok := v.(io.Writer)
if !ok {
    fmt.Println("not a Writer")
}

// Type switch
switch x := v.(type) {
case nil:
    fmt.Println("nil")
case int:
    fmt.Println("int:", x)
case string:
    fmt.Println("string:", x)
case io.Reader:
    data, _ := io.ReadAll(x)
    fmt.Println("reader:", len(data))
default:
    fmt.Printf("unknown type: %T\n", x)
}

// Type switch with fallthrough-like logic
switch x := v.(type) {
case int, int8, int16, int32, int64:
    fmt.Println("signed integer:", reflect.TypeOf(x))
case uint, uint8, uint16, uint32, uint64:
    fmt.Println("unsigned integer:", reflect.TypeOf(x))
}
```

### 1.12 Generics (Go 1.18+)

Type parameters use `[T any]` syntax. The `any` constraint is an alias for
`interface{}`. Constraints can be `interface` types with type elements.

```go
// Simple generic function
func First[T any](slice []T) (T, bool) {
    if len(slice) == 0 {
        var zero T
        return zero, false
    }
    return slice[0], true
}

// Generic data structure
type Set[T comparable] struct {
    data map[T]struct{}
}
func NewSet[T comparable]() *Set[T] {
    return &Set[T]{data: make(map[T]struct{})}
}
func (s *Set[T]) Add(v T) {
    s.data[v] = struct{}{}
}
func (s *Set[T]) Contains(v T) bool {
    _, ok := s.data[v]
    return ok
}
```

Constraints:

```go
// The ~ operator matches any type whose UNDERLYING type matches
type Unsigned interface {
    ~uint | ~uint8 | ~uint16 | ~uint32 | ~uint64
}
type MyUint uint  // MyUint satisfies Unsigned because underlying is uint

// Ordered is built-in (golang.org/x/exp/constraints or cmp.Ordered in Go 1.21+)
func Max[T cmp.Ordered](a, b T) T {
    if a > b {
        return a
    }
    return b
}

// Custom constraint with method set
type Stringer interface {
    ~string | ~[]byte
    String() string
}

// Type inference works for most calls
m := Max(3, 5)      // T inferred as int
s := Max("a", "b")  // T inferred as string
// m := Max(3, 5.0) // error: default types int and float64 mismatch
m = Max(float64(3), 5.0) // explicit conversion, T inferred as float64
```

### 1.13 Error Handling

Go errors are values. The `error` interface has one method: `Error() string`.

```go
// Sentinel errors — fixed values, check with errors.Is
var ErrNotFound = errors.New("not found")
var ErrPermission = errors.New("permission denied")

// Error types — custom structs, check with errors.As
type ValidationError struct {
    Field string
    Value any
}
func (e *ValidationError) Error() string {
    return fmt.Sprintf("validation: %s=%v", e.Field, e.Value)
}

// Wrapping with %w (exactly one %w per format string)
func getUser(id int) (*User, error) {
    u, err := db.GetUser(id)
    if err != nil {
        return nil, fmt.Errorf("getUser %d: %w", id, err)
    }
    return u, nil
}
```

`errors.Is` unwraps the chain checking each error for equality:

```go
if errors.Is(err, ErrNotFound) {
    // handles ErrNotFound at any depth in the wrapped chain
}
```

`errors.As` unwraps the chain checking for type assignability:

```go
var valErr *ValidationError
if errors.As(err, &valErr) {
    fmt.Println("bad field:", valErr.Field)
}
```

Opaque errors (no sentinel, no type check — just behavioural):

```go
func IsTemporary(err error) bool {
    type temporary interface{ Temporary() bool }
    var t temporary
    return errors.As(err, &t) && t.Temporary()
}
```

Never inspect `err.Error()` string to make decisions — it's for humans, not
control flow. Use `errors.Is` and `errors.As` instead.

### 1.14 Context

Context carries deadlines, cancellation signals, and request-scoped values
across API boundaries. The first parameter convention is universal in Go.

```go
// Creation
ctx := context.Background()  // root context, never cancelled, no deadline
ctx := context.TODO()        // placeholder: use when unsure which context to use

// Cancellation
ctx, cancel := context.WithCancel(parentCtx)
defer cancel()               // ALWAYS call cancel to release resources

// Timeouts
ctx, cancel := context.WithTimeout(parentCtx, 5*time.Second)
defer cancel()

// Deadlines
ctx, cancel := context.WithDeadline(parentCtx, time.Now().Add(30*time.Second))
defer cancel()

// Values — use sparingly, only for request-scoped data (trace IDs, not optional args)
type contextKey string
const traceIDKey contextKey = "traceID"
ctx = context.WithValue(ctx, traceIDKey, "abc123")
```

Never store context in a struct; pass it as the first parameter:

```go
// RIGHT
func (s *Server) Handle(ctx context.Context, req *Request) (*Response, error) { ... }

// WRONG
type Server struct {
    ctx context.Context // NEVER do this
}
```

The `ctx.Done()` channel is the standard cancellation mechanism:

```go
func process(ctx context.Context, input <-chan Item) error {
    for {
        select {
        case <-ctx.Done():
            return ctx.Err() // context.Canceled or context.DeadlineExceeded
        case item, ok := <-input:
            if !ok {
                return nil
            }
            handle(item)
        }
    }
}
```

---

## 2. Concurrency Patterns

### 2.1 Goroutines

Goroutines are lightweight threads managed by the Go runtime with M:N
scheduling. They start with a ~2KB stack that grows and shrinks as needed.

```go
go func() {
    fmt.Println("running in a goroutine")
}()

// Common: fire and forget with recovery
go func() {
    defer func() {
        if r := recover(); r != nil {
            log.Printf("goroutine panic: %v\n%s", r, debug.Stack())
        }
    }()
    doWork()
}()
```

Goroutines have no identity — no thread-local storage, no goroutine ID in the
standard library. Pass data explicitly:

```go
// WRONG
var user string
go func() { fmt.Println(user) }() // reads shared variable

// RIGHT
go func(user string) { fmt.Println(user) }(user)
```

Goroutine leaks — any goroutine blocked on a channel that never receives or
sends will live forever:

```go
// LEAK: this goroutine blocks sending to ch forever
ch := make(chan int)
go func() { ch <- 42 }()
// ch is never received — goroutine leaks

// Leak detection at program exit:
// import _ "net/http/pprof"
// go tool pprof http://localhost:6060/debug/pprof/goroutine
```

### 2.2 Channels

Channels are typed conduits. Unbuffered channels (`make(chan T)`) require
simultaneous sender and receiver — a synchronous handoff. Buffered channels
(`make(chan T, N)`) hold up to N elements before blocking sends.

```go
ch := make(chan int)      // unbuffered: synchronous
ch := make(chan int, 10)  // buffered: capacity 10, async up to capacity

// Send
ch <- 42

// Receive
v := <-ch
v, ok := <-ch  // ok is false if channel is closed and drained

// Range — loops until channel is closed (used by receiver)
for v := range ch {
    process(v)
}

// Close — sender MUST close; receiver must not
close(ch)

// Send on closed channel → panic
// Receive from closed channel → zero value, ok=false (after drain)
```

Channel direction types document intent and prevent bugs:

```go
func producer(out chan<- int) {       // send-only
    for i := 0; i < 10; i++ {
        out <- i
    }
    close(out)
}

func consumer(in <-chan int) {        // receive-only
    for v := range in {
        fmt.Println(v)
    }
}
```

The nil-channel trick: `select` never selects a nil channel. This enables
dynamic case enable/disable.

```go
var in <-chan int = nil  // disabled
for {
    select {
    case v := <-in:
        process(v)
    case <-ctx.Done():
        return
    }
}
```

### 2.3 Select

`select` blocks until one of its cases can proceed, then executes it. If
multiple cases are ready, one is chosen pseudo-randomly (uniform).

```go
select {
case v := <-ch1:
    fmt.Println("ch1:", v)
case ch2 <- 42:
    fmt.Println("sent to ch2")
case <-time.After(5 * time.Second):
    fmt.Println("timeout")
}
```

The `default` case makes `select` non-blocking:

```go
select {
case v := <-ch:
    fmt.Println("received:", v)
default:
    fmt.Println("no value available")
}
// Polling with default (busy-wait — use sparingly)
for {
    select {
    case v := <-ch:
        process(v)
    default:
        // do other work
    }
}
```

The random selection is not round-robin. Do not rely on ordering between ready
cases — if you need fairness, implement it explicitly.

### 2.4 Sync Primitives

**`sync.Mutex`** — mutual exclusion for shared state:

```go
type Cache struct {
    mu    sync.Mutex
    items map[string]Item
}

func (c *Cache) Get(key string) (Item, bool) {
    c.mu.Lock()
    defer c.mu.Unlock()
    item, ok := c.items[key]
    return item, ok
}
```

**`sync.RWMutex`** — multiple readers, exclusive writer. Readers hold read lock
(`RLock`/`RUnlock`), which allows concurrency among readers:

```go
type SafeMap struct {
    mu   sync.RWMutex
    data map[string]string
}

func (m *SafeMap) Get(key string) string {
    m.mu.RLock()
    defer m.mu.RUnlock()
    return m.data[key]
}

func (m *SafeMap) Set(key, value string) {
    m.mu.Lock()
    defer m.mu.Unlock()
    m.data[key] = value
}

// Two-reader rule: RWMutex may prefer writers, starving readers.
// Use sync.Mutex if writes are frequent — the overhead of the
// read-write bookkeeping can exceed the benefit.
```

**`sync.WaitGroup`** — wait for a collection of goroutines:

```go
var wg sync.WaitGroup
for _, url := range urls {
    wg.Add(1) // increment BEFORE launching goroutine
    go func(url string) {
        defer wg.Done() // decrement in defer
        fetch(url)
    }(url)
}
wg.Wait() // blocks until counter reaches zero
```

**`sync.Once`** — execute exactly once:

```go
var (
    once   sync.Once
    client *http.Client
)

func getClient() *http.Client {
    once.Do(func() {
        client = &http.Client{Timeout: 10 * time.Second}
    })
    return client
}
```

**`sync/atomic`** — lock-free operations for simple counters and flags:

```go
var counter int64

func increment() {
    atomic.AddInt64(&counter, 1)
}

func get() int64 {
    return atomic.LoadInt64(&counter)
}

// Compare-and-swap (CAS) for state transitions
var state int32
const (
    stateIdle    int32 = 0
    stateRunning int32 = 1
)

func startIfIdle() bool {
    return atomic.CompareAndSwapInt32(&state, stateIdle, stateRunning)
}
```

**`sync.Map`** — concurrent map with two valid use cases:

```go
// Case 1: entry written once, read many times
var cache sync.Map
cache.Store("key", expensiveValue)
v, ok := cache.Load("key")

// Case 2: disjoint key sets per goroutine
// (each goroutine writes different keys)
// For all other cases, use map + sync.Mutex

// Range over sync.Map
cache.Range(func(key, value any) bool {
    fmt.Printf("%v: %v\n", key, value)
    return true // continue iteration
})
```

**`sync.Cond`** — goroutine waiting on a condition:

```go
var mu sync.Mutex
cond := sync.NewCond(&mu)
ready := false

// Waiter
go func() {
    cond.L.Lock()
    for !ready { // ALWAYS check condition in a loop
        cond.Wait() // releases lock, waits for Signal/Broadcast, re-acquires
    }
    // ready is true
    cond.L.Unlock()
}()

// Signaler
cond.L.Lock()
ready = true
cond.Broadcast() // wake all waiters
cond.L.Unlock()
```

### 2.5 Errgroup

`golang.org/x/sync/errgroup` runs parallel work with error propagation:

```go
g, ctx := errgroup.WithContext(ctx)

for _, url := range urls {
    url := url // capture for closure (or Go 1.22+ does this automatically)
    g.Go(func() error {
        return fetch(ctx, url)
    })
}

if err := g.Wait(); err != nil {
    log.Printf("at least one fetch failed: %v", err)
}

// With concurrency limit
g.SetLimit(10) // max 10 concurrent goroutines
```

### 2.6 Worker Pool and Semaphore Patterns

**Counting semaphore** with a buffered channel:

```go
sem := make(chan struct{}, 10) // max 10 concurrent
for _, item := range items {
    item := item
    sem <- struct{}{} // acquire
    go func() {
        defer func() { <-sem }() // release
        process(item)
    }()
}
// Wait for all to finish: fill the semaphore
for i := 0; i < cap(sem); i++ {
    sem <- struct{}{}
}
close(sem)
```

**Worker pool** with input channel:

```go
jobs := make(chan Job, 100)
var wg sync.WaitGroup

// Start fixed pool of workers
for w := 0; w < runtime.NumCPU(); w++ {
    wg.Add(1)
    go func() {
        defer wg.Done()
        for job := range jobs { // range exits when channel closed
            job.Process()
        }
    }()
}

// Send jobs
for _, job := range allJobs {
    jobs <- job
}
close(jobs) // signal workers to exit after draining
wg.Wait()
```

### 2.7 Pipeline Patterns

**Fan-out** — distribute work to multiple goroutines:

```go
func fanOut(ctx context.Context, input <-chan Item, workers int) []<-chan Result {
    outputs := make([]<-chan Result, workers)
    for i := 0; i < workers; i++ {
        ch := make(chan Result)
        outputs[i] = ch
        go func(out chan<- Result) {
            defer close(out)
            for item := range input {
                select {
                case out <- process(item):
                case <-ctx.Done():
                    return
                }
            }
        }(ch)
    }
    return outputs
}
```

**Fan-in** — merge multiple channels into one:

```go
func fanIn(ctx context.Context, channels ...<-chan Result) <-chan Result {
    var wg sync.WaitGroup
    out := make(chan Result)

    multiplex := func(ch <-chan Result) {
        defer wg.Done()
        for v := range ch {
            select {
            case out <- v:
            case <-ctx.Done():
                return
            }
        }
    }

    wg.Add(len(channels))
    for _, ch := range channels {
        go multiplex(ch)
    }

    go func() {
        wg.Wait()
        close(out)
    }()
    return out
}
```

**Pipeline** — chain stages via channels:

```go
func pipeline() {
    naturals := make(chan int)
    squares := make(chan int)

    // Stage 1: generate
    go func() {
        for i := 0; i < 100; i++ {
            naturals <- i
        }
        close(naturals)
    }()

    // Stage 2: square
    go func() {
        defer close(squares)
        for n := range naturals {
            squares <- n * n
        }
    }()

    // Stage 3: consume
    for s := range squares {
        fmt.Println(s)
    }
}
```

### 2.8 Common Concurrency Bugs

**Loop-variable capture** — the classic Go race:

```go
// BUG — all goroutines share the same loop variable
for _, item := range items {
    go func() {
        process(item) // item is the same variable, final value used
    }()
}

// FIX (Go < 1.22): copy to a new variable
for _, item := range items {
    item := item // shadow; new variable per iteration
    go func() {
        process(item)
    }()
}

// Go 1.22+ — loop variables are per-iteration by default
```

**Unbuffered channel deadlock:**

```go
// DEADLOCK: send on unbuffered channel with no receiver ready
ch := make(chan int)
ch <- 1  // blocks forever — no goroutine to receive
fmt.Println("never reached")
```

**Closing from receiver side:**

```go
// Receiver must never close — it doesn't own the channel.
// If the receiver needs to signal "stop," use a separate done channel or ctx.Done().
```

**Send on closed channel:**

```go
ch := make(chan int)
close(ch)
ch <- 1 // PANIC: send on closed channel
```

**Data races** — detect with `-race`:

```bash
go test -race ./...
go build -race -o myapp
go run -race main.go
```

The race detector has ~10x memory overhead and cannot be used in production.
It is a development-only tool.

**Goroutine leak from unclosed channel:**

```go
// LEAK: goroutine blocks on send forever
ch := make(chan int)
go func() { ch <- work() }() // blocks when function returns
// ch never received, never closed → goroutine lives forever

// FIX: always provide a way out
go func() {
    select {
    case ch <- work():
    case <-ctx.Done():
    }
}()
```

**WaitGroup misuse:**

```go
// WRONG: Add inside goroutine may not execute before Wait
go func() {
    wg.Add(1) // race: Wait may see counter=0 and return
    defer wg.Done()
    work()
}()
wg.Wait()

// RIGHT: Add before launching goroutine
wg.Add(1)
go func() {
    defer wg.Done()
    work()
}()
wg.Wait()
```

---

## 3. Standard Library Deep Dive

### 3.1 net/http

**Handler interface** and ServeMux:

```go
type MyHandler struct{}

func (h *MyHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(http.StatusOK)
    fmt.Fprintf(w, `{"message": "hello"}`)
}

mux := http.NewServeMux()
mux.Handle("/api/", &MyHandler{})
mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
    w.WriteHeader(http.StatusOK)
    w.Write([]byte("OK"))
})
http.ListenAndServe(":8080", mux)
```

**Middleware pattern:**

```go
func loggingMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        start := time.Now()
        next.ServeHTTP(w, r)
        log.Printf("%s %s %s", r.Method, r.URL.Path, time.Since(start))
    })
}

func authMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        token := r.Header.Get("Authorization")
        if token == "" {
            http.Error(w, "unauthorized", http.StatusUnauthorized)
            return
        }
        next.ServeHTTP(w, r)
    })
}

// Chain
mux := http.NewServeMux()
mux.HandleFunc("/api/hello", helloHandler)
handler := loggingMiddleware(authMiddleware(mux))
http.ListenAndServe(":8080", handler)
```

**HTTP client with proper configuration:**

```go
client := &http.Client{
    Timeout: 30 * time.Second,
    Transport: &http.Transport{
        MaxIdleConns:        100,
        MaxIdleConnsPerHost: 10,
        IdleConnTimeout:     90 * time.Second,
        TLSHandshakeTimeout: 10 * time.Second,
        DisableCompression:  false,
    },
    CheckRedirect: func(req *http.Request, via []*http.Request) error {
        if len(via) >= 10 {
            return fmt.Errorf("too many redirects")
        }
        return nil
    },
}

resp, err := client.Get("https://api.example.com/data")
if err != nil {
    log.Fatal(err)
}
defer resp.Body.Close()
if resp.StatusCode != http.StatusOK {
    body, _ := io.ReadAll(resp.Body)
    log.Printf("unexpected status %d: %s", resp.StatusCode, body)
    return
}

var data MyData
if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
    log.Printf("decode error: %v", err)
    return
}
```

**httptest for testing:**

```go
func TestHandler(t *testing.T) {
    // Test a handler directly
    req := httptest.NewRequest("GET", "/api/hello", nil)
    w := httptest.NewRecorder()
    myHandler.ServeHTTP(w, req)

    if w.Code != http.StatusOK {
        t.Errorf("expected 200, got %d", w.Code)
    }
    var resp map[string]string
    if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
        t.Fatal(err)
    }
}

func TestClientIntegration(t *testing.T) {
    // Test against a real server
    server := httptest.NewServer(http.HandlerFunc(realHandler))
    defer server.Close()

    resp, err := http.Get(server.URL + "/api/hello")
    if err != nil {
        t.Fatal(err)
    }
    defer resp.Body.Close()
    // assert on resp
}
```

### 3.2 encoding/json

```go
type Record struct {
    Name  string `json:"name"`
    Email string `json:"email,omitempty"` // omitted if empty
    Age   int    `json:"age,string"`       // encoded as string "30"
    internal string `json:"-"`             // never marshaled/unmarshaled
}

// Marshal
data, err := json.Marshal(records)

// MarshalIndent for human-readable output
data, err := json.MarshalIndent(records, "", "  ")

// Unmarshal
var records []Record
err := json.Unmarshal(data, &records)
```

**Custom marshal/unmarshal:**

```go
func (r Record) MarshalJSON() ([]byte, error) {
    type Alias Record // avoid infinite recursion
    return json.Marshal(struct {
        Alias
        DisplayName string `json:"display_name"`
    }{
        Alias:       Alias(r),
        DisplayName: r.Name + " <" + r.Email + ">",
    })
}

func (r *Record) UnmarshalJSON(data []byte) error {
    type Alias Record
    aux := struct {
        *Alias
    }{Alias: (*Alias)(r)}
    return json.Unmarshal(data, &aux)
}
```

**Partial decode with RawMessage:**

```go
type Envelope struct {
    Type    string          `json:"type"`
    Payload json.RawMessage `json:"payload"` // defer decoding
}

var env Envelope
json.Unmarshal(data, &env)

switch env.Type {
case "user":
    var user User
    json.Unmarshal(env.Payload, &user)
case "order":
    var order Order
    json.Unmarshal(env.Payload, &order)
}
```

**Decoder for streams and strict parsing:**

```go
dec := json.NewDecoder(resp.Body)
dec.DisallowUnknownFields() // reject unknown fields
var v MyType
if err := dec.Decode(&v); err != nil {
    log.Printf("decode: %v", err)
}

// Decode stream of JSON objects (e.g., NDJSON)
dec := json.NewDecoder(reader)
for {
    var obj MyType
    if err := dec.Decode(&obj); err == io.EOF {
        break
    } else if err != nil {
        log.Printf("decode: %v", err)
        continue
    }
    process(obj)
}
```

**Encoder for streams:**

```go
enc := json.NewEncoder(w)
for _, obj := range objects {
    if err := enc.Encode(obj); err != nil {
        log.Printf("encode: %v", err)
        return
    }
}
```

### 3.3 database/sql

```go
// Open the pool — does NOT connect; first query will
db, err := sql.Open("postgres", "postgres://user:pass@localhost/db?sslmode=disable")
if err != nil {
    log.Fatal(err)
}
defer db.Close()

// Pool tuning
db.SetMaxOpenConns(25)          // max simultaneous connections
db.SetMaxIdleConns(10)          // max idle connections in pool
db.SetConnMaxLifetime(5 * time.Minute)  // max lifetime per connection
db.SetConnMaxIdleTime(1 * time.Minute)  // max idle time per connection

// Ping to verify connection
if err := db.PingContext(ctx); err != nil {
    log.Fatal(err)
}
```

**Queries:**

```go
// QueryRow — single row
var name string
var created time.Time
err := db.QueryRowContext(ctx,
    "SELECT name, created_at FROM users WHERE id = $1", userID,
).Scan(&name, &created)
if errors.Is(err, sql.ErrNoRows) {
    // not found
} else if err != nil {
    // error
}

// Query — multiple rows
rows, err := db.QueryContext(ctx,
    "SELECT id, name FROM users WHERE active = $1", true,
)
if err != nil {
    return err
}
defer rows.Close() // ALWAYS close; must be called even on error
for rows.Next() {
    var id int64
    var name string
    if err := rows.Scan(&id, &name); err != nil {
        return err
    }
    fmt.Println(id, name)
}
if err := rows.Err(); err != nil { // check final error after loop
    return err
}

// Exec — INSERT/UPDATE/DELETE
result, err := db.ExecContext(ctx,
    "INSERT INTO users (name, email) VALUES ($1, $2)", name, email,
)
if err != nil {
    return err
}
id, _ := result.LastInsertId()     // not supported by postgres
rowsAffected, _ := result.RowsAffected()
```

**Transactions:**

```go
tx, err := db.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelSerializable})
if err != nil {
    return err
}
defer tx.Rollback() // no-op after Commit

_, err = tx.ExecContext(ctx,
    "UPDATE accounts SET balance = balance - $1 WHERE id = $2", amount, fromID,
)
if err != nil {
    return err
}
_, err = tx.ExecContext(ctx,
    "UPDATE accounts SET balance = balance + $1 WHERE id = $2", amount, toID,
)
if err != nil {
    return err
}

if err := tx.Commit(); err != nil {
    return err
}
// after Commit, deferred Rollback is a no-op
```

**Prepared statements** reuse query plans:

```go
stmt, err := db.PrepareContext(ctx, "SELECT name FROM users WHERE id = $1")
if err != nil {
    return err
}
defer stmt.Close()

for _, id := range ids {
    var name string
    stmt.QueryRowContext(ctx, id).Scan(&name)
}
```

**Nullable types:**

```go
var email sql.NullString
var deletedAt sql.NullTime
var credits sql.NullInt64
var rating sql.NullFloat64
var verified sql.NullBool

err := db.QueryRowContext(ctx,
    "SELECT email, deleted_at, credits FROM users WHERE id = $1", userID,
).Scan(&email, &deletedAt, &credits)

if email.Valid {
    fmt.Println("email:", email.String)
}
if deletedAt.Valid {
    fmt.Println("deleted:", deletedAt.Time)
}
```

### 3.4 io

The `io.Reader` and `io.Writer` interfaces are Go's universal I/O abstraction:

```go
type Reader interface {
    Read(p []byte) (n int, err error)
}
type Writer interface {
    Write(p []byte) (n int, err error)
}
```

Key functions:

```go
// Copy all data from reader to writer
written, err := io.Copy(dst, src)

// Copy up to N bytes
written, err := io.CopyN(dst, src, 1024)

// Read all bytes from reader
data, err := io.ReadAll(reader)

// TeeReader: reads from r and writes to w simultaneously
// Useful for logging request bodies without consuming them
var buf bytes.Buffer
tee := io.TeeReader(r.Body, &buf)
body, _ := io.ReadAll(tee)

// MultiWriter: writes to multiple writers at once
var logBuf bytes.Buffer
mwriter := io.MultiWriter(os.Stdout, &logBuf)
fmt.Fprintf(mwriter, "hello\n")

// LimitReader: read at most N bytes
limited := io.LimitReader(r.Body, 1<<20) // 1MB limit

// Pipe: in-memory synchronous pipe (writer blocks until reader reads)
pr, pw := io.Pipe()
go func() {
    defer pw.Close()
    json.NewEncoder(pw).Encode(data)
}()
var decoded MyType
json.NewDecoder(pr).Decode(&decoded)
```

### 3.5 os

File operations:

```go
// Read entire file
data, err := os.ReadFile("/path/to/file")

// Write entire file (creates or truncates)
err := os.WriteFile("/path/to/file", data, 0644)

// Open for reading
f, err := os.Open("/path/to/file")
defer f.Close()

// Open for writing (create/truncate)
f, err := os.Create("/path/to/file")
defer f.Close()

// Open with flags
f, err := os.OpenFile("/path/to/file", os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
defer f.Close()

// Stat
info, err := os.Stat("/path/to/file")
info.IsDir()
info.Mode()
info.Size()
info.ModTime()

// Directory operations
os.Mkdir("/newdir", 0755)
os.MkdirAll("/path/to/newdir", 0755) // creates parents
os.Remove("/file")
os.RemoveAll("/dir") // removes recursively

// File mode
os.Chmod("/file", 0755)
os.Chown("/file", uid, gid)

// Environment
os.Getenv("PATH")
os.Setenv("KEY", "value")
os.LookupEnv("KEY") // returns (value, exists)
os.Environ()        // []string of "KEY=VALUE"

// Temp files/dirs
f, err := os.CreateTemp("", "prefix-*")  // /tmp/prefix-abc123
dir, err := os.MkdirTemp("", "prefix-*")

// User cache/config dirs
dir, err := os.UserCacheDir()   // ~/Library/Caches (macOS)
dir, err := os.UserConfigDir()  // ~/Library/Application Support (macOS)
dir, err := os.UserHomeDir()    // ~
```

Signal handling:

```go
sigCh := make(chan os.Signal, 1) // buffer important — don't block signal delivery
signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

go func() {
    sig := <-sigCh
    log.Printf("received signal %v, shutting down", sig)
    cancel() // trigger graceful shutdown
}()

// Reset specific signal handling to default
signal.Reset(syscall.SIGINT)
```

Subprocesses with `os/exec`:

```go
// Simple command
cmd := exec.CommandContext(ctx, "git", "log", "--oneline", "-n", "5")
output, err := cmd.Output() // captures stdout, waits for completion
// ⚠️ cmd.Output() buffers all of stdout in memory — use for small output only

// Stream output (for large output)
cmd := exec.CommandContext(ctx, "long-running-process")
cmd.Stdout = os.Stdout
cmd.Stderr = os.Stderr
if err := cmd.Run(); err != nil {
    log.Printf("command failed: %v", err)
}

// Capture stdout with pipe
cmd := exec.CommandContext(ctx, "prog")
stdout, _ := cmd.StdoutPipe()
cmd.Start()
scanner := bufio.NewScanner(stdout)
for scanner.Scan() {
    process(scanner.Text())
}
cmd.Wait()

// Set environment
cmd.Env = append(os.Environ(),
    "CGO_ENABLED=0",
    "GOOS=linux",
)
```

### 3.6 crypto

```go
// Secure random (use crypto/rand, NEVER math/rand)
import "crypto/rand"

// Random bytes
buf := make([]byte, 32)
if _, err := rand.Read(buf); err != nil {
    log.Fatal(err)
}
token := hex.EncodeToString(buf)

// SHA-256
h := sha256.Sum256(data)
fmt.Printf("%x\n", h)

// HMAC
mac := hmac.New(sha256.New, key)
mac.Write(data)
signature := mac.Sum(nil)
// Verify: hmac.Equal(signature, expected)

// Constant-time comparison (for MACs, tokens, signatures)
if subtle.ConstantTimeCompare(receivedMAC, expectedMAC) != 1 {
    return errors.New("invalid MAC")
}
// NEVER use == for security-sensitive comparisons — timing attack

// AES-GCM (authenticated encryption)
func encrypt(plaintext, key []byte) ([]byte, error) {
    block, err := aes.NewCipher(key) // key must be 16 (AES-128), 24, or 32 bytes
    if err != nil {
        return nil, err
    }
    aead, err := cipher.NewGCM(block)
    if err != nil {
        return nil, err
    }
    nonce := make([]byte, aead.NonceSize())
    if _, err := rand.Read(nonce); err != nil {
        return nil, err
    }
    ciphertext := aead.Seal(nonce, nonce, plaintext, nil)
    return ciphertext, nil
}

func decrypt(ciphertext, key []byte) ([]byte, error) {
    block, err := aes.NewCipher(key)
    if err != nil {
        return nil, err
    }
    aead, err := cipher.NewGCM(block)
    if err != nil {
        return nil, err
    }
    nonceSize := aead.NonceSize()
    if len(ciphertext) < nonceSize {
        return nil, errors.New("ciphertext too short")
    }
    nonce, ciphertext := ciphertext[:nonceSize], ciphertext[nonceSize:]
    return aead.Open(nil, nonce, ciphertext, nil)
}

// Password hashing (argon2)
import "golang.org/x/crypto/argon2"
hash := argon2.IDKey([]byte(password), salt, 1, 64*1024, 4, 32)
```

### 3.7 testing

```go
func TestDivide(t *testing.T) {
    t.Helper() // attribute failures to the caller's line

    tests := []struct {
        name     string
        a, b     int
        expected int
        wantErr  bool
    }{
        {"positive", 10, 2, 5, false},
        {"negative", -10, 2, -5, false},
        {"by zero", 10, 0, 0, true},
    }

    for _, tt := range tests {
        tt := tt // capture (Go < 1.22)
        t.Run(tt.name, func(t *testing.T) {
            t.Parallel() // tests within this table run in parallel
            result, err := Divide(tt.a, tt.b)
            if tt.wantErr && err == nil {
                t.Fatal("expected error, got nil")
            }
            if !tt.wantErr && err != nil {
                t.Fatalf("unexpected error: %v", err)
            }
            if result != tt.expected {
                t.Errorf("Divide(%d, %d) = %d; want %d",
                    tt.a, tt.b, result, tt.expected)
            }
        })
    }
}

// t.Cleanup vs defer — t.Cleanup runs at test end even if t.Parallel
func TestWithTempDir(t *testing.T) {
    dir := t.TempDir() // auto-cleanup
    t.Setenv("HOME", dir) // auto-restore

    t.Cleanup(func() {
        // runs when test and all subtests finish
        log.Println("cleanup after all subtests")
    })
}
```

### 3.8 time

```go
now := time.Now()
elapsed := time.Since(start)

// ⚠️ time.After leaks — use time.NewTimer + Stop instead
// WRONG: timer cannot be stopped, leaks until it fires
select {
case <-time.After(5 * time.Second):
    fmt.Println("timeout")
}

// RIGHT: NewTimer can be stopped
timer := time.NewTimer(5 * time.Second)
defer timer.Stop()
select {
case <-timer.C:
    fmt.Println("timeout")
case result := <-work:
    if !timer.Stop() {
        <-timer.C // drain the channel
    }
}

// Ticker for periodic work
ticker := time.NewTicker(1 * time.Second)
defer ticker.Stop()
for {
    select {
    case <-ticker.C:
        doWork()
    case <-ctx.Done():
        return
    }
}

// Parsing and formatting — reference time is Mon Jan 2 15:04:05 MST 2006
// (each part is distinct: 1-2-3-4-5-6-7)
t, err := time.Parse("2006-01-02 15:04:05", "2024-03-15 14:30:00")
s := t.Format("January 2, 2006 at 3:04pm") // "March 15, 2024 at 2:30pm"
s = t.Format(time.RFC3339)                  // "2024-03-15T14:30:00Z"

// Duration
d, _ := time.ParseDuration("2h30m")
sleep := 5 * time.Second + 300*time.Millisecond
```

### 3.9 reflect

Use sparingly. Reflection is slow, panic-prone, and bypasses type safety.

```go
// TypeOf and ValueOf
t := reflect.TypeOf(x)
v := reflect.ValueOf(x)

// Struct field iteration
t := reflect.TypeOf(user)
for i := 0; i < t.NumField(); i++ {
    field := t.Field(i)
    tag := field.Tag.Get("json")
    value := v.Field(i).Interface()
    fmt.Printf("%s (json:%s) = %v\n", field.Name, tag, value)
}

// Setting a value through reflection (requires addressable value)
func setField(v any, name string, value any) error {
    rv := reflect.ValueOf(v)
    if rv.Kind() != reflect.Ptr || rv.Elem().Kind() != reflect.Struct {
        return fmt.Errorf("expected pointer to struct")
    }
    field := rv.Elem().FieldByName(name)
    if !field.IsValid() {
        return fmt.Errorf("field %s not found", name)
    }
    if !field.CanSet() {
        return fmt.Errorf("field %s cannot be set (unexported)", name)
    }
    field.Set(reflect.ValueOf(value))
    return nil
}
```

### 3.10 unsafe

Rules for `unsafe.Pointer` conversions:
1. `*T` → `unsafe.Pointer` → any pointer type — valid
2. `uintptr` → `unsafe.Pointer` — invalid (use `unsafe.Add` Go 1.17+)
3. `uintptr` is a plain integer; GC can move the object — never store
   `uintptr` across allocation boundaries

```go
// Checking struct layout
fmt.Println(unsafe.Sizeof(MyStruct{}))
fmt.Println(unsafe.Offsetof(MyStruct{}.Field))

// Go 1.17+: unsafe.Slice and unsafe.String (zero-copy conversions)
b := []byte("hello world")
s := unsafe.String(&b[0], len(b)) // byte slice → string, no copy (Go 1.20+)

s := "hello"
b := unsafe.Slice(unsafe.StringData(s), len(s)) // string → byte slice, no copy (Go 1.20+)
// ⚠️ Both are read-only — mutating the result is undefined behaviour
```

---

## 4. Testing & Benchmarking

### 4.1 Test Flags

```bash
go test ./...                         # all packages
go test -v ./...                      # verbose
go test -run TestFoo ./...            # run tests matching regex
go test -run "TestFoo/edge" ./...     # run specific subtest
go test -count=1 ./...                # disable test caching
go test -count=5 ./...                # run 5 times (flake detection)
go test -race ./...                   # data race detector
go test -cover ./...                  # coverage percentage
go test -coverprofile=cover.out ./... # write coverage profile
go test -coverpkg=./... ./...         # include all packages in coverage
go test -shuffle=on ./...             # randomize test order
go test -shuffle=1234567890 ./...     # deterministic shuffle (for replay)
go test -timeout 30s ./...            # timeout (default 10m)
go test -failfast ./...               # stop on first failure
go test -short ./...                  # skip long-running tests
go test -parallel 4 ./...             # max parallel tests
go test -json ./...                   # machine-readable output
```

### 4.2 Benchmarks

```go
func BenchmarkSHA256(b *testing.B) {
    data := []byte("hello world")
    b.ResetTimer() // exclude setup from measurement
    for i := 0; i < b.N; i++ {
        sha256.Sum256(data)
    }
}

func BenchmarkReadFile(b *testing.B) {
    for i := 0; i < b.N; i++ {
        b.StopTimer()
        os.WriteFile("/tmp/bench", []byte("data"), 0644)
        b.StartTimer()

        _, err := os.ReadFile("/tmp/bench")
        if err != nil {
            b.Fatal(err)
        }
    }
}

func BenchmarkAlloc(b *testing.B) {
    b.ReportAllocs() // report memory allocations
    for i := 0; i < b.N; i++ {
        _ = make([]byte, 1024)
    }
}
```

Run with:

```bash
go test -bench=. ./...
go test -bench=. -benchmem ./...          # memory allocations
go test -bench=. -benchtime=10s ./...     # custom duration
go test -bench=. -count=5 ./...           # multiple runs
go test -bench=. -cpuprofile=cpu.out ./.. # CPU profile
go test -bench=. -memprofile=mem.out ./.. # memory profile

# Compare benchmarks with benchstat
go test -bench=. -count=10 ./... > old.txt
# make changes
go test -bench=. -count=10 ./... > new.txt
benchstat old.txt new.txt
```

### 4.3 Fuzzing (Go 1.18+)

```go
func FuzzDivide(f *testing.F) {
    // Seed corpus
    f.Add(10, 2)
    f.Add(-10, 5)
    f.Add(0, 1)

    f.Fuzz(func(t *testing.T, a, b int) {
        if b == 0 {
            _, err := Divide(a, b)
            if err == nil {
                t.Error("expected error for divide by zero")
            }
            return
        }
        result, err := Divide(a, b)
        if err != nil {
            t.Errorf("unexpected error: %v", err)
        }
        if result*b != a {
            t.Errorf("Divide(%d, %d) = %d; not reversible", a, b, result)
        }
    })
}
```

Run with:

```bash
go test -fuzz=FuzzDivide ./...
go test -fuzz=FuzzDivide -fuzztime=30s ./...
go test -fuzz=FuzzDivide -fuzzminimizetime=10s ./...
```

### 4.4 Table-Driven Tests with t.Run

```go
func TestParsePort(t *testing.T) {
    tests := []struct {
        name      string
        input     string
        wantPort  int
        wantError bool
    }{
        {name: "valid port", input: ":8080", wantPort: 8080, wantError: false},
        {name: "no colon", input: "8080", wantPort: 0, wantError: true},
        {name: "negative port", input: ":-1", wantPort: 0, wantError: true},
        {name: "too large", input: ":65536", wantPort: 0, wantError: true},
    }

    for _, tt := range tests {
        tt := tt
        t.Run(tt.name, func(t *testing.T) {
            t.Parallel()
            port, err := ParsePort(tt.input)
            if tt.wantError && err == nil {
                t.Fatal("expected error")
            }
            if !tt.wantError && err != nil {
                t.Fatalf("unexpected error: %v", err)
            }
            if port != tt.wantPort {
                t.Errorf("got %d, want %d", port, tt.wantPort)
            }
        })
    }
}
```

### 4.5 Test Helpers

`t.Helper()` marks the function as a test helper so stack traces attribute
failures to the caller's line — not the helper's.

```go
func assertEqual[T comparable](t *testing.T, got, want T) {
    t.Helper()
    if got != want {
        t.Errorf("got %v, want %v", got, want)
    }
}

func assertNoError(t *testing.T, err error) {
    t.Helper()
    if err != nil {
        t.Fatalf("unexpected error: %v", err)
    }
}
```

### 4.6 TestMain

For global setup/teardown across all tests in a package:

```go
func TestMain(m *testing.M) {
    // Setup
    setupDB()
    defer teardownDB()

    // Run tests
    os.Exit(m.Run())
}
```

⚠️ Only one `TestMain` per package. `t.Cleanup` and `t.TempDir` are usually
better — avoid global state.

### 4.7 Golden Files

Store expected outputs in `testdata/` — the standard location Go tools
ignore for regular builds.

```go
func TestRender(t *testing.T) {
    golden := filepath.Join("testdata", "output.json")
    got := Render(input)

    if *update { // go test -args -update
        os.WriteFile(golden, got, 0644)
        return
    }

    want, err := os.ReadFile(golden)
    if err != nil {
        t.Fatalf("error reading golden file: %v", err)
    }
    if string(got) != string(want) {
        t.Errorf("output mismatch:\ngot:\n%s\nwant:\n%s\n", got, want)
    }
}
```

**go:embed** for embedding test fixtures:

```go
import _ "embed"

//go:embed testdata/input.json
var inputFixture []byte

func TestParse(t *testing.T) {
    result, err := Parse(inputFixture)
    assertNoError(t, err)
    // ...
}
```

### 4.8 Integration Tests with Build Tags

```go
//go:build integration

package mypkg_test

func TestDatabaseIntegration(t *testing.T) {
    if testing.Short() {
        t.Skip("skipping integration test in short mode")
    }
    db, err := sql.Open("postgres", os.Getenv("TEST_DATABASE_URL"))
    if err != nil {
        t.Fatal(err)
    }
    defer db.Close()
    // ...
}
```

Run with:

```bash
go test -tags=integration ./...
go test -tags=integration -short ./...  # skip long tests
```

---

## 5. Build & Tooling

### 5.1 go build

```bash
go build -o myapp ./cmd/myapp               # output binary name
go build -ldflags "-s -w" ./...             # strip debug info (smaller binary)
go build -ldflags "-X main.version=1.0.0" . # inject version at link time
go build -tags integration ./...            # include files with build tag
go build -tags "integration debug" ./...    # multiple tags (AND)
GOOS=linux GOARCH=arm64 go build ./...      # cross-compile
go build -trimpath ./...                    # reproducible builds (strip filesystem paths)
go build -gcflags="-m" ./...                # escape analysis output
go build -gcflags="-N -l" ./...             # disable optimizations + inlining (for debugging)
go build -race -o myapp ./...               # build with race detector
go build -buildmode=c-shared -o lib.so ./.. # build as shared library
go build -buildmode=plugin -o plugin.so .   # build as Go plugin
```

### 5.2 go install

`go install` builds and caches the binary in `$GOPATH/bin` (or `$GOBIN`).
Unlike `go build`, the binary is cached and reused.

```bash
go install ./cmd/myapp            # install from local module
go install golang.org/x/tools/cmd/goimports@latest # install specific tool
```

### 5.3 go mod

```bash
go mod init github.com/user/repo                  # initialize module
go mod tidy                                       # add missing, remove unused deps
go mod tidy -e                                    # ignore errors
go mod vendor                                     # create vendor/ directory
go mod verify                                     # verify checksums in go.sum
go mod download                                   # prefetch all dependencies
go mod download -json                             # JSON output
go mod graph                                      # print dependency graph
go mod why -m golang.org/x/sync                   # why is this module needed?
go mod edit -replace old=new                      # add replace directive
go mod edit -require module@version               # add require directive
go mod edit -droprequire module                   # remove require directive
go mod edit -go 1.22                              # set go directive
```

`go.sum` is a lockfile — commit it. It records the checksums of all modules in
the dependency graph. `go mod verify` checks every entry.

### 5.4 go workspaces (Go 1.18+)

For multi-module development:

```bash
go work init ./module1 ./module2                  # create go.work
go work use ./module3                             # add module
go work sync                                      # sync go.work with module go.mod files
```

`go.work` should NOT be committed to version control (by convention) — it's
for local development. CI uses the individual `go.mod` files.

### 5.5 Code Quality Tools

**go vet** — built-in static analysis:

```bash
go vet ./...                                      # all checks
go vet -copylocks ./...                           # locks passed by value
go vet -loopclosure ./...                         # loop variable capture (Go <1.22)
go vet -printf ./...                              # printf format string issues
go vet -shadow ./...                              # variable shadowing (Go 1.23+, was -shadow)
go vet -nilness ./...                             # nilness analysis (Go 1.19+)
go vet -unusedwrite ./...                         # unused writes
```

**staticcheck** — the standard third-party linter:

```bash
go install honnef.co/go/tools/cmd/staticcheck@latest
staticcheck ./...

# Key checks (SAxxxx):
# SA1000: regexp.MustCompile called with invalid pattern
# SA1006: fmt.Printf with dynamic format string and no arguments
# SA1019: deprecated identifier
# SA4006: value assigned but never read
# SA5001: defer in loop
# SA9004: only the first constant in group has explicit type
```

**golangci-lint** — aggregator running dozens of linters:

```bash
golangci-lint run ./...
golangci-lint run --enable-all --disable staticcheck ./...
golangci-lint run --new-from-rev=HEAD~1  # only check new code
```

Key linters enabled by default: `errcheck`, `gosimple`, `govet`, `ineffassign`,
`staticcheck`, `unused`.

Additional linters to enable: `revive`, `gosec`, `misspell`, `gocritic`,
`gofumpt`, `gci`, `goimports`.

**Formatter:**

```bash
gofmt -w .                           # standard formatter
gofumpt -w .                         # stricter gofmt (preferred)
goimports -w .                       # format + organize imports
gci write .                          # import grouping (stdlib → third-party → local)
```

### 5.6 Profiling with pprof

```go
// In your application:
import _ "net/http/pprof"
// Then: go tool pprof http://localhost:6060/debug/pprof/profile?seconds=30
```

Command-line profiling:

```bash
go test -cpuprofile=cpu.out -bench=. ./...
go tool pprof cpu.out
(pprof) top
(pprof) list functionName
(pprof) web  # opens browser graph (requires graphviz)

go test -memprofile=mem.out -bench=. ./...
go tool pprof mem.out
(pprof) top
(pprof) alloc_space  # cumulative allocations

go tool pprof -http :8080 cpu.out   # interactive web UI
```

Profile types:

| Profile | Command | What it measures |
|---------|---------|-----------------|
| CPU | `-cpuprofile=cpu.out` | Where CPU time is spent |
| Heap | `-memprofile=mem.out` | Current heap allocations |
| Alloc | `pprof alloc_space` | Cumulative allocations |
| Goroutine | `/debug/pprof/goroutine` | Goroutine stack traces |
| Block | `/debug/pprof/block` | Blocking on synchronization |
| Mutex | `/debug/pprof/mutex` | Mutex contention |

### 5.7 Execution Trace

```bash
go test -trace=trace.out ./...
go tool trace trace.out           # opens browser with trace viewer
```

The trace shows goroutine scheduling, GC events, network blocking, and system
calls — useful for latency analysis.

### 5.8 Debugging with Delve

```bash
dlv debug ./cmd/myapp              # debug binary
dlv attach <PID>                   # attach to running process
dlv --headless --listen :2345 ./.. # headless server for IDE integration
dlv test ./...                     # debug tests

# Common dlv commands:
(dlv) break main.go:42             # set breakpoint
(dlv) condition 1 userID == 123    # conditional breakpoint
(dlv) continue                     # resume execution
(dlv) next                         # step over
(dlv) step                         # step into
(dlv) print variable               # print value
(dlv) goroutines                   # list goroutines
(dlv) goroutine 5                  # switch to goroutine 5
(dlv) stack                        # print stack trace
(dlv) frame 2                      # switch to frame 2
(dlv) locals                       # print local variables
```

### 5.9 Live Reload

Using `air`:

```toml
# .air.toml
[build]
  cmd = "go build -o ./tmp/main ./cmd/server"
  bin = "./tmp/main"
  full_bin = "APP_ENV=dev ./tmp/main"
  include_ext = ["go", "tpl", "tmpl", "html"]
  exclude_dir = ["tmp", "vendor", "node_modules"]
  delay = 1000
  stop_on_error = true
```

```bash
air -c .air.toml
```

### 5.10 Container Builds

**Multi-stage Dockerfile:**

```dockerfile
FROM golang:1.24 AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /app/server ./cmd/server

FROM gcr.io/distroless/static:nonroot
COPY --from=builder /app/server /server
USER nonroot:nonroot
ENTRYPOINT ["/server"]
```

**ko** — build Go apps as container images without Dockerfile:

```bash
ko build ./cmd/server
ko build -B ./cmd/server --platform=linux/amd64,linux/arm64
```

**GoReleaser** — cross-compile, package, and release:

```yaml
# .goreleaser.yml
builds:
  - env: [CGO_ENABLED=0]
    goos: [linux, darwin, windows]
    goarch: [amd64, arm64]
    ldflags: [-s -w -X main.version={{ .Version }}]
archives:
  - format: tar.gz
    name_template: "{{ .ProjectName }}_{{ .Version }}_{{ .Os }}_{{ .Arch }}"
checksum:
  name_template: "checksums.txt"
changelog:
  sort: asc
```

---

## 6. Security

### 6.1 SQL Injection Prevention

**Always use placeholders.** Never concatenate user input into SQL.

```go
// RIGHT — placeholders prevent injection
rows, err := db.QueryContext(ctx,
    "SELECT id, name FROM users WHERE email = $1", userInput,
)

// WRONG — SQL injection vulnerability
query := fmt.Sprintf("SELECT id, name FROM users WHERE email = '%s'", userInput)
rows, err := db.QueryContext(ctx, query)
```

For dynamic column/table names, use a whitelist:

```go
var allowedColumns = map[string]bool{
    "id": true, "name": true, "email": true, "created_at": true,
}

func listUsers(db *sql.DB, orderBy string) ([]User, error) {
    if !allowedColumns[orderBy] {
        return nil, fmt.Errorf("invalid column: %s", orderBy)
    }
    // Safe: orderBy is from a whitelist, not user input
    query := fmt.Sprintf("SELECT id, name, email FROM users ORDER BY %s", orderBy)
    rows, err := db.QueryContext(context.Background(), query)
    // ...
}
```

### 6.2 Path Traversal Prevention

```go
// Clean the path
safe := filepath.Clean(userInput)

// Check the cleaned path stays within the base directory
func safeRead(baseDir, userPath string) ([]byte, error) {
    fullPath := filepath.Join(baseDir, filepath.Clean(userPath))
    if !strings.HasPrefix(fullPath, filepath.Clean(baseDir)+string(os.PathSeparator)) {
        return nil, fmt.Errorf("path traversal attempt: %s", userPath)
    }
    return os.ReadFile(fullPath)
}

// Or use io/fs.ValidPath (Go 1.22+) for fs.FS operations
if !fs.ValidPath(userPath) {
    return nil, fmt.Errorf("invalid path: %s", userPath)
}
```

### 6.3 Template Injection

```go
// RIGHT — html/template auto-escapes by context
import "html/template"
tmpl := template.Must(template.New("page").Parse(`
    <h1>{{.Title}}</h1>
    <p>{{.Body}}</p>
    <a href="{{.URL}}">link</a>
`))
// Title, Body, and URL are auto-escaped for their HTML context

// WRONG — text/template does NOT escape
import "text/template"
// text/template output goes directly to output — NEVER for HTML
```

### 6.4 Cryptographic Best Practices

```go
// Random tokens
import "crypto/rand"
// NOT math/rand — that's deterministic, not secure

func generateToken() (string, error) {
    b := make([]byte, 32)
    if _, err := rand.Read(b); err != nil {
        return "", err
    }
    return base64.RawURLEncoding.EncodeToString(b), nil
}

// Constant-time comparison for any security-sensitive comparison
// Not just HMACs — tokens, API keys, any secret comparison
func verifyToken(received, expected string) bool {
    return subtle.ConstantTimeCompare([]byte(received), []byte(expected)) == 1
}

// Password hashing with argon2id (recommended)
import "golang.org/x/crypto/argon2"
func hashPassword(password string, salt []byte) []byte {
    return argon2.IDKey([]byte(password), salt,
        1,         // iterations (time parameter)
        64*1024,   // memory (64MB)
        4,         // threads (parallelism)
        32,        // key length
    )
}
// Store the salt alongside the hash (prepend or use encoded format)

// bcrypt fallback (simpler API, fixed params)
import "golang.org/x/crypto/bcrypt"
hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
err = bcrypt.CompareHashAndPassword(hash, []byte(password))
```

**TLS configuration:**

```go
tlsConfig := &tls.Config{
    MinVersion: tls.VersionTLS12,
    // Prefer modern cipher suites — Go's defaults are already good
    // Explicitly disable weak ciphers if needed
    CurvePreferences: []tls.CurveID{tls.X25519, tls.CurveP256},
}
server := &http.Server{
    Addr:      ":443",
    TLSConfig: tlsConfig,
}
```

### 6.5 Input Validation

```go
import "regexp"

// Whitelist validation — define what IS allowed
var validUsername = regexp.MustCompile(`^[a-zA-Z0-9_-]{3,32}$`)
var validEmail = regexp.MustCompile(`^[^\s@]+@[^\s@]+\.[^\s@]+$`)

func validateUsername(s string) bool {
    return validUsername.MatchString(s)
}

// For struct validation, use the validator package
import "github.com/go-playground/validator/v10"
type CreateUserRequest struct {
    Name  string `validate:"required,min=3,max=50"`
    Email string `validate:"required,email"`
    Age   int    `validate:"gte=0,lte=150"`
}
validate := validator.New()
if err := validate.Struct(req); err != nil {
    return err
}
```

### 6.6 Memory Safety

Go is memory-safe by default — no buffer overflows (bounds-checked slices),
no use-after-free (GC), no dangling pointers (escape analysis ensures
liveness). The unsafe package is the sole exception.

```go
// Slice capacity vs length — classic confusion
s := make([]byte, 5, 10)
fmt.Println(len(s)) // 5
fmt.Println(cap(s)) // 10

// Truncate while keeping capacity: s = s[:len(s)]
s = s[:len(s)] // keeps capacity=10

// Truncate to zero: s = s[:0]
s = s[:0] // len=0, cap=10 — backing array retained

// Release backing array: s = nil
s = nil // len=0, cap=0 — GC collects backing array
```

### 6.7 Deserialization Safety

```go
// encoding/gob is unsafe with untrusted input — gob can call methods during decode
// NEVER: gob.NewDecoder(untrusted).Decode(&v)

// json.Decoder with strict settings
dec := json.NewDecoder(untrustedInput)
dec.DisallowUnknownFields()

// XML with caution
d := xml.NewDecoder(untrustedInput)
d.Strict = true

// YAML: use yaml.Unmarshal with a known type, never yaml.Unmarshal(..., &any)
// yaml.v2's Unmarshal into interface{} is dangerous (like JSON into map[string]any)
```

### 6.8 HTTP Security

```go
server := &http.Server{
    Addr:           ":8080",
    ReadTimeout:    5 * time.Second,  // from connection accept to request body read
    WriteTimeout:   10 * time.Second, // from request header read to response write
    IdleTimeout:    120 * time.Second, // keep-alive timeout
    MaxHeaderBytes: 1 << 20,         // 1 MB, prevent header blowup
}

// TimeoutHandler wraps slow handlers
timeoutHandler := http.TimeoutHandler(myHandler, 5*time.Second, "handler timed out")

// Security headers middleware
func securityHeaders(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        w.Header().Set("X-Content-Type-Options", "nosniff")
        w.Header().Set("X-Frame-Options", "DENY")
        w.Header().Set("Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'")
        w.Header().Set("Strict-Transport-Security",
            "max-age=31536000; includeSubDomains")
        next.ServeHTTP(w, r)
    })
}
```

---

## 7. Project Structure

### 7.1 Standard Layout

```
myproject/
├── cmd/                    # Main applications
│   ├── server/main.go      # API server binary
│   ├── worker/main.go      # Background worker binary
│   └── cli/main.go         # CLI tool binary
├── internal/               # Private application code (Go enforces unimportability)
│   ├── app/
│   │   ├── app.go          # Application struct, wiring, lifecycle
│   │   └── middleware.go
│   ├── handler/
│   │   ├── user.go         # HTTP handlers per domain
│   │   └── product.go
│   ├── service/
│   │   ├── user.go         # Business logic
│   │   └── auth.go
│   └── repository/
│       ├── user.go         # Data access
│       └── dberrors.go
├── pkg/                    # Public library code (importable externally)
│   ├── auth/
│   └── pagination/
├── api/                    # API definitions
│   ├── proto/              # protobuf definitions
│   │   └── user/v1/
│   │       └── user.proto
│   └── openapi/            # OpenAPI/Swagger specs
├── web/                    # Web assets
│   ├── static/
│   └── templates/
├── configs/                # Configuration files
│   ├── config.yaml
│   └── config.prod.yaml
├── scripts/                # Build, install, analysis scripts
│   ├── build.sh
│   └── migrate.sh
├── build/                  # CI/CD configuration
│   ├── Dockerfile
│   └── ci/
│       └── .github/workflows/
├── deployments/            # Deployment configs
│   ├── kubernetes/
│   └── terraform/
├── test/                   # External test data
│   └── testdata/
├── docs/                   # Design docs, README
├── go.mod
├── go.sum
└── Makefile
```

### 7.2 Module Naming

```bash
go mod init github.com/organization/project
# or for internal tools:
go mod init company.com/internal/tool
```

### 7.3 Go Workspaces

Multi-module repositories use `go.work`:

```
├── go.work
├── server/
│   ├── go.mod (github.com/org/monorepo/server)
│   └── main.go
├── shared/
│   ├── go.mod (github.com/org/monorepo/shared)
│   └── types.go
└── proto/
    ├── go.mod (github.com/org/monorepo/proto)
    └── user.proto
```

```bash
go work init ./server ./shared ./proto
```

Do not commit `go.work` — it's for local development.

### 7.4 Platform-Specific Files

Go automatically selects files by `GOOS` and `GOARCH`:

```
file.go           # always compiled
file_linux.go     # only on linux
file_darwin.go    # only on macOS
file_windows.go   # only on Windows
file_amd64.go     # only on amd64
file_arm64.go     # only on arm64
file_linux_amd64.go  # only on linux/amd64
file_linux_amd64_test.go  # test, only on linux/amd64
```

### 7.5 Internal Visibility

Packages under `internal/` can only be imported by code rooted at the
module path containing the `internal` directory. The Go toolchain enforces
this at compile time.

```
github.com/org/project/
├── internal/auth/      # only importable by code under github.com/org/project/
├── cmd/server/         # can import internal/auth
└── pkg/public/         # can import internal/auth
```

### 7.6 cgo

Call C code from Go. Use only when necessary — cgo breaks cross-compilation,
slows builds, and complicates deployment.

```go
/*
#cgo CFLAGS: -I/usr/local/include
#cgo LDFLAGS: -L/usr/local/lib -lmylib
#include <stdlib.h>
#include <mylib.h>
*/
import "C"
import "unsafe"

func CallCLibrary(input string) string {
    cInput := C.CString(input)           // Go string → C string (allocates)
    defer C.free(unsafe.Pointer(cInput)) // MUST free

    cResult := C.my_function(cInput)
    result := C.GoString(cResult)        // C string → Go string (copies)
    return result
}
```

Key cgo rules:

| Rule | Detail |
|------|--------|
| C.CString | Allocates C memory — call `C.free` after |
| C.GoString | Copies data — original C memory can be freed |
| C.GoBytes | Copies C byte array to Go slice |
| unsafe.Pointer | Bridge between Go pointers and C void* |
| CGO_ENABLED=0 | Disable cgo for pure Go builds |
| -buildmode=c-shared | Build Go as a C shared library (.so/.dylib) |

```go
// Exporting Go functions to C
//export Multiply
func Multiply(a, b int) int {
    return a * b
}
// Build: CGO_ENABLED=1 go build -buildmode=c-shared -o libmath.so
```

cgo limitations: no cross-compilation without a cross-C-toolchain; each cgo
call locks the calling goroutine to an OS thread; significantly slower builds.

---

## 8. Performance Patterns

### 8.1 sync.Pool

Reuse temporary objects to reduce GC pressure. The pool may clear objects at
each GC cycle — never assume a pooled object will survive.

```go
var bufferPool = sync.Pool{
    New: func() any {
        return new(bytes.Buffer)
    },
}

func process(data []byte) {
    buf := bufferPool.Get().(*bytes.Buffer)
    defer func() {
        buf.Reset()
        bufferPool.Put(buf)
    }()
    buf.Write(data)
    // use buf...
}

// Pool for slices
var slicePool = sync.Pool{
    New: func() any {
        s := make([]byte, 0, 4096)
        return &s
    },
}

func getBuf() *[]byte {
    return slicePool.Get().(*[]byte)
}

func putBuf(b *[]byte) {
    *b = (*b)[:0] // reset length, keep capacity
    slicePool.Put(b)
}
```

**When to use:** high-allocation-rate temporary objects with similar lifetimes.
**When NOT to use:** long-lived objects, objects with varying sizes (capacity
mismatch wastes memory), objects that must never be zeroed.

### 8.2 strings.Builder

For building strings in loops, `strings.Builder` avoids repeated allocations:

```go
// SLOW: each + allocates a new string
var s string
for _, item := range items {
    s += item.Name + ","
}

// FAST: single allocation
var b strings.Builder
b.Grow(estimatedSize) // avoid reallocations if you know the size
for _, item := range items {
    b.WriteString(item.Name)
    b.WriteByte(',')
}
s := b.String()
```

### 8.3 Pre-allocating Slices and Maps

```go
// Pre-allocate slice capacity to avoid reallocation
const nItems = 1000
ids := make([]int64, 0, nItems) // len=0, cap=1000
for rows.Next() {
    var id int64
    rows.Scan(&id)
    ids = append(ids, id)
}

// Bulk append
final := make([]int, 0, len(a)+len(b)+len(c))
final = append(final, a...)
final = append(final, b...)
final = append(final, c...)

// Pre-size map when count is known
// Only use if you know the approximate size — over-sizing wastes memory
users := make(map[int64]*User, estimatedCount)
```

### 8.4 Escape Analysis

The compiler decides whether a variable lives on the stack or heap. Use
`-gcflags="-m"` to see what escapes:

```bash
go build -gcflags="-m" ./... 2>&1 | grep "escapes to heap"
```

What causes escape:
- Returning a pointer to a local variable
- Storing a value in an `interface{}` (or `any`)
- Passing a value to `fmt.Println` (variadic `interface{}`)
- Closures that reference local variables
- Sending pointers on channels
- Storing values in global variables

```go
// ESCAPES: pointer to local returned
func newInt() *int { x := 42; return &x }

// DOES NOT ESCAPE: value returned by copy
func getInt() int { x := 42; return x }

// ESCAPES: interface boxing
func print(v any) { fmt.Println(v) }
print(42) // 42 escapes to heap (boxed in interface{})
```

### 8.5 Memory Pitfalls

**Slice retaining backing array:**

```go
// s2 keeps the entire backing array of s1 alive
s1 := make([]byte, 1<<20) // 1MB
s2 := s1[:100]            // s2's backing array is still 1MB

// Fix: copy to a new slice
s2 := make([]byte, 100)
copy(s2, s1[:100])
// s1's backing array can now be GC'd if no other references exist
```

**Goroutine memory:**

```
1 goroutine  = ~2KB stack  (grows as needed; starts at 2KB)
1M goroutines = ~2GB stack memory alone
Context switching overhead grows with goroutines > logical CPUs
```

Channel is not always best — for simple shared-state locking, `sync.Mutex` is
faster than an unbuffered channel (no goroutine switch).

### 8.6 map vs switch

For small N (≤ ~8), a `switch` on int/string is faster than a `map` lookup.
The switch compiles to a jump table; the map requires hashing.

```go
// For 3-5 cases: switch is faster
switch key {
case "create":
    return handleCreate()
case "update":
    return handleUpdate()
case "delete":
    return handleDelete()
default:
    return handleUnknown()
}

// For 100+ cases: map is more readable and reasonable speed
handlers := map[string]func() error{
    "create": handleCreate,
    "update": handleUpdate,
    // ...
}
if h, ok := handlers[key]; ok {
    return h()
}
```

---

## 9. Popular Ecosystem

### 9.1 Web Frameworks

**net/http (stdlib)** — always the right starting point:

```go
mux := http.NewServeMux()
mux.HandleFunc("GET /api/users/{id}", handleGetUser)  // Go 1.22+ method+pattern
mux.HandleFunc("POST /api/users", handleCreateUser)
http.ListenAndServe(":8080", mux)
```

**chi** — lightweight, idiomatic, middleware-composable:

```go
r := chi.NewRouter()
r.Use(middleware.Logger)
r.Use(middleware.Recoverer)
r.Use(middleware.Timeout(30 * time.Second))

r.Route("/api/users", func(r chi.Router) {
    r.Get("/", listUsers)
    r.Post("/", createUser)
    r.Route("/{userID}", func(r chi.Router) {
        r.Use(UserCtx) // middleware scoped to this subrouter
        r.Get("/", getUser)
        r.Put("/", updateUser)
        r.Delete("/", deleteUser)
    })
})
http.ListenAndServe(":8080", r)
```

**gin** — fast, with binding and validation:

```go
r := gin.Default()
r.GET("/api/users/:id", func(c *gin.Context) {
    id := c.Param("id")
    user, err := svc.GetUser(c, id)
    if err != nil {
        c.JSON(http.StatusNotFound, gin.H{"error": err.Error()})
        return
    }
    c.JSON(http.StatusOK, user)
})
r.POST("/api/users", func(c *gin.Context) {
    var req CreateUserReq
    if err := c.ShouldBindJSON(&req); err != nil {
        c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
        return
    }
    user, err := svc.CreateUser(c, req)
    if err != nil {
        c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
        return
    }
    c.JSON(http.StatusCreated, user)
})
```

### 9.2 Database and ORM

**sqlx** — extends database/sql with struct scanning:

```go
type User struct {
    ID    int64  `db:"id"`
    Name  string `db:"name"`
    Email string `db:"email"`
}
db, _ := sqlx.Connect("postgres", dsn)

var users []User
db.SelectContext(ctx, &users, "SELECT id, name, email FROM users WHERE active = $1", true)

var user User
db.GetContext(ctx, &user, "SELECT id, name, email FROM users WHERE id = $1", id)

result, _ := db.NamedExecContext(ctx,
    "INSERT INTO users (name, email) VALUES (:name, :email)", user,
)
```

**pgx** — PostgreSQL driver with connection pool:

```go
pool, _ := pgxpool.New(ctx, dsn)
defer pool.Close()

var name string
err := pool.QueryRow(ctx,
    "SELECT name FROM users WHERE id = $1", userID,
).Scan(&name)

rows, _ := pool.Query(ctx,
    "SELECT id, name FROM users WHERE active = $1", true,
)
defer rows.Close()
for rows.Next() {
    var id int64
    var name string
    rows.Scan(&id, &name)
}
```

**sqlc** — code generation from SQL (type-safe, great performance):

```sql
-- query.sql
-- name: GetUser :one
SELECT id, name, email FROM users WHERE id = $1;

-- name: ListActiveUsers :many
SELECT id, name FROM users WHERE active = $1;

-- name: CreateUser :exec
INSERT INTO users (name, email) VALUES ($1, $2);
```

```go
// Generated code
queries := db.New(conn)
user, err := queries.GetUser(ctx, 42)
users, err := queries.ListActiveUsers(ctx, true)
err = queries.CreateUser(ctx, db.CreateUserParams{Name: "Alice", Email: "alice@example.com"})
```

**gorm** (popular but not idiomatic Go — prefer sqlc or sqlx):

```go
db, _ := gorm.Open(postgres.Open(dsn), &gorm.Config{})
db.AutoMigrate(&User{}, &Order{})

var user User
db.Where("email = ?", email).First(&user)
db.Where("id = ?", id).Delete(&User{})

// Preload associations (N+1 by default unless you Preload)
db.Preload("Orders").Find(&users)
```

### 9.3 gRPC

**Protobuf definition:**

```protobuf
syntax = "proto3";
package user.v1;
option go_package = "github.com/org/project/api/user/v1;userv1";

service UserService {
  rpc GetUser(GetUserRequest) returns (GetUserResponse);
  rpc ListUsers(ListUsersRequest) returns (stream User); // server streaming
  rpc CreateUser(stream CreateUserRequest) returns (CreateUserResponse); // client streaming
  rpc Chat(stream ChatMessage) returns (stream ChatMessage); // bidirectional
}

message GetUserRequest {
  string user_id = 1;
}
message GetUserResponse {
  string user_id = 1;
  string name = 2;
  string email = 3;
}
```

**Code generation:**

```bash
protoc --go_out=. --go_opt=paths=source_relative \
       --go-grpc_out=. --go-grpc_opt=paths=source_relative \
       api/user/v1/user.proto
```

**Server implementation:**

```go
type userServer struct {
    userv1.UnimplementedUserServiceServer
    svc *service.UserService
}

func (s *userServer) GetUser(ctx context.Context, req *userv1.GetUserRequest) (*userv1.GetUserResponse, error) {
    user, err := s.svc.GetUser(ctx, req.UserId)
    if err != nil {
        return nil, status.Errorf(codes.NotFound, "user %s: %v", req.UserId, err)
    }
    return &userv1.GetUserResponse{
        UserId: user.ID,
        Name:   user.Name,
        Email:  user.Email,
    }, nil
}

func main() {
    lis, _ := net.Listen("tcp", ":50051")
    s := grpc.NewServer(
        grpc.UnaryInterceptor(loggingInterceptor),
    )
    userv1.RegisterUserServiceServer(s, &userServer{svc: svc})
    s.Serve(lis)
    // Graceful stop: s.GracefulStop()
}
```

**Client:**

```go
conn, err := grpc.Dial("localhost:50051",
    grpc.WithTransportCredentials(insecure.NewCredentials()), // dev only
    grpc.WithUnaryInterceptor(clientLoggingInterceptor),
)
defer conn.Close()

client := userv1.NewUserServiceClient(conn)
ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
defer cancel()

resp, err := client.GetUser(ctx, &userv1.GetUserRequest{UserId: "123"})
```

**Interceptors (middleware):**

```go
func loggingInterceptor(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
    start := time.Now()
    resp, err := handler(ctx, req)
    log.Printf("%s %v %v", info.FullMethod, err, time.Since(start))
    return resp, err
}
```

**grpc-gateway** — REST/JSON gateway that translates HTTP to gRPC:

```protobuf
import "google/api/annotations.proto";
service UserService {
  rpc GetUser(GetUserRequest) returns (GetUserResponse) {
    option (google.api.http) = {
      get: "/v1/users/{user_id}"
    };
  }
}
```

### 9.4 CLI

**cobra** — the standard CLI framework:

```go
var rootCmd = &cobra.Command{
    Use:   "myapp",
    Short: "My application",
    PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
        return initConfig()
    },
}
var serveCmd = &cobra.Command{
    Use:   "serve",
    Short: "Start the server",
    RunE: func(cmd *cobra.Command, args []string) error {
        port, _ := cmd.Flags().GetInt("port")
        return runServer(port)
    },
}

func init() {
    rootCmd.AddCommand(serveCmd)
    serveCmd.Flags().IntP("port", "p", 8080, "port to listen on")
    rootCmd.PersistentFlags().StringP("config", "c", "", "config file")
}

func main() {
    if err := rootCmd.Execute(); err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(1)
    }
}
```

### 9.5 Testing Libraries

**testify** — assert, require, mock, suite:

```go
import (
    "github.com/stretchr/testify/assert"
    "github.com/stretchr/testify/require"
    "github.com/stretchr/testify/mock"
)

func TestUser(t *testing.T) {
    user, err := GetUser("123")
    require.NoError(t, err)  // Fatal on failure
    assert.Equal(t, "Alice", user.Name) // Non-fatal
    assert.NotNil(t, user.Email)
}

type MockUserRepo struct {
    mock.Mock
}
func (m *MockUserRepo) GetUser(id string) (*User, error) {
    args := m.Called(id)
    return args.Get(0).(*User), args.Error(1)
}
// Usage:
mockRepo := new(MockUserRepo)
mockRepo.On("GetUser", "123").Return(&User{Name: "Alice"}, nil)
user, err := mockRepo.GetUser("123")
mockRepo.AssertExpectations(t)
```

### 9.6 Observability

**slog** (Go 1.21+, standard structured logging):

```go
import "log/slog"

logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
    Level: slog.LevelInfo,
}))

logger.Info("request processed",
    slog.String("method", "GET"),
    slog.String("path", "/api/users"),
    slog.Int("status", 200),
    slog.Duration("duration", elapsed),
)
logger.Error("db query failed",
    slog.Any("error", err),
    slog.String("query_id", queryID),
)

// With pre-populated attributes
logger = logger.With(
    slog.String("service", "users"),
    slog.String("env", "production"),
)
```

**OpenTelemetry:**

```go
import "go.opentelemetry.io/otel"

ctx, span := tracer.Start(ctx, "GetUser",
    trace.WithAttributes(attribute.String("user_id", userID)),
)
defer span.End()

// Propagate context through layers automatically
user, err := repo.GetUser(ctx, userID) // span is implicit via ctx
```

**Prometheus:**

```go
import "github.com/prometheus/client_golang/prometheus"
import "github.com/prometheus/client_golang/prometheus/promhttp"

var (
    requestDuration = prometheus.NewHistogramVec(
        prometheus.HistogramOpts{
            Name:    "http_request_duration_seconds",
            Help:    "Duration of HTTP requests",
            Buckets: prometheus.DefBuckets,
        },
        []string{"method", "path", "status"},
    )
    activeConnections = prometheus.NewGauge(
        prometheus.GaugeOpts{
            Name: "active_connections",
            Help: "Number of active connections",
        },
    )
)

http.Handle("/metrics", promhttp.Handler())
```

---

## 10. Platform-Specific

### 10.1 Cross-Compilation

```bash
GOOS=linux GOARCH=amd64 go build -o myapp-linux-amd64 ./cmd/myapp
GOOS=linux GOARCH=arm64 go build -o myapp-linux-arm64 ./cmd/myapp
GOOS=darwin GOARCH=amd64 go build -o myapp-darwin-amd64 ./cmd/myapp
GOOS=darwin GOARCH=arm64 go build -o myapp-darwin-arm64 ./cmd/myapp
GOOS=windows GOARCH=amd64 go build -o myapp.exe ./cmd/myapp
GOOS=js GOARCH=wasm go build -o myapp.wasm ./cmd/myapp

# Explicitly set CGO_ENABLED for static binaries
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o myapp ./cmd/myapp
```

### 10.2 TinyGo

Go for microcontrollers and WASM. Subset of standard Go — no `reflect`, limited
`map`, no `cgo`, no `os/exec`.

```bash
tinygo build -target=arduino -o blink.hex ./examples/blink
tinygo flash -target=arduino ./examples/blink
tinygo build -target=wasm -o main.wasm ./cmd/wasm

# Microcontroller code
package main
import "machine"
func main() {
    led := machine.LED
    led.Configure(machine.PinConfig{Mode: machine.PinOutput})
    for {
        led.High()
        // ... delay
        led.Low()
        // ... delay
    }
}
```

Common targets: `arduino`, `arduino-nano33`, `esp32`, `pico`, `microbit`,
`wasm`, `wasi`.

### 10.3 Go in Containers

```dockerfile
# Multi-stage build producing a minimal image
FROM golang:1.24-alpine AS builder
RUN apk add --no-cache git ca-certificates
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w -X main.version=$(git describe --tags)" -o /app/server ./cmd/server

FROM scratch
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /app/server /server
ENTRYPOINT ["/server"]

# Alternative: distroless (includes ca-certs, tzdata, /etc/passwd)
FROM gcr.io/distroless/static:nonroot
COPY --from=builder /app/server /server
USER nonroot:nonroot
ENTRYPOINT ["/server"]
```

Image choice tradeoffs:

| Base | Size | CA certs | TZ data | /etc/passwd | Shell |
|------|------|----------|---------|-------------|-------|
| `scratch` | ~0MB | No | No | No | No |
| `alpine` | ~5MB | Yes | Yes | Yes | Yes (ash) |
| `distroless/static` | ~2MB | Yes | Yes | Yes | No |

### 10.4 Go Mobile (gomobile)

```bash
gomobile bind -target=android -o app.aar ./mypackage  # Android library
gomobile bind -target=ios -o App.framework ./mypackage # iOS framework
gomobile build -target=android ./cmd/myapp            # Android APK
gomobile build -target=ios ./cmd/myapp                # iOS app
```

### 10.5 Go Shared Libraries

```go
package main
import "C"

//export Greet
func Greet(name *C.char) *C.char {
    greeting := "Hello, " + C.GoString(name) + "!"
    return C.CString(greeting)
}
func main() {}
```

```bash
CGO_ENABLED=1 go build -buildmode=c-shared -o libgreet.so .
# Generates libgreet.so and libgreet.h
```

Then use from C/Rust/Python/Node via FFI. The generated header declares all
`//export`-ed functions.

---

## References

- **Go Specification:** https://go.dev/ref/spec
- **Effective Go:** https://go.dev/doc/effective_go
- **Go Code Review Comments:** https://github.com/golang/go/wiki/CodeReviewComments
- **Standard Library:** https://pkg.go.dev/std
- **Go Blog (The Go Blog):** https://go.dev/blog/
- **Go by Example:** https://gobyexample.com/
- **100 Go Mistakes:** https://100go.co/
- **Uber Go Style Guide:** https://github.com/uber-go/guide
- **golang-standards/project-layout:** https://github.com/golang-standards/project-layout
- **OWASP Go Security:** https://github.com/OWASP/Go-SCP
- **Go concurrency patterns:** https://go.dev/talks/2012/concurrency.slide
- **Go performance:** https://github.com/dgryski/go-perfbook
- **Go diagnostics:** https://go.dev/doc/diagnostics
