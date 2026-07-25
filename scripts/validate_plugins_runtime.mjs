#!/usr/bin/env node --experimental-strip-types
/**
 * scripts/validate_plugins_runtime.mjs — runtime validation of all .opencode plugins.
 *
 * For each plugin .ts file, spawns a child process that attempts to dynamically
 * import the module. Catches:
 *   - SyntaxError (unparseable TypeScript under --experimental-strip-types)
 *   - ReferenceError (undefined symbols — the exact bug this was built for)
 *   - Import resolution failures
 *   - Module evaluation errors
 *
 * Usage: node --experimental-strip-types scripts/validate_plugins_runtime.mjs [--dir .opencode/plugin]
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");

const defaultDirs = [".opencode/plugin", ".opencode/plugins"];
const dirs = process.argv.length > 2
  ? [path.resolve(ROOT, process.argv[2])]
  : defaultDirs.map(d => path.resolve(ROOT, d));

function collectFiles(dir) {
  const results = [];
  if (!fs.existsSync(dir)) return results;
  const entries = fs.readdirSync(dir);
  for (const entry of entries) {
    const full = path.join(dir, entry);
    const stat = fs.statSync(full);
    if (stat.isDirectory()) {
      results.push(...collectFiles(full));
    } else if (stat.isFile() && entry.endsWith(".ts") && !entry.includes(".test.")) {
      results.push(full);
    }
  }
  return results;
}

const files = [];
for (const dir of dirs) {
  files.push(...collectFiles(dir));
}
files.sort();

if (files.length === 0) {
  console.log("No .ts plugin files found — nothing to check");
  process.exit(0);
}

let passed = 0;
let failed = 0;
const failures = [];

for (const file of files) {
  const rel = path.relative(ROOT, file);
  // Build a minimal eval script that tries to import the module
  // We use a self-contained script that prints PASS/FAIL on stdout
  const code = `
(async () => {
  try {
    const m = await import("${file.replace(/\\/g, "\\\\")}");
    let plugin = m.default || m;
    // If default export is a factory function (Plugin satisfies pattern), invoke it
    if (typeof plugin === 'function') {
      try {
        plugin = plugin({});
      } catch (e) {
        // Factory function threw — that's a real bug
        process.stderr.write("FACTORY INVOCATION ERROR: " + e.message + "\\n");
        process.exit(1);
      }
    }
    // Realistic inputs that mirror actual tool call arguments. Hooks that only
    // crash on real input shapes (not null) hide bugs the null pass misses:
    //   - ReferenceError from undefined symbols called inside conditional branches
    //     that only execute when a real tool/tool_input is present
    //   - TypeError from accessing properties off the wrong shape (e.g.
    //     tool_input.command when tool_input is undefined on a real edit call)
    // The null pass above intentionally ignores TypeError (expected on null).
    // The realistic pass below treats BOTH ReferenceError AND TypeError as bugs
    // because the input shape is now valid.
    const REAL_INPUTS = {
      "tool.execute.before": [
        {
          tool: "bash",
          tool_input: { command: "make lint" },
          path: "/Users/shawnwilson/gludd",
        },
        {
          tool: "edit",
          tool_input: {
            filePath: "/Users/shawnwilson/gludd/test.py",
            oldString: "",
            newString: "x",
          },
          path: "/Users/shawnwilson/gludd/test.py",
        },
      ],
      "experimental.text.complete": [{ text: "some response text" }],
      "text.complete": [{ text: "some response text" }],
      "session.idle": [{ event: "idle" }],
      "experimental.chat.system.transform": [{ role: "user", content: "hi" }],
    };
    const hooks = Object.keys(REAL_INPUTS);
    for (const hook of hooks) {
      const fn = plugin[hook];
      if (typeof fn !== 'function') continue;
      // Null-input pass: runs once per hook. Hooks should handle null/undefined
      // gracefully. ReferenceError is always a bug; TypeError on null is
      // expected (null.length etc.) and ignored.
      try {
        await fn(null, null);
      } catch (e) {
        if (e instanceof ReferenceError) {
          process.stderr.write(
            "HOOK INVOCATION ERROR (null, " + hook + "): " + e.message + "\\n"
          );
          process.exit(1);
        }
      }
      // Realistic-input pass: input shape is valid, so BOTH ReferenceError AND
      // TypeError indicate a real bug (not a null-input artifact). Catches
      // branch-only ReferenceErrors and shape-mismatch TypeErrors.
      for (const input of REAL_INPUTS[hook]) {
        try {
          await fn(input, { tool: input.tool });
        } catch (e) {
          if (e instanceof ReferenceError || e instanceof TypeError) {
            const kind = e.constructor.name;
            const descr = JSON.stringify(input).slice(0, 120);
            process.stderr.write(
              "REAL-INPUT " + kind + " (" + hook + ", input=" + descr +
              "): " + e.message + "\\n"
            );
            process.exit(1);
          }
        }
      }
    }
    process.stdout.write("PASS\\n");
    process.exit(0);
  } catch (e) {
    process.stderr.write(e.message + "\\n");
    process.exit(1);
  }
})();
`;

  const result = spawnSync(
    process.execPath,
    ["--experimental-strip-types", "--input-type=module", "--eval", code],
    {
      timeout: 15000,
      encoding: "utf8",
      maxBuffer: 1024 * 1024,
      stdio: ["pipe", "pipe", "pipe"],
      env: {
        ...process.env,
        // Do NOT set OPENCODE_SUBAGENT — we want hooks to actually execute
        // so we can catch ReferenceErrors inside hook functions.
        GLUDD_FLOOR_ENFORCE: "0",
        GLUDD_STOP_ENFORCE: "0",
        GLUDD_MAKE_ENFORCE: "0",
        GLUDD_MULTITASK_FLOOR_ENFORCE: "0",
        GLUDD_SESSION_START_ENFORCE: "0",
        GLUDD_TDD_ENFORCE: "0",
        GLUDD_NO_SUPPRESSIONS_ENFORCE: "0",
        GLUDD_VERIFIED_CLAIMS_ENFORCE: "0",
        GLUDD_ENHANCEMENT_RATIO_ENFORCE: "0",
        GLUDD_CLEAN_TREE_ENFORCE: "0",
        GLUDD_MAINTHREAD_STREAK_ENFORCE: "0",
        GLUDD_NO_WAIT_ENFORCE: "0",
        GLUDD_TASK_DEADLINE_ENFORCE: "0",
        GLUDD_DELETION_GATE_ENFORCE: "0",
        GLUDD_OBJECTIVE_ENFORCE: "0",
        GLUDD_ANTI_ESSAY_ENFORCE: "0",
        GLUDD_AUDIT_ENFORCE: "0",
        GLUDD_CONTEXT_ENFORCE: "0",
        GLUDD_BATCH_PUSH_ENFORCE: "0",
        GLUDD_BRANCH_DISCIPLINE_ENFORCE: "0",
        GLUDD_WORKTREE_ENFORCE: "0",
        GLUDD_COMMIT_LOCK_ENFORCE: "0",
        GLUDD_TEST_INTEGRITY_ENFORCE: "0",
        GLUDD_DEPTH_ENFORCE: "0",
        // Set project root so file reads stay in-bounds
        GLUDD_PROJECT_ROOT: ROOT,
      },
    },
  );

  const stdout = (result.stdout || "").trim();
  const stderr = (result.stderr || "").trim();

  if (stdout.includes("PASS") && result.status === 0) {
    passed++;
  } else {
    failed++;
    // Extract the most useful error line
    const errorLines = (stderr || stdout).split("\n").filter(l => l.trim());
    const error = errorLines.filter(
      l => l.includes("Error") || l.includes("error") || l.includes("Cannot")
    ).slice(0, 3).join(" | ") || (stderr || stdout).slice(0, 200);
    failures.push({ file: rel, error });
    console.log(`FAIL: ${rel} — ${error}`);
  }
}

// Summary
console.log(`\n${passed} passed, ${failed} failed out of ${files.length} plugin file(s)`);

if (failed > 0) {
  console.log("\nFailures:");
  for (const f of failures) {
    console.log(`  ${f.file}`);
    console.log(`    Error: ${f.error}`);
  }
  process.exit(1);
}

console.log("PASS: all plugins load successfully");
process.exit(0);
