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

