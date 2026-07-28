#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { spawnSync } = require("node:child_process");

const PLUGIN_DIR = path.resolve(__dirname, "..", ".opencode", "plugin");
const PLUGIN_TEST_EXPORTS = path.resolve(
  PLUGIN_DIR,
  "..",
  "lib",
  "plugin_test_exports.ts",
);
const OUT_DIR = "/tmp";
const HOT_PREFIX = process.env.GLUDD_HOT_MODULE_PREFIX || path.join(OUT_DIR, "gludd-hot-");

const PLUGINS = [
  "enforce-anti-essay",
  "enforce-audit",
  "enforce-batch-push",
  "enforce-branch-discipline",
  "enforce-deadline",
  "enforce-context",
  "enforce-deliverable",
  "enforce-depth",
  "enforce-enhancement-ratio",
  "enforce-floor",
  "enforce-floor-v2",
  "enforce-delegate",
  "enforce-make",
  "enforce-multitask",
  "enforce-no-ci-poll",
  "enforce-no-suppressions",
  "enforce-no-wait",
  "enforce-release-deadline",
  "enforce-session-start",
  "enforce-stop",
  "enforce-task-tracking",
  "enforce-verified-claims",
  "enforce-clean-tree",
  "enforce-deletion-gate",
  "enforce-objective",
  "enforce-tdd",
  "enforce-test-integrity",
  "enforce-worktree",
];

function tsToJs(content) {
  return require("./ts_to_js.js").tsToJs(content);
}

