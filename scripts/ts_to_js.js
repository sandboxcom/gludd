"use strict";

const esbuild = require("esbuild");

function tsToJs(content) {
  // Pre-process: handle createRequire and import.meta patterns
  content = content
    .replace(/const\s+\w+\s*=\s*createRequire\(import\.meta\.url\)\s*;?\s*/g, "")
    .replace(/\bnodeRequire\(/g, "require(")
    .replace(/void\s+import\.meta\.url\s*;?/g, "")
    .replace(/import\.meta\.url/g, '""');

  let js = esbuild.transformSync(content, {
    loader: "ts",
    format: "esm",
    target: "node22",
  }).code;

  // Post-process: convert ESM output to CJS for hot module loading.
  // Order matters: specific strips before generic conversions.
  // Each replacement consumes the entire line including optional trailing semicolon and newline.

  // 1. Strip node:child_process and node:module imports (sandbox stubs provide these)
  js = js
    .replace(/import\s+\{[^}]*\}\s+from\s+"node:child_process"\s*;?\s*\n?/g, "")
    .replace(/import\s+\{[^}]*\}\s+from\s+"node:module"\s*;?\s*\n?/g, "")
    .replace(/import\s+\*\s+as\s+\w+\s+from\s+"node:child_process"\s*;?\s*\n?/g, "")
    .replace(/import\s+\*\s+as\s+\w+\s+from\s+"node:module"\s*;?\s*\n?/g, "");

  // 2. Strip shared/lib imports (sandbox stubs provide these)
  js = js
    .replace(/import\s+\{[^}]*\}\s+from\s+"\.\.\/lib\/shared\.(?:ts|js)"\s*;?\s*\n?/g, "")
    .replace(/import\s+\{[^}]*\}\s+from\s+"\.\.\/lib\/hot_reload\.(?:ts|js)"\s*;?\s*\n?/g, "");

  // 3. Convert remaining node:* named imports to require()
  js = js.replace(
    /import\s+\{([^}]+)\}\s+from\s+"node:([^"]+)"\s*;?/g,
    "const {$1} = require(\"node:$2\");"
  );

  // 4. Convert remaining node:* namespace imports to require()
  js = js.replace(
    /import\s+\*\s+as\s+(\w+)\s+from\s+"node:([^"]+)"\s*;?/g,
    "const $1 = require(\"node:$2\");"
  );

  // 5. Strip remaining .ts imports
  js = js.replace(
    /import\s+\{[^}]*\}\s+from\s+"[^"]+\.ts"\s*;?\s*\n?/g,
    ""
  );

  // 6. Strip export blocks
  js = js
    .replace(/export\s*\{[^}]*\}\s*;?\s*/g, "")
    .replace(/export\s+default\s+\w+\s*;?\s*/g, "");

  return js;
}

module.exports = { tsToJs };
