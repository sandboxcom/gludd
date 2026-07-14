# javascript_debug — JS debugging, error handling, bundle analysis

Check JavaScript syntax, lint with eslint configuration, analyze error patterns,
verify source maps, and audit bundle composition.

## Knowledge Areas

### Console API
- `console.log()`, `console.warn()`, `console.error()`, `console.info()`, `console.debug()`
- `console.table()` — tabular data display
- `console.group()` / `console.groupEnd()` — collapsible logging groups
- `console.trace()` — stack trace logging
- `console.assert()` — conditional error logging
- `console.time()` / `console.timeEnd()` — performance measurement

### Breakpoints
- **Line breakpoints**: standard `debugger` statement
- **Conditional breakpoints**: break only when expression evaluates truthy
- **DOM breakpoints**: break on subtree modification, attribute change, node removal
- **XHR/fetch breakpoints**: break when URL matches a pattern
- **Event listener breakpoints**: break on specific event types (click, keydown, etc.)

### Source Maps
- `.map` file structure: `version`, `sources`, `mappings`, `sourcesContent`
- Base64 VLQ encoding of source positions
- Original source mapping: minified line:col → original file:line:col
- `sourceMappingURL` comment at end of bundled files
- `x_google_ignoreList` for framework code exclusion

### Error Handling
- **Stack traces**: `Error.stack`, `console.trace()`, error boundaries
- **Unhandled promise rejection**: `window.onunhandledrejection`
- **Try/catch patterns**: synchronous wrapping, async/await error catching
- **Error types**: `SyntaxError`, `TypeError`, `ReferenceError`, `RangeError`, `AggregateError`
- **Custom errors**: `class AppError extends Error`

### Performance & Debugging APIs
- **Performance API**: `performance.now()`, `performance.mark()`, `performance.measure()`
- **MutationObserver**: watch DOM changes with callback, `observe(target, config)`
- **Network panel waterfall**: request timing breakdown (DNS, TCP, TLS, TTFB, download)
- **Coverage panel**: unused CSS/JS detection

## Operations

| Operation | Description |
|-----------|-------------|
| `check_syntax` | Validate bracket matching, quote balance, detect debugger/console calls |
| `lint` | Run eslint with configurable rules against JS files |
| `analyze_errors` | Scan for error patterns: unhandled promises, type errors, null refs, CORS |
| `verify_source_maps` | Check .map file existence and structure for each JS file |

## Usage

```yaml
- name: Check JS syntax and error patterns
  include_role:
    name: general_ludd.web.javascript_debug
  vars:
    js_files:
      - /src/app.js
      - /src/utils.js
      - /src/api.js
    operation: check_syntax
    debug_mode: true
    check_source_maps: true
```
