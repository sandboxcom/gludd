#!/usr/bin/env node

const fs = require("node:fs");
const { createRequire } = require("node:module");
const path = require("node:path");
const vm = require("node:vm");
const { spawnSync } = require("node:child_process");
const { pathToFileURL } = require("node:url");
const opencodeRequire = createRequire(path.resolve(__dirname, "..", ".opencode", "package.json"));
const esbuild = opencodeRequire("esbuild");

const PLUGIN_DIR = path.resolve(__dirname, "..", ".opencode", "plugin");
const OUT_DIR = "/tmp";
const HOT_PREFIX = process.env.GLUDD_HOT_MODULE_PREFIX || path.join(OUT_DIR, "gludd-hot-");
const BUILD_FAILURES = [];

const PLUGINS = [
  "enforce-additive-task",
  "enforce-anti-essay",
  "enforce-audit",
  "enforce-batch-push",
  "enforce-branch-discipline",
  "enforce-deadline",
  "enforce-context",
  "enforce-depth",
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
  "enforce-deliverable",
  "enforce-directives",
  "enforce-objective",
  "enforce-floor-v2",
  "enforce-tdd",
  "enforce-test-integrity",
  "enforce-task-tracking",
  "enforce-worktree",
  "enforce-no-ci-poll",
  "enforce-release-deadline",
];

function extractHotLookupName(content, sourceName) {
  const matches = [...content.matchAll(/loadHotModule\(\s*["']([^"']+)["']/g)]
    .map((match) => match[1]);
  const unique = [...new Set(matches)];
  if (unique.length !== 1) {
    const message = `${sourceName}: expected one loadHotModule lookup key, found ${JSON.stringify(unique)}`;
    BUILD_FAILURES.push(message);
    console.log(`  FAIL ${message}`);
    return null;
  }
  return unique[0];
}

function resolveImplementationSource(srcPath, content) {
  if (content.includes("defaultImpl")) return { srcPath, content };
  const match = content.match(/import\s+\w+\s+from\s+["'](\.\/impl\/[A-Za-z0-9_-]+\.ts)["']/);
  if (!match) return { srcPath, content };
  const implPath = path.resolve(path.dirname(srcPath), match[1]);
  if (!fs.existsSync(implPath)) return { srcPath, content };
  const implContent = fs.readFileSync(implPath, "utf8");
  if (!/loadHotModule\(\s*["'][^"']+["']/.test(implContent)) {
    return { srcPath, content };
  }
  return { srcPath: implPath, content: implContent };
}

function compileDefaultImpl(srcPath, content) {
  const source = `${content}\nexport { defaultImpl as __gluddHotModule };\n`;
  const result = esbuild.buildSync({
    stdin: {
      contents: source,
      loader: "ts",
      resolveDir: path.dirname(srcPath),
      sourcefile: srcPath,
    },
    bundle: true,
    platform: "node",
    format: "cjs",
    target: "node22",
    write: false,
    legalComments: "none",
    logLevel: "silent",
    define: {
      "import.meta.url": JSON.stringify(pathToFileURL(srcPath).href),
    },
    footer: {
      js: "module.exports = module.exports.__gluddHotModule;",
    },
  });
  return result.outputFiles[0].text;
}

function failBuild(name, outPath, detail) {
  const message = `${name}: ${detail}`;
  BUILD_FAILURES.push(message);
  try { fs.unlinkSync(outPath); } catch {}
  console.log(`  FAIL ${message} — removed`);
  return false;
}

function buildPlugin(name) {
  const srcPath = path.join(PLUGIN_DIR, `${name}.ts`);
  if (!fs.existsSync(srcPath)) {
    console.log(`  SKIP ${name}: source not found`);
    return false;
  }

  const facadeContent = fs.readFileSync(srcPath, "utf8");
  const resolved = resolveImplementationSource(srcPath, facadeContent);
  const content = resolved.content;
  if (!content.includes("defaultImpl")) {
    console.log(`  SKIP ${name}: not yet converted to proxy pattern (no defaultImpl)`);
    return false;
  }

  const hotLookupName = extractHotLookupName(content, name);
  if (hotLookupName === null) return false;
  const outPath = `${HOT_PREFIX}${hotLookupName}.js`;
  const legacyOutPath = `${HOT_PREFIX}${name}.js`;
  if (legacyOutPath !== outPath) {
    try { fs.unlinkSync(legacyOutPath); } catch {}
  }

  let out;
  try {
    out = compileDefaultImpl(resolved.srcPath, content);
  } catch (error) {
    return failBuild(name, outPath, `esbuild compilation failed (${error.message})`);
  }
  fs.writeFileSync(outPath, out, "utf8");

  try {
    new vm.Script(out, { filename: outPath });
  } catch (error) {
    return failBuild(name, outPath, `generated module has invalid JS (${error.message})`);
  }

  let probe;
  try {
    probe = spawnSync(
      "node",
      ["-e", `const m = require(${JSON.stringify(outPath)}); process.stdout.write(JSON.stringify(Object.keys(m || {})));`],
      { timeout: 10000, encoding: "utf8", env: { ...process.env, OPENCODE_SUBAGENT: "0" } },
    );
  } catch (error) {
    return failBuild(name, outPath, `require probe crashed (${error.message})`);
  }
  if (probe.status !== 0) {
    const detail = (probe.stderr || "").split("\n").find((line) => line.trim()) || "unknown error";
    return failBuild(name, outPath, `generated module failed to require (${detail.trim()})`);
  }

  let exported = [];
  try { exported = JSON.parse(probe.stdout || "[]"); } catch {}
  if (exported.length === 0) {
    return failBuild(name, outPath, "module exports zero hooks");
  }
  for (const hookName of exported) console.log(`    hook: ${hookName}`);
  console.log(`  BUILT ${name} → ${outPath} (${exported.length} hooks)`);
  return true;
}

function status() {
  console.log("=== hot-reload module status ===\n");
  try {
    const base = path.dirname(HOT_PREFIX);
    const prefix = path.basename(HOT_PREFIX);
    const files = fs.readdirSync(OUT_DIR)
      .filter(f => f.startsWith(prefix))
      .sort();
    if (files.length === 0) {
      console.log("  (none built)");
    } else {
      for (const f of files) {
        const p = path.join(base, f);
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
  if (BUILD_FAILURES.length > 0) {
    console.error(`Hot-module build failed: ${BUILD_FAILURES.length} invalid module(s)`);
    for (const failure of BUILD_FAILURES) console.error(`  FAIL ${failure}`);
    process.exitCode = 1;
  }
}

main();