function hotModuleName(content, sourceName) {
  const names = new Set(
    [...content.matchAll(/loadHotModule\(\s*["']([^"']+)["']/g)]
      .map((match) => match[1]),
  );
  if (names.size !== 1) {
    throw new Error(
      `${sourceName}: expected exactly one loadHotModule lookup name, found ${[...names].join(", ") || "none"}`,
    );
  }
  return [...names][0];
}

function importedTestHelperPrelude(content) {
  const match = content.match(
    /import\s*\{([^}]*)\}\s*from\s*["']\.\.\/lib\/plugin_test_exports\.ts["']\s*;?/,
  );
  if (!match) return "";

  const bindings = match[1]
    .split(",")
    .map((binding) => binding.trim())
    .filter(Boolean)
    .map((binding) => {
      const [imported, local = imported] = binding.split(/\s+as\s+/);
      return { imported: imported.trim(), local: local.trim() };
    });
  const helperJs = tsToJs(fs.readFileSync(PLUGIN_TEST_EXPORTS, "utf8"));
  const returned = bindings
    .map(({ imported, local }) => (
      imported === local ? imported : `${local}: ${imported}`
    ))
    .join(", ");
  const locals = bindings.map(({ local }) => local).join(", ");

  return [
    "const __pluginTestExports = (() => {",
    helperJs,
    `return { ${returned} };`,
    "})();",
    `const { ${locals} } = __pluginTestExports;`,
    "",
  ].join("\n");
}

function hasDefaultImpl(content) {
  return /\b(?:const|let|var)\s+defaultImpl\b/.test(content);
}

function implementationSource(srcPath, content) {
  if (hasDefaultImpl(content)) return { path: srcPath, content };
  const match = content.match(
    /import\s+impl\s+from\s+["']\.\/impl\/([^"']+\.ts)["']\s*;?/,
  );
  if (!match) return null;
  const implPath = path.resolve(path.dirname(srcPath), "impl", match[1]);
  if (!fs.existsSync(implPath)) return null;
  const implContent = fs.readFileSync(implPath, "utf8");
  return hasDefaultImpl(implContent)
    ? { path: implPath, content: implContent }
    : null;
}

function buildPlugin(name) {
  const srcPath = path.join(PLUGIN_DIR, `${name}.ts`);

  if (!fs.existsSync(srcPath)) {
    console.log(`  SKIP ${name}: source not found`);
    return false;
  }

  const entryContent = fs.readFileSync(srcPath, "utf8");
  const source = implementationSource(srcPath, entryContent);
  if (!source) {
    console.log(`  SKIP ${name}: not yet converted to proxy pattern (no defaultImpl)`);
    return false;
  }
  const content = source.content;

  let lookupName;
  try {
    lookupName = hotModuleName(content, name);
  } catch (e) {
    console.error(`  FAIL ${name}: ${e.message}`);
    return false;
  }
  const outPath = `${HOT_PREFIX}${lookupName}.js`;

  const js = tsToJs(content);
  if (!/\bdefaultImpl\s*=/.test(js)) {
    console.log(`  SKIP ${name}: transpiled output has no defaultImpl`);
    return false;
  }

  let out = `// Hot-reload module for ${name}\n`;
  out += `// Generated ${new Date().toISOString()}\n`;
  out += `// Source ${path.relative(path.resolve(__dirname, ".."), source.path)}\n`;
  out += `// Overrides compiled-in defaultImpl hooks. Run make hot-reload-plugins after edits,\n`;
  out += `// and the next hook invocation picks up changes without restart.\n\n`;

  // Inject shared utility stubs — hot modules are eval'd via new Function()
  // in a bare sandbox, so functions imported from shared.ts must be provided inline.
  out += `// === shared utility stubs (sandbox context) ===\n`;
  out += `var _fs = require("node:fs");\n`;
  out += `var _path = require("node:path");\n`;
  out += `var createRequire = require("node:module").createRequire;\n`;
  out += `function isSubagent() {\n`;
  out += `  if (process.env.OPENCODE_SUBAGENT === "1") return true;\n`;
  out += `  try { return _fs.existsSync("/tmp/gludd-subagent-" + process.pid + ".json"); } catch (e) { return false; }\n`;
  out += `}\n`;
  out += `function reportAlive(pluginName) {\n`;
  out += `  try {\n`;
  out += `    var p = process.env.GLUDD_ALIVE_PATH || "/tmp/gludd-plugin-alive.json";\n`;
  out += `    var a = {}; try { if (_fs.existsSync(p)) a = JSON.parse(_fs.readFileSync(p, "utf8")); } catch (e) {}\n`;
  out += `    var now = Date.now();\n`;
  out += `    var existing = a[pluginName] || {};\n`;
  out += `    a[pluginName] = { last_seen: now, ts: now, loaded: existing.loaded || now };\n`;
  out += `    _fs.writeFileSync(p, JSON.stringify(a), "utf8");\n`;
  out += `  } catch (e) {}\n`;
  out += `}\n`;
  out += `function writeHeartbeat(pluginName) {\n`;
  out += `  try {\n`;
  out += `    _fs.writeFileSync("/tmp/gludd-plugin-heartbeat-" + pluginName + ".json",\n`;
  out += `      JSON.stringify({ plugin: pluginName, ts: Date.now(), pid: process.pid }), "utf8");\n`;
  out += `  } catch (e) {}\n`;
  out += `}\n`;
  out += `var _childProcess = require("node:child_process");\n`;
  out += `var execSync = _childProcess.execSync;\n`;
  out += `var _spawn = _childProcess.spawn;\n`;
  out += `function spawn(cmd, args, opts) { return _spawn(cmd, args, opts); }\n`;
  out += `var _DISPATCH_TOOLS = ["task", "agent", "workflow"];\n`;
  out += `var READ_TOOLS = ["read", "grep", "glob"];\n`;
  out += `function isDispatchTool(tool) { return _DISPATCH_TOOLS.includes(tool); }\n`;
  out += `function isReadTool(tool) { return READ_TOOLS.includes(tool); }\n`;
  out += `function isDisengaged(opts) {\n`;
  out += `  try {\n`;
  out += `    var nextPath = process.env.GLUDD_DISENGAGE_NEXT_PATH || "/tmp/gludd-disengage-next";\n`;
  out += `    if (_fs.existsSync(nextPath)) {\n`;
  out += `      try { _fs.unlinkSync(nextPath); } catch (e) {}\n`;
  out += `      return true;\n`;
  out += `    }\n`;
  out += `    var disengagePath = process.env.GLUDD_DISENGAGE_PATH || "/tmp/gludd-watchdog-disengage.json";\n`;
  out += `    if (!_fs.existsSync(disengagePath)) return false;\n`;
  out += `    var d = JSON.parse(_fs.readFileSync(disengagePath, "utf8"));\n`;
  out += `    if (d.expires === 1) {\n`;
  out += `      try { _fs.unlinkSync(disengagePath); } catch (e) {}\n`;
  out += `      return true;\n`;
  out += `    }\n`;
  out += `    if (typeof d.disengage_until !== "number") return false;\n`;
  out += `    var now = Date.now();\n`;
  out += `    var maxMs = opts && typeof opts.maxMs === "number" ? opts.maxMs : 300000;\n`;
  out += `    return Math.min(d.disengage_until, now + maxMs) > now;\n`;
  out += `  } catch (e) { return false; }\n`;
  out += `}\n`;
  out += `function readJsonFile(filePath, defaultVal) {\n`;
  out += `  try {\n`;
  out += `    if (_fs.existsSync(filePath)) return JSON.parse(_fs.readFileSync(filePath, "utf8"));\n`;
  out += `  } catch (e) {}\n`;
  out += `  return defaultVal;\n`;
  out += `}\n`;
  out += `function writeJsonFile(filePath, data) {\n`;
  out += `  try { _fs.writeFileSync(filePath, JSON.stringify(data), "utf8"); } catch (e) {}\n`;
  out += `}\n`;
  out += `function getProjectRoot() {
  return process.env.GLUDD_PROJECT_ROOT || process.cwd();
}
`;
  out += `function getSessionStartMtimeMs() {
  try { return require("fs").statSync("/tmp/gludd-session-start.json").mtimeMs; } catch { return 0; }
}
`;
  out += `function isStateFileMtimeStale(stateFilePath) {
  try {
    var sessionMtime = getSessionStartMtimeMs();
    if (sessionMtime === 0) return false;
    if (!_fs.existsSync(stateFilePath)) return false;
    return _fs.statSync(stateFilePath).mtimeMs < sessionMtime;
  } catch(e) { return false; }
}
`;
  out += `function readSharedStreak() {
  try { return JSON.parse(require("fs").readFileSync("/tmp/gludd-shared-streak.json","utf8")); } catch { return {count:0}; }
}
`;
  out += `function writeSharedStreak(data) {
  try { require("fs").writeFileSync("/tmp/gludd-shared-streak.json", JSON.stringify(data)); } catch {}
}
`;
  out += `function updateSharedStreak(plugin, options) {
  const s = readSharedStreak();
  s.count = (s.count || 0) + 1;
  s.plugin = plugin;
  s.max = options?.max || 5;
  writeSharedStreak(s);
  return s;
}
`;
  out += `// === end shared stubs ===\n\n`;
  out += importedTestHelperPrelude(content);

  // Include everything from the js output EXCEPT the export default block.
  // This gives hooks access to all module-level vars/functions/consts.
  // The defaultImpl object is harmless (it's just a module-level var at this point).
  let exportIdx = js.lastIndexOf('export {');
  if (exportIdx < 0) exportIdx = js.lastIndexOf('export default');
  let moduleBody = js;
  if (exportIdx >= 0) {
    moduleBody = js.substring(0, exportIdx);
  }
  // Clean up any leftover import-comment lines from tsToJs
  moduleBody = moduleBody
    .replace(/^\/\/.*import.*stripped.*\n/gm, "");
  out += "// === module-level declarations ===\n";
  out += moduleBody.trim() + "\n";
  out += "// === end module-level declarations ===\n\n";

  // If the module does not already declare DISPATCH_TOOLS, alias it from _DISPATCH_TOOLS
  if (!/\b(const|var|let)\s+DISPATCH_TOOLS\b/.test(moduleBody)) {
    out += `var DISPATCH_TOOLS = _DISPATCH_TOOLS;
`;
  }

  // Export the transpiled fallback functions directly. Reconstructing function
  // bodies with regexes used to make the last hook absorb declarations that
  // followed defaultImpl (notably handleTextComplete in enforce-multitask),
  // producing a dangling `};` and invalid JavaScript. The real transpiler has
  // already built the runtime object, so copying its functions is both simpler
  // and preserves the exact hook signatures and behavior.
  out += `for (const [hookName, hook] of Object.entries(defaultImpl)) {\n`;
  out += `  if (typeof hook === "function") exports[hookName] = hook;\n`;
  out += `}\n`;

  // Validate before atomically publishing the module. A malformed file must
  // never replace the last known-good module, even though loadHotModule has a
  // compiled-in fallback.
  try {
    new vm.Script(out, { filename: outPath });
  } catch (e) {
    console.error(`  FAIL ${name}: generated module has invalid JS (${e.message})`);
    return false;
  }

  const candidatePath = `${outPath}.candidate-${process.pid}.js`;
  fs.writeFileSync(candidatePath, out, "utf8");
  let probe;
  try {
    probe = spawnSync("node", ["-e",
      `const m = require(${JSON.stringify(candidatePath)}); process.stdout.write(JSON.stringify(Object.keys(m)));`,
    ], { timeout: 10000, encoding: "utf8" });
  } catch (e) {
    fs.unlinkSync(candidatePath);
    console.error(`  FAIL ${name}: require() probe crashed (${e.message})`);
    return false;
  }
  if (probe.status !== 0) {
    fs.unlinkSync(candidatePath);
    const errLine = (probe.stderr || "").split("\n").find(l => l.trim()) || "unknown error";
    console.error(`  FAIL ${name}: generated module failed to require (${errLine.trim()})`);
    return false;
  }

  let exported = [];
  try {
    exported = JSON.parse(probe.stdout || "[]");
  } catch {
    exported = [];
  }
  if (exported.length === 0) {
    fs.unlinkSync(candidatePath);
    console.error(`  FAIL ${name}: generated module exports zero hooks`);
    return false;
  }

  fs.renameSync(candidatePath, outPath);
  for (const hookName of exported) {
    console.log(`    hook: ${hookName}`);
  }
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
  let expected = 0;
  for (const name of PLUGINS) {
    const srcPath = path.join(PLUGIN_DIR, `${name}.ts`);
    if (
      fs.existsSync(srcPath)
      && implementationSource(srcPath, fs.readFileSync(srcPath, "utf8"))
    ) {
      expected++;
    }
    if (buildPlugin(name)) built++;
  }

  console.log(`\nBuilt ${built}/${PLUGINS.length} hot-reload modules in ${OUT_DIR}/`);
  status();
  if (built !== expected) {
    console.error(`Hot-reload build failed: built ${built}/${expected} proxy-pattern plugins`);
    process.exitCode = 1;
  }
}

main();
