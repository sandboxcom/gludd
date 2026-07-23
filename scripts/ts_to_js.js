"use strict";

let esbuild;
try {
  esbuild = require("esbuild");
} catch {
  esbuild = null;
}

function fallbackTsToJs(content) {
  return content
    .replace(/^import type .*$/gm, "")
    .replace(/:\s*\w+(\[\])?(\s*\|(\s*\w+(\[\])?))*\s*(?=[,;=)\s}])/g, "")
    .replace(/^export default \(/gm, "module.exports = (")
    .replace(/satisfies Plugin\)/g, "")
    .replace(/\.exports(\))(.*)/, ".exports)");
}

function tsToJs(content) {
  content = content
    .replace(/const\s+\w+\s*=\s*createRequire\(import\.meta\.url\)\s*;?\s*/g, "")
    .replace(/\bnodeRequire\(/g, "require(")
    .replace(/void\s+import\.meta\.url\s*;?/g, "")
    .replace(/import\.meta\.url/g, '""');

  let js;
  try {
    js = esbuild.transformSync(content, {
      loader: "ts",
      format: "esm",
      target: "node22",
    }).code;
  } catch {
    content = fallbackTsToJs(content);
    try {
      js = esbuild.transformSync(content, {
        loader: "ts",
        format: "esm",
        target: "node22",
      }).code;
    } catch {
      js = content;
    }
  }

  js = js
    .replace(/import\s+\{[^}]*\}\s+from\s+"node:child_process"\s*;?\s*\n?/g, "")
    .replace(/import\s+\{[^}]*\}\s+from\s+"node:module"\s*;?\s*\n?/g, "")
    .replace(/import\s+\*\s+as\s+\w+\s+from\s+"node:child_process"\s*;?\s*\n?/g, "")
    .replace(/import\s+\*\s+as\s+\w+\s+from\s+"node:module"\s*;?\s*\n?/g, "");

  js = js
    .replace(/import\s+\{[^}]*\}\s+from\s+"\.\.\/lib\/shared\.(?:ts|js)"\s*;?\s*\n?/g, "")
    .replace(/import\s+\{[^}]*\}\s+from\s+"\.\.\/lib\/hot_reload\.(?:ts|js)"\s*;?\s*\n?/g, "");

  js = js.replace(
    /import\s+\{([^}]+)\}\s+from\s+"node:([^"]+)"\s*;?/g,
    "const {$1} = require(\"node:$2\");"
  );

  js = js.replace(
    /import\s+\*\s+as\s+(\w+)\s+from\s+"node:([^"]+)"\s*;?/g,
    "const $1 = require(\"node:$2\");"
  );

  js = js.replace(
    /import\s+\{[^}]*\}\s+from\s+"[^"]+\.ts"\s*;?\s*\n?/g,
    ""
  );

  js = js
    .replace(/export\s*\{[^}]*\}\s*;?\s*/g, "")
    .replace(/export\s+default\s+\w+\s*;?\s*/g, "");

  return js;
}

module.exports = { tsToJs };
