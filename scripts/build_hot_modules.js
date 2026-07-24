#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { spawnSync } = require("node:child_process");

const PLUGIN_DIR = path.resolve(__dirname, "..", ".opencode", "plugin");
const OUT_DIR = "/tmp";
const HOT_PREFIX = process.env.GLUDD_HOT_MODULE_PREFIX || path.join(OUT_DIR, "gludd-hot-");

const PLUGINS = [
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
  "enforce-objective",
  "enforce-tdd",
  "enforce-test-integrity",
  "enforce-worktree",
];

function tsToJs(content) {
  return require("./ts_to_js.js").tsToJs(content);
}

function extractDefaultImplMethods(content) {
  const methods = {};

  const implMatch = content.match(/defaultImpl\s*=\s*\{/);
  if (!implMatch) return methods;

  const objStart = implMatch.index + implMatch[0].length;

  // Find the end of defaultImpl by locating the next structural marker
  // (the "PROXY" comment or "export default" that always follows defaultImpl)
  const afterDefault = content.substring(objStart);
  const proxyMarker = afterDefault.search(/var stdin_default\b/);
  if (proxyMarker < 0) return methods;

  // Walk backward from the proxy marker to find the closing }; of defaultImpl
  let objEnd = objStart + proxyMarker;
  while (objEnd > objStart && content[objEnd] !== "}") objEnd--;
  if (objEnd <= objStart) return methods;

  // Extract methods from within the defaultImpl body using positional
  // boundaries instead of brace counting (which fails on {} in strings)
  const bodySlice = content.substring(objStart, objEnd);
  const methodRegex = /^(\s*)"([^"]+)"(\s*[:=])/gm;
  const methodPositions = [];
  let match;
  while ((match = methodRegex.exec(bodySlice)) !== null) {
    methodPositions.push({ name: match[2], pos: match.index + match[0].length });
  }

  for (let i = 0; i < methodPositions.length; i++) {
    const { name, pos } = methodPositions[i];
    const nextPos = (i + 1 < methodPositions.length) ? methodPositions[i + 1].pos : bodySlice.length;

    // Find => to locate arrow function body (skip past type annotation remnants)
    const arrowIdx = bodySlice.indexOf("=>", pos);
    let braceIdx = -1;
    if (arrowIdx >= 0 && arrowIdx < nextPos) {
      braceIdx = bodySlice.indexOf("{", arrowIdx + 2);
    }
    if (braceIdx < 0 || braceIdx >= nextPos) {
      braceIdx = bodySlice.indexOf("{", pos);
    }
    if (braceIdx < 0 || braceIdx >= nextPos) continue;

    let body = bodySlice.substring(braceIdx, nextPos);
    body = body.replace(/\n\s*"[^"]*"\s*:\s*$/, "").trimEnd();
    body = body.replace(/,\s*$/, "").trimEnd();
    methods[name] = body;
  }

  return methods;
}

function buildPlugin(name) {
  const srcPath = path.join(PLUGIN_DIR, `${name}.ts`);
  const outPath = `${HOT_PREFIX}${name}.js`;

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

  // Inject shared utility stubs — hot modules are eval'd via new Function()
  // in a bare sandbox, so functions imported from shared.ts must be provided inline.
  out += `// === shared utility stubs (sandbox context) ===\n`;
  out += `var _fs = require("node:fs");\n`;
  out += `var _path = require("node:path");\n`;
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
  out += `function isDisengaged() {\n`;
  out += `  try {\n`;
  out += `    if (!_fs.existsSync("/tmp/gludd-disengage-enforcement")) return false;\n`;
  out += `    var d = JSON.parse(_fs.readFileSync("/tmp/gludd-disengage-enforcement", "utf8"));\n`;
  out += `    return typeof d.disengage_until === "number" && d.disengage_until > Date.now();\n`;
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

  for (const [hookName, body] of Object.entries(methods)) {
    const fnBody = body.trim();
    // Map ...args parameters to input/output for body references
    const mapped = fnBody.replace(
      /^\{/,
      "{ var input = callArgs[0] || {}; var output = callArgs[1]; "
    );
    out += `exports["${hookName}"] = async function(...callArgs) ${mapped};\n\n`;
    console.log(`    hook: ${hookName} (${fnBody.length} bytes)`);
  }

  fs.writeFileSync(outPath, out, "utf8");

  // Validate the generated module. Three outcomes:
  //   1. Parse/require failure — HARMLESS: loadHotModule() catches the error
  //      and falls back to the compiled-in defaultImpl. Keep the file, warn.
  //   2. Loads but exports none of the extracted hooks — DANGEROUS: the proxy
  //      does `fn ? await fn(...) : undefined`, so an empty-export module
  //      silently DISABLES the plugin's enforcement. Delete the file so
  //      loadHotModule falls back to defaultImpl (missing file => defaults).
  //   3. Loads with the expected hook exports — fully functional hot module.
  let parseOk = true;
  try {
    new vm.Script(out, { filename: outPath });
  } catch (e) {
    parseOk = false;
    console.log(`  WARN ${name}: generated module has invalid JS (${e.message}) — kept; loadHotModule will fail-open to compiled-in defaultImpl`);
  }
  if (parseOk) {
    const hookNames = Object.keys(methods);
    let probe;
    try {
      probe = spawnSync("node", ["-e",
        `const m = require(${JSON.stringify(outPath)}); process.stdout.write(JSON.stringify(Object.keys(m)));`,
      ], { timeout: 10000, encoding: "utf8" });
    } catch (e) {
      console.log(`  WARN ${name}: require() probe crashed (${e.message}) — kept; loadHotModule will fail-open to compiled-in defaultImpl`);
      console.log(`  BUILT ${name} → ${outPath} (${Object.keys(methods).length} hooks)`);
      return true;
    }
    if (probe.status !== 0) {
      const errLine = (probe.stderr || "").split("\n").find(l => l.trim()) || "unknown error";
      console.log(`  WARN ${name}: generated module failed to require (${errLine.trim()}) — kept; loadHotModule will fail-open to compiled-in defaultImpl`);
    } else {
      let exported = [];
      try { exported = JSON.parse(probe.stdout || "[]"); } catch (e) { exported = []; }
      const missing = hookNames.filter(h => !exported.includes(h));
      if (missing.length === hookNames.length) {
        fs.unlinkSync(outPath);
        console.log(`  SKIP ${name}: module loads but exports ZERO hooks (would silently disable enforcement) — removed; plugin falls back to compiled-in defaultImpl`);
        return false;
      }
      if (missing.length > 0) {
        console.log(`  WARN ${name}: module missing hook exports: ${missing.join(", ")}`);
      }
    }
  }

  console.log(`  BUILT ${name} → ${outPath} (${Object.keys(methods).length} hooks)`);
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
}

main();
