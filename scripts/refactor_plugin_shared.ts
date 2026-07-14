// refactor_plugin_shared.ts — E.5 plugin leanness refactor
// Replaces private _isSubagent() / _reportAlive() with shared imports.
// Usage: npx tsx scripts/refactor_plugin_shared.ts

import * as fs from "node:fs";
import * as path from "node:path";

const PLUGIN_DIR = path.join(process.cwd(), ".opencode/plugin");
const PLUGINS_DIR = path.join(process.cwd(), ".opencode/plugins");

// Files already using shared imports (skip)
const ALREADY_MIGRATED = new Set(["shared.ts", "enforce-floor.ts"]);

// Files that have _isSubagent but NOT _reportAlive
const SUBAGENT_ONLY = new Set(["hot_reload.ts"]);

// Files that have _reportAlive but NOT _isSubagent
const REPORTALIVE_ONLY = new Set(["watchdog.ts"]);

interface FileInfo {
  path: string;
  name: string;
  hasSubagent: boolean;
  hasReportAlive: boolean;
}

function getPluginName(filename: string): string {
  return filename.replace(/\.ts$/, "");
}

function findPluginFiles(): FileInfo[] {
  const result: FileInfo[] = [];
  for (const dir of [PLUGIN_DIR, PLUGINS_DIR]) {
    if (!fs.existsSync(dir)) continue;
    for (const entry of fs.readdirSync(dir)) {
      if (!entry.endsWith(".ts")) continue;
      if (ALREADY_MIGRATED.has(entry)) continue;
      const filePath = path.join(dir, entry);
      const content = fs.readFileSync(filePath, "utf8");
      const hasSubagent = /_isSubagent/.test(content);
      const hasReportAlive = /_reportAlive/.test(content);
      if (hasSubagent || hasReportAlive) {
        result.push({ path: filePath, name: entry, hasSubagent, hasReportAlive });
      }
    }
  }
  return result;
}

// ── isSubagent function patterns ──────────────────────────────────────────
const IS_SUBAGENT_PATTERNS = [
  // Pattern 1: one-line compact
  /function _isSubagent\(\):\s*boolean\s*\{\s*if\s*\(process\.env\.OPENCODE_SUBAGENT\s*===\s*"1"\)\s*return\s*true;\s*try\s*\{\s*return\s*fs\.existsSync\(`\/tmp\/gludd-subagent-\$\{process\.pid\}\.json`\);\s*\}\s*catch\s*\{\s*return\s*false;\s*\}\s*\}/g,
  // Pattern 2: multi-line with semicolons on own lines
  /function _isSubagent\(\):\s*boolean\s*\{\s*\n\s*if\s*\(process\.env\.OPENCODE_SUBAGENT\s*===\s*"1"\)\s*return\s*true;\s*\n\s*try\s*\{\s*return\s*fs\.existsSync\(`\/tmp\/gludd-subagent-\$\{process\.pid\}\.json`\);\s*\}\s*catch\s*\{\s*return\s*false;\s*\}\s*\n\s*\}/g,
];

// ── _reportAlive patterns ─────────────────────────────────────────────────
// We need to match the whole function body. Let's use a more flexible approach.

