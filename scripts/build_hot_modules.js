#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");

const PLUGIN_DIR = path.resolve(__dirname, "..", ".opencode", "plugin");
const OUT_DIR = "/tmp";

const PLUGINS = [
  "enforce-deadline",
  "enforce-enhancement-ratio",
  "enforce-floor",
  "enforce-delegate",
  "enforce-make",
  "enforce-multitask",
  "enforce-no-suppressions",
  "enforce-no-wait",
  "enforce-session-start",
  "enforce-stop",
  "enforce-verified-claims",
  "enforce-clean-tree",
  "enforce-deletion-gate",
];

function tsToJs(content) {
  return content
    .replace(/import type \{ Plugin \} from "@opencode-ai\/plugin"/g, "// @opencode-ai/plugin (stripped)")
    .replace(/import \* as (\w+) from "node:(\w+)"/g, 'var $1 = require("node:$2");')
    .replace(/import \* as (\w+) from "(\w+)"/g, 'var $1 = require("$2");')
    .replace(/import \{ ([^}]+) \} from "[^"]+"/g, '// import { $1 } (stripped)')
    .replace(/export type \w+\s*=\s*[^;]+;/g, "")
    .replace(/export interface \w+\s*\{[^}]*\}/g, "")
    .replace(/export const /g, "var ")
    .replace(/export function /g, "function ")
    .replace(/export \{ [^}]+\};?\s*/g, "")
    .replace(/satisfies Plugin/g, "")
    .replace(/as const/g, "")
    .replace(/:\s*Record<[^>]+>/g, "")
    .replace(/:\s*Map<[^>]+>/g, "")
    .replace(/:\s*Promise<[^>]+>/g, "")
    .replace(/:\s*\{\s*\[key:\s*\w+\]\s*:\s*\w+\s*\}/g, "")
    .replace(/\bas\s+Record<[^>]+>/g, "")
    .replace(/\bas\s+string(\[\])?\b/g, "")
    .replace(/\bas\s+number(\[\])?\b/g, "")
    .replace(/\bas\s+boolean\b/g, "")
    .replace(/\bas\s+any(\[\])?\b/g, "")
    .replace(/\bas\s+void\b/g, "")
    .replace(/\bas\s+never\b/g, "")
    .replace(/\bas\s+unknown(\[\])?\b/g, "")
    .replace(/:\s*\w+\s*=\s*new\s+Set</g, " = new Set")
    .replace(/:\s*\w+\s*=\s*new\s+Map</g, " = new Map")
    .replace(/:\s*\w+\s*=\s*\{\s*\}/g, " = {}")
    .replace(/:\s*\w+\s*=\s*\[\]/g, " = []")
    .replace(/:\s*\w+\s*=\s*"/g, ' = "')
    .replace(/:\s*\w+\s*=\s*'/g, " = '")
    .replace(/:\s*\w+\s*=\s*\d+/g, " = 0")
    .replace(/:\s*\w+\s*=\s*true/g, " = true")
    .replace(/:\s*\w+\s*=\s*false/g, " = false")
    .replace(/:\s*\w+\s*=\s*null/g, " = null")
    .replace(/const (\w+): ([^=]+)=/g, "var $1 =")
    .replace(/let (\w+): ([^=]+)=/g, "var $1 =")
    .replace(/function (\w+)\(([^)]*)\): ([^{]+)\{/g, "function $1($2) {")
    .replace(/:\s+\w+(\[\])?\s*;/g, ";")
    .replace(/:\s+\w+(\[\])?\s*,/g, ",")
    .replace(/:\s+\w+(\[\])?\s*\)/g, ")")
    .replace(/: (string|number|boolean|any|void|null|undefined|never)\b/g, "")
    .replace(/catch \{/g, "catch (e) {")
    .replace(/catch\s*\n\s*\{/g, "catch (e) {")
  ;
}

function extractDefaultImplMethods(content) {
  const methods = {};

  const implMatch = content.match(/defaultImpl\s*=\s*\{/);
  if (!implMatch) return methods;

  const startIdx = implMatch.index + implMatch[0].length;

  let depth = 1;
  let pos = startIdx;
  let currentMethod = "";
  let currentBody = "";
  let inMethod = false;
  let braceDepth = 0;
  let parenDepth = 0;

  for (; pos < content.length; pos++) {
    const ch = content[pos];

    if (!inMethod) {
      const slice = content.slice(pos, pos + 80);
      const methodMatch = slice.match(/^(\s*)"([^"]+)"(\s*[:=])/);
      if (methodMatch) {
        const methodName = methodMatch[2];
        currentMethod = methodName;
        inMethod = true;
        currentBody = "";
        braceDepth = 0;
        parenDepth = 0;
        let methodStart = pos + methodMatch[0].length;

        while (methodStart < content.length && content[methodStart] !== "{") methodStart++;
        if (content[methodStart] === "{") {
          pos = methodStart;
          braceDepth = 1;
          currentBody = "{";
        }
        continue;
      }
    }

    if (inMethod) {
      currentBody += ch;
      if (ch === "{") braceDepth++;
      else if (ch === "}") {
        braceDepth--;
        if (braceDepth === 0) {
          methods[currentMethod] = currentBody;
          currentMethod = "";
          currentBody = "";
          inMethod = false;
        }
      }
    }
  }

  return methods;
}

function buildPlugin(name) {
  const srcPath = path.join(PLUGIN_DIR, `${name}.ts`);
  const outPath = path.join(OUT_DIR, `gludd-hot-${name}.js`);

  if (!fs.existsSync(srcPath)) {
    console.log(`  SKIP ${name}: source not found`);
    return false;
  }

  let content = fs.readFileSync(srcPath, "utf8");

  if (!content.includes("defaultImpl")) {
    console.log(`  SKIP ${name}: not yet converted to proxy pattern (no defaultImpl)`);
    return false;
  }

  const js = tsToJs(content);
  const methods = extractDefaultImplMethods(js);

  // Debug: show what we found
  const hasDefaultImpl = js.includes("defaultImpl");
  const defaultImplIdx = js.indexOf("defaultImpl");
  const snippet = defaultImplIdx >= 0 ? js.substring(defaultImplIdx, defaultImplIdx + 200) : "(not found)";
  console.log(`  DEBUG ${name}: defaultImpl=${hasDefaultImpl}, pos=${defaultImplIdx}, snippet=${JSON.stringify(snippet)}`);

  if (Object.keys(methods).length === 0) {
    console.log(`  SKIP ${name}: no hook methods extracted from defaultImpl`);
    return false;
  }

  let out = `// Hot-reload module for ${name}\n`;
  out += `// Generated ${new Date().toISOString()}\n`;
  out += `// Overrides compiled-in defaultImpl hooks.  Edit ${name}.ts, run make hot-reload-plugins,\n`;
  out += `// and the next hook invocation picks up changes without restart.\n\n`;

  for (const [hookName, body] of Object.entries(methods)) {
    const fnBody = body.trim();
    out += `exports["${hookName}"] = async function(...args) ${fnBody};\n\n`;
    console.log(`    hook: ${hookName} (${fnBody.length} bytes)`);
  }

  fs.writeFileSync(outPath, out, "utf8");
  console.log(`  BUILT ${name} → ${outPath} (${Object.keys(methods).length} hooks)`);
  return true;
}

function status() {
  console.log("=== hot-reload module status ===\n");
  try {
    const files = fs.readdirSync(OUT_DIR)
      .filter(f => f.startsWith("gludd-hot-"))
      .sort();
    if (files.length === 0) {
      console.log("  (none built)");
    } else {
      for (const f of files) {
        const p = path.join(OUT_DIR, f);
        const stat = fs.statSync(p);
        const age = Math.round((Date.now() - stat.mtimeMs) / 1000);
        const ageStr = age < 120 ? `${age}s ago` : `${Math.round(age / 60)}m ago`;
        console.log(`  ${f.padEnd(50)} ${ageStr.padStart(8)}  ${stat.size}B`);
      }
    }
    console.log("");
  } catch (e) {
    console.log(`  (error reading status: ${e.message})`);
  }
}

function main() {
  const args = process.argv.slice(2);
  if (args[0] === "--status" || args[0] === "status") {
    status();
    return;
  }

  console.log("=== build_hot_modules.js ===\n");

  let built = 0;
  for (const name of PLUGINS) {
    if (buildPlugin(name)) built++;
  }

  console.log(`\nBuilt ${built}/${PLUGINS.length} hot-reload modules in ${OUT_DIR}/`);
  status();
}

main();
