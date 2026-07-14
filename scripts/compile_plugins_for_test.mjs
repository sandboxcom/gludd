#!/usr/bin/env node
/**
 * Compile TypeScript enforcement plugins to JavaScript for runtime testing.
 * Node v26 --experimental-strip-types has known bugs with try/catch inside
 * arrow functions with inline type annotations. This script produces clean JS.
 *
 * Usage: node scripts/compile_plugins_for_test.mjs
 * Output: /tmp/gludd-plugin-js/*.mjs
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import { execSync } from 'node:child_process';

const PLUGIN_DIR = '.opencode/plugin';
const PLUGINS_DIR = '.opencode/plugins';
const OUT_DIR = '/tmp/gludd-plugin-js';

// Ensure output dir
fs.mkdirSync(OUT_DIR, { recursive: true });

function stripTypesManually(src) {
  let out = src;

  // Remove import type
  out = out.replace(/^import type .*$/gm, '/* import type removed */');

  // Remove TypeScript-only declarations
  out = out.replace(/^interface\s+\w+\s*\{[\s\S]*?\n\}/gm, '/* interface */');
  out = out.replace(/^type\s+\w+\s*=[^;]*;/gm, '/* type */');
  out = out.replace(/^enum\s+\w+\s*\{[\s\S]*?\n\}/gm, '/* enum */');

  // Remove type annotations from function params: (name: Type, name2: Type2) =>
  out = out.replace(/\(\s*(\w+)\s*:\s*[\w\s|<>\[\]{}?:]+\s*,\s*(\w+)\s*:\s*[\w\s|<>\[\]{}?:]+\s*\)/g, '($1, $2)');
  out = out.replace(/\(\s*(\w+)\s*:\s*[\w\s|<>\[\]{}?:]+\s*\)/g, '($1)');

  // Remove type annotations from const: const name: Type =
  out = out.replace(/const\s+(\w+)\s*:\s*\w+(\[\])?\s*=\s*/g, 'const $1 = ');

  // Remove function return types: function foo(): Type {
  out = out.replace(/\):\s*\w+(\[\])?\s*\{/g, '){');

  // Remove 'as const', 'as Type' — but PRESERVE 'import * as name'
  // Handle 'as Type' on non-import lines
  out = out.replace(/("deny"\s*)as const/g, '$1');
  out = out.replace(/("[\w-]+"\s*)as const/g, '$1');

  // Remove inline type assertions on complex expressions
  out = out.replace(/\s+as\s+Record<[^>]+>\s*\|?\s*\w*/g, '');
  out = out.replace(/\s+as\s+\{[^}]*\}\s*\|?\s*\w*/g, '');

  // Remove remaining 'as Type' on lines that DON'T start with 'import'
  // Split into lines and only process non-import lines
  const lines = out.split('\n');
  out = lines.map(line => {
    if (line.trim().startsWith('import ')) return line;
    return line.replace(/\s+as\s+\w+/g, '');
  }).join('\n');

  return out;
}

const pluginDir = path.resolve(PLUGIN_DIR);
const pluginsDir = path.resolve(PLUGINS_DIR);

for (const dir of [pluginDir, pluginsDir]) {
  if (!fs.existsSync(dir)) continue;

  for (const file of fs.readdirSync(dir)) {
    if (!file.endsWith('.ts')) continue;

    const src = fs.readFileSync(path.join(dir, file), 'utf8');
    const stripped = stripTypesManually(src);
    const outName = file.replace('.ts', '.mjs');
    fs.writeFileSync(path.join(OUT_DIR, outName), stripped);
    console.log(`Compiled: ${file} → ${outName}`);
  }
}

console.log(`\nAll plugins compiled to ${OUT_DIR}/`);