function processFile(info: FileInfo): boolean {
  let content = fs.readFileSync(info.path, "utf8");
  const original = content;
  const pluginName = getPluginName(info.name);
  let changed = false;

  // Step 1: Delete _isSubagent function
  if (info.hasSubagent) {
    for (const pattern of IS_SUBAGENT_PATTERNS) {
      const before = content;
      content = content.replace(pattern, "");
      if (content !== before) {
        changed = true;
        break;
      }
    }
    // If patterns didn't match, try a more flexible regex
    if (content === original) {
      // Match the function body between the first { and the matching }
      const startIdx = content.indexOf("function _isSubagent");
      if (startIdx !== -1) {
        // Find the opening brace
        const openBrace = content.indexOf("{", startIdx);
        if (openBrace !== -1) {
          // Find matching closing brace
          let depth = 0;
          let endIdx = -1;
          for (let i = openBrace; i < content.length; i++) {
            if (content[i] === "{") depth++;
            if (content[i] === "}") {
              depth--;
              if (depth === 0) {
                endIdx = i + 1;
                break;
              }
            }
          }
          if (endIdx !== -1) {
            // Remove the function + trailing whitespace/newline
            let snippet = content.substring(startIdx, endIdx);
            // Remove trailing semicolons and newlines after the closing brace
            let cutEnd = endIdx;
            while (cutEnd < content.length && (content[cutEnd] === ";" || content[cutEnd] === "\n" || content[cutEnd] === "\r")) {
              cutEnd++;
            }
            // Remove leading newline before the function if present
            let cutStart = startIdx;
            while (cutStart > 0 && content[cutStart - 1] === "\n") cutStart--;
            if (cutStart > 0 && content[cutStart - 1] === "\r") cutStart--;

            content = content.substring(0, cutStart) + content.substring(cutEnd);
            changed = true;
          }
        }
      }
    }
  }

  // Step 2: Delete _reportAlive function
  if (info.hasReportAlive) {
    // Find the function definition
    let startIdx = content.indexOf("function _reportAlive");
    if (startIdx === -1) {
      startIdx = content.indexOf("async function _reportAlive");
    }
    if (startIdx !== -1) {
      const openBrace = content.indexOf("{", startIdx);
      if (openBrace !== -1) {
        let depth = 0;
        let endIdx = -1;
        for (let i = openBrace; i < content.length; i++) {
          if (content[i] === "{") depth++;
          if (content[i] === "}") {
            depth--;
            if (depth === 0) {
              endIdx = i + 1;
              break;
            }
          }
        }
        if (endIdx !== -1) {
          let cutEnd = endIdx;
          while (cutEnd < content.length && (content[cutEnd] === ";" || content[cutEnd] === "\n" || content[cutEnd] === "\r")) {
            cutEnd++;
          }
          let cutStart = startIdx;
          while (cutStart > 0 && content[cutStart - 1] === "\n") cutStart--;
          if (cutStart > 0 && content[cutStart - 1] === "\r") cutStart--;

          content = content.substring(0, cutStart) + content.substring(cutEnd);
          changed = true;
        }
      }
    }
  }

  // Step 3: Replace _isSubagent() calls with isSubagent()
  if (info.hasSubagent) {
    // Handle special case in enforce-make.ts: const isSubagent = _isSubagent()
    content = content.replace(/const isSubagent\s*=\s*_isSubagent\(\)/g, "const _isSub = isSubagent()");
    // Replace all other _isSubagent() calls
    content = content.replace(/_isSubagent\(\)/g, "isSubagent()");
    // Fix the special case back
    content = content.replace(/const _isSub\s*=\s*isSubagent\(\)/g, "const isSubagentResult = isSubagent()");
    changed = true;
  }

  // Step 4: Replace _reportAlive() calls with reportAlive("plugin-name")
  if (info.hasReportAlive) {
    // Remove `await` before _reportAlive() calls
    content = content.replace(/await\s+_reportAlive\(\)/g, `reportAlive("${pluginName}")`);
    content = content.replace(/_reportAlive\(\)/g, `reportAlive("${pluginName}")`);
    changed = true;
  }

  // Step 5: Add import line
  if (changed) {
    const sharedImports: string[] = [];
    if (info.hasSubagent) sharedImports.push("isSubagent");
    if (info.hasReportAlive) sharedImports.push("reportAlive");

    const importLine = `import { ${sharedImports.join(", ")} } from "./shared.ts";`;

    // Check if already has a shared.ts import
    if (!content.includes('from "./shared.ts"')) {
      // Insert after the last existing import line or at the top
      const lines = content.split("\n");
      let insertIdx = 0;
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].startsWith("import ")) {
          insertIdx = i + 1;
        }
      }
      // Skip blank lines after last import
      while (insertIdx < lines.length && lines[insertIdx].trim() === "") {
        insertIdx++;
      }
      lines.splice(insertIdx, 0, importLine);
      content = lines.join("\n");
    } else {
      // Already has shared.ts import — add missing symbols
      if (info.hasSubagent && !content.includes("isSubagent")) {
        content = content.replace(
          /import\s*\{\s*/,
          "import { isSubagent, "
        );
        // Clean up double commas
        content = content.replace(/, ,/g, ",");
      }
      if (info.hasReportAlive && !content.includes("reportAlive")) {
        content = content.replace(
          /import\s*\{\s*/,
          "import { reportAlive, "
        );
        content = content.replace(/, ,/g, ",");
      }
    }

    // Step 6: Clean up - remove duplicate blank lines
    content = content.replace(/\n{3,}/g, "\n\n");

    // Write back
    fs.writeFileSync(info.path, content, "utf8");
  }

  return changed;
}

function main() {
  const files = findPluginFiles();
  console.log(`Found ${files.length} files to process`);

  let modified = 0;
  for (const info of files) {
    const wasChanged = processFile(info);
    if (wasChanged) {
      modified++;
      console.log(`  ✓ ${info.name} — applies both isSubagent and reportAlive`);
    }
  }

  console.log(`\nModified ${modified} files.`);
  console.log("Run `node --check` manually or via `make verify-plugin-syntax` to validate.");
}

main();
