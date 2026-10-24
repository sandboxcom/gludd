"""Real installed plugin and Effect TOML behavior across the security leaf repair."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PREFIX = r"""
import assert from "node:assert/strict";
import { tool } from "@opencode-ai/plugin";
import { Effect, FileSystem, Path, Schema } from "effect";
import { Primitive } from "effect/unstable/cli";

let content = "";
const fs = FileSystem.makeNoop({
  exists: (path) => {
    assert.equal(path, "/gludd-owned/config.toml");
    return Effect.succeed(true);
  },
  stat: () => Effect.succeed({ type: "File" }),
  readFileString: () => Effect.succeed(content)
});
const run = (effect) => Effect.runPromise(effect.pipe(
  Effect.provideService(FileSystem.FileSystem, fs), Effect.provide(Path.layer)
));
const parse = () => run(Primitive.fileParse({ format: "toml" }).parse("/gludd-owned/config.toml"));
"""
CASES = (
    ("numbers", r"""
content = "integer = 42\nnegative = -42\nhex = 0x2a\nratio = 1.25\nsafe_max = 9007199254740991\n";
const value = await parse();
assert.equal(typeof value.integer, "number");
assert.equal(value.integer, 42);
assert.equal(value.negative, -42);
assert.equal(value.hex, 42);
assert.equal(value.ratio, 1.25);
assert.equal(value.safe_max, Number.MAX_SAFE_INTEGER);
"""),
    ("wide-integer", r"""
content = "wide = 9007199254740993\n";
const value = await parse();
assert.equal(typeof value.wide, "number", "original Effect TOML consumer returns a Number");
assert.equal(value.wide, Number("9007199254740993"));
"""),
    ("dates", r"""
content = "offset = 1979-05-27T07:32:00Z\nlocal_date = 1979-05-27\nlocal_time = 07:32:00\n";
const value = await parse();
assert.ok(value.offset instanceof Date, "default offset dates retain Date objects");
assert.equal(value.offset.toISOString(), "1979-05-27T07:32:00.000Z");
assert.equal(value.local_date, "1979-05-27");
assert.equal(value.local_time, "07:32:00");
"""),
    ("arrays", r"""
content = "ports = [8000, 8001]\n[[servers]]\nname = \"alpha\"\n[[servers]]\nname = \"beta\"\n";
const value = await parse();
assert.deepEqual(value.ports, [8000, 8001]);
assert.deepEqual(value.servers.map((server) => server.name), ["alpha", "beta"]);
"""),
    ("typed-schema", r"""
content = "port = 3000\nratio = 1.25\nenabled = true\nports = [8000, 8001]\n";
const schema = Schema.Struct({
  port: Schema.Number, ratio: Schema.Number, enabled: Schema.Boolean,
  ports: Schema.Array(Schema.Number)
});
const value = await run(Primitive.fileSchema(schema, { format: "toml" }).parse("/gludd-owned/config.toml"));
assert.deepEqual(value, { port: 3000, ratio: 1.25, enabled: true, ports: [8000, 8001] });
"""),
    ("invalid-toml", r"""
content = "ports = [\n";
await assert.rejects(parse(), /Failed to parse '\.toml' file content/);
"""),
    ("plugin-tool", r"""
const execute = async (args, context) => context.prefix + args.name + ":" + args.count;
const input = {
  description: "owned compatibility tool",
  args: { name: tool.schema.string().min(1), count: tool.schema.number().int().min(0) },
  execute
};
const declared = tool(input);
assert.equal(declared, input);
assert.equal(declared.execute, execute);
const schema = tool.schema.object(declared.args);
assert.deepEqual(schema.parse({ name: "gludd", count: 2 }), { name: "gludd", count: 2 });
assert.equal(schema.safeParse({ name: "", count: -1 }).success, false);
assert.equal(await declared.execute({ name: "gludd", count: 2 }, { prefix: "owned:" }), "owned:gludd:2");
assert.deepEqual(tool.schema.array(tool.schema.number()).max(2).parse([1, 2]), [1, 2]);
assert.equal(tool.schema.array(tool.schema.number()).max(2).safeParse([1, 2, 3]).success, false);
"""),
)


@pytest.mark.parametrize("case_name,assertions", CASES, ids=[case[0] for case in CASES])
def test_installed_dependency_consumer_semantics(case_name: str, assertions: str) -> None:
    environment = os.environ.copy()
    result = subprocess.run(
        ["node", "--input-type=module", "-e",
         PREFIX + assertions + '\nconsole.log("NODE_DEPENDENCY_COMPATIBILITY_PASS ' + case_name + '");\n'],
        cwd=ROOT / ".opencode", env=environment, capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NODE_DEPENDENCY_COMPATIBILITY_PASS " + case_name in result.stdout
