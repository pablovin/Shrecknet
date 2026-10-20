import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const css = await readFile(new URL("styles/base.css", root), "utf8");
const template = await readFile(new URL("templates/shell.hbs", root), "utf8");

test("Atlas cards override native button height and single-line labels", () => {
  const rule = css.match(/\.shrecknet button\.shrecknet-folder,\s*\.shrecknet button\.shrecknet-page-card\s*\{([^}]+)\}/)?.[1];
  assert.ok(rule, "Card overrides must be scoped above native button specificity");
  assert.match(rule, /height:\s*auto\s*;/);
  assert.match(rule, /max-height:\s*none\s*;/);
  assert.match(rule, /white-space:\s*normal\s*;/);
  assert.match(css, /\.shrecknet-folder strong,[\s\S]*?overflow-wrap:\s*anywhere\s*;/);
});

test("Atlas directory icons are contained and retain type and ontology labels", () => {
  assert.match(css, /\.shrecknet-folder img\s*\{[^}]*object-fit:\s*contain\s*;/);
  const home = template.split('{{#each resource.folders}}')[1]?.split('{{/each}}')[0];
  assert.ok(home, "Home renders the loaded entity-type folders");
  assert.match(home, /<strong>\{\{name\}\}<\/strong>/);
  assert.match(home, /<small>\{\{ontologyName\}\}<\/small>/);
});
