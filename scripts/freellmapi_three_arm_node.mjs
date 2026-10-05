import { readFileSync } from "node:fs";
import { runInContext, createContext } from "node:vm";

const [bundlePath] = process.argv.slice(2);
if (!bundlePath) {
  process.exitCode = 2;
} else {
  const payload = readFileSync(0, "utf8");
  const source = readFileSync(bundlePath, "utf8");
  const sandbox = createContext(Object.create(null), {
    codeGeneration: { strings: false, wasm: false },
  });
  runInContext(source, sandbox, { timeout: 1000 });
  const invoke = sandbox.__gludd_freellmapi_candidate_batch;
  if (typeof invoke !== "function") {
    process.exitCode = 2;
  } else {
    process.stdout.write(invoke(payload));
  }
}
