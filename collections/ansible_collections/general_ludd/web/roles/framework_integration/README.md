# `general_ludd.web.framework_integration` — Modern Web Framework Integration

Scaffold, generate, test, and analyze modern web frontends: React components, Next.js pages/routes, HTMX templates, GraphQL queries, and REST API endpoints.

## Quick start

```yaml
- name: Scaffold a React component
  hosts: localhost
  vars:
    framework_integration_framework: "react"
    framework_integration_operation: "scaffold"
    framework_integration_component_name: "UserProfile"
  roles:
    - general_ludd.web.framework_integration
```

## Operations

| Operation   | Description                                                    |
|-------------|----------------------------------------------------------------|
| `scaffold`  | Generate component/route/template boilerplate                  |
| `generate`  | Generate GraphQL query or Next.js page route                   |
| `test`      | Test a REST API endpoint or validate a GraphQL query           |
| `analyze`   | Analyze schema, validate template structure                    |

## Frameworks

| Framework | Coverage                                                       |
|-----------|-----------------------------------------------------------------|
| `react`   | Scaffold functional component with hooks boilerplate            |
| `nextjs`  | Generate page/route, API route, layout, or loading component    |
| `htmx`    | Create HTMX template with hx-* attributes, validate structure   |
| `graphql` | Generate query/mutation/fragment, parse schema, validate        |
| `rest`    | Test endpoint with fetch/Axios pattern, analyze response        |

## Knowledge Domains

### React
- **Functional components**: props, state, side-effects
- **Hooks**: useState, useEffect, useContext, useMemo, useCallback, useRef
- **JSX**: expressions, conditional rendering, list rendering, keys
- **Virtual DOM**: reconciliation algorithm, diffing, keyed elements
- **Component lifecycle**: mounting, updating, unmounting phases
- **State lifting**: shared state moved to closest common ancestor
- **Context API**: createContext, Provider, useContext — avoid prop drilling
- **Error boundaries**: static getDerivedStateFromError, componentDidCatch
- **Suspense**: lazy loading, fallback UI, streaming SSR
- **Server components**: zero client JS, async data fetching, use server directive

### Next.js
- **Pages router**: getServerSideProps (SSR), getStaticProps (SSG), getStaticPaths
- **App router**: server components, layouts (layout.js), loading states (loading.js), error boundaries (error.js)
- **API routes**: pages/api/ or app/api/, request/response handlers
- **Middleware**: edge runtime, path matching, redirect/rewrite
- **ISR**: revalidate interval, on-demand revalidation
- **Rendering strategies**: SSR (request-time), SSG (build-time), ISR (incremental), CSR (client-only)
- **Image optimization**: next/image, automatic lazy loading, responsive sizes

### HTMX
- **Core attributes**: hx-get, hx-post, hx-put, hx-delete — declare HTTP requests on any element
- **Targeting**: hx-target (CSS selector), hx-swap (innerHTML, outerHTML, beforeend, afterbegin, delete, none)
- **Triggering**: hx-trigger (click, change, submit, load, revealed, intersect, every 2s)
- **Indicators**: hx-indicator — show/hide loading element during request
- **History**: hx-push-url — update browser URL on request
- **Out-of-band swaps**: hx-swap-oob — update multiple page regions from one response
- **Extensions**: WebSocket (ws://), server-sent events (sse://), class-tools, morphdom-swap
- **Headers**: HX-Request, HX-Trigger, HX-Trigger-Name — server-side request detection
- **Validation**: hx-validate — form validation before request

### GraphQL
- **Queries**: named queries, field selection, nested objects
- **Mutations**: input types, optimistic updates
- **Subscriptions**: WebSocket transport, real-time updates
- **Fragments**: DRY field sets, fragment composition, inline/named fragments
- **Variables**: $variable syntax, typed variables, default values
- **Apollo Client**: useQuery (loading/error/data), useMutation, InMemoryCache, cache policies
- **Relay**: compiled queries, fragment containers, store updater
- **Schema introspection**: __schema, __type, __typename meta-fields
- **Persisted queries**: hash-based, APQ (automatic persisted queries)
- **Schema-first vs code-first**: SDL vs programmatic type definitions

### REST APIs
- **fetch API**: fetch(), async/await, response.json(), response.ok, AbortController
- **Axios**: interceptors (request/response), defaults, cancel tokens, create instance
- **Error handling**: status codes (4xx client, 5xx server), retry logic, fallback UI
- **Caching**: ETag (If-None-Match), Cache-Control (max-age, stale-while-revalidate), Last-Modified
- **Pagination**: cursor-based (next_cursor), offset-based (page/limit), Link header (RFC 5988)
- **Rate limiting**: 429 Too Many Requests, Retry-After header, exponential backoff
- **API versioning**: URL path (/v1/), header (Accept-Version), query param (?v=1)

## Parameters

| Variable                                   | Default    | Description                              |
|--------------------------------------------|------------|------------------------------------------|
| `framework_integration_framework`          | `"react"`  | Framework: react/nextjs/htmx/graphql/rest |
| `framework_integration_operation`          | `"scaffold"`| scaffold/generate/test/analyze           |
| `framework_integration_component_name`     | `""`       | Component/route name                     |
| `framework_integration_endpoint_url`       | `""`       | REST API endpoint URL                    |
| `framework_integration_graphql_schema_file`| `""`       | Path to GraphQL SDL schema file          |
| `framework_integration_output_dir`         | `/tmp/...` | Output directory for generated artifacts |

## Results

The `_fi_output` fact contains the operation result; a `framework_integration.json` artifact is written to the output directory.
