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
