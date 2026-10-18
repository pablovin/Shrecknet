import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import test from "node:test";

const moduleRoot = new URL("../", import.meta.url);
const manifest = JSON.parse(await readFile(new URL("module.json", moduleRoot), "utf8"));

test("release download matches the module ID and version", () => {
  assert.match(manifest.version, /^\d+\.\d+\.\d+$/);
  assert.equal(
    manifest.download,
    `https://github.com/pablovin/Shrecknet/releases/download/foundry-v${manifest.version}/${manifest.id}-v${manifest.version}.zip`,
  );
});

test("all manifest scripts, styles, and README exist in the package", async () => {
  for (const path of [...manifest.esmodules, ...manifest.styles, manifest.readme]) {
    assert.ok((await stat(new URL(path, moduleRoot))).isFile(), `Missing module file: ${path}`);
  }
});
