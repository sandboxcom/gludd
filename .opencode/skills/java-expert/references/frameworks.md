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
