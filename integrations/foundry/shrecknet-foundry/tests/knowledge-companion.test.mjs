import assert from "node:assert/strict";
import test from "node:test";
import { knowledgeReaderUrl, presentKnowledgeTurn, formatKnowledgeTurn } from "../scripts/apps/knowledge-companion.js";

globalThis.foundry = { applications: { api: { ApplicationV2: class {}, HandlebarsApplicationMixin: (base) => base } } };
const { ShrecknetShell } = await import("../scripts/apps/shrecknet-shell.js");

test("reader links use the supplied web route, encode identifiers and contain no API token", () => {
  const url = new URL(knowledgeReaderUrl("https://shrecknet.club/backend_api", "world &", "book?"));
  assert.equal(url.pathname, "/home/shreckworld/shreckworld/shreckworld");
  assert.equal(url.searchParams.get("collectionId"), "world &");
  assert.equal(url.searchParams.get("sourceId"), "book?");
  assert.equal(url.searchParams.size, 2);
});

test("presentation preserves unsupported status, source conflicts, warnings and page references", () => {
  const scope = ["book"];
  const turn = presentKnowledgeTurn("Rule?", { answer: "Cannot establish the rule", answer_status: "unsupported", conflicts: [{ description: "Different editions", citation_ids: ["source-1"] }], source_warnings: [{ message: "Reindex required" }], citations: [{ title: "Rules", page_number: 8, excerpt: "Source words" }] }, scope);
  scope.push("other");
  assert.deepEqual(turn.sourceIds, ["book"]);
  assert.equal(turn.status, "unsupported");
  assert.equal(turn.conflicts[0].description, "Different editions");
  assert.match(formatKnowledgeTurn(turn), /Rules · page 8\nSource words/);
});

test("query snapshots book selection, prevents duplicate sends and retains the turn across navigation", async () => {
  let resolve, requests = 0;
  const state = { selected: new Set(["book"]), turns: [], draft: "Question", busy: false };
  const app = { resource: { world: { id: "world" } }, session: {}, _knowledgeSession: () => state,
    _form: () => new Map([["query", "Question"]]), _serviceUrl: () => "https://rules.example", render: async () => {},
    client: { queryKnowledge: (...args) => { requests++; assert.deepEqual(args.slice(1), ["world", "Question", ["book"], {answer: true}]); return new Promise((r) => { resolve = r; }); } },
  };
  const pending = ShrecknetShell._queryKnowledge.call(app);
  await Promise.resolve();
  await ShrecknetShell._queryKnowledge.call(app);
  app.resource = {kind: "home"};
  resolve({answer: "Answer", answer_status: "complete"});
  await pending;
  assert.equal(requests, 1);
  assert.equal(state.turns[0].answer, "Answer");
  assert.equal(state.draft, "");
  assert.equal(state.busy, false);
});

test("failed questions retain draft and sign-out discards late answers", async () => {
  const state = { selected: new Set(), turns: [], draft: "Question", busy: false };
  const app = { resource: {world: {id: "world"}}, session: {}, _knowledgeSession: () => state,
    _form: () => new Map([["query", "Question"]]), _serviceUrl: () => "https://rules.example", render: async () => {},
    _handleError: async () => {}, client: { queryKnowledge: async () => { throw Error("offline"); } },
  };
  await ShrecknetShell._queryKnowledge.call(app);
  assert.equal(state.draft, "Question");
  assert.equal(state.turns.length, 0);
  app.client.queryKnowledge = async () => { app.session = null; return {answer: "Private answer"}; };
  await ShrecknetShell._queryKnowledge.call(app);
  assert.equal(state.turns.length, 0);
});

test("separate knowledge worlds retain independent drafts, selections and conversations", () => {
  const app = { knowledgeSessions: new Map() };
  const first = ShrecknetShell.prototype._knowledgeSession.call(app, "a");
  first.draft = "Private question";
  first.selected.add("book-a");
  first.turns.push({answer: "World A"});
  const second = ShrecknetShell.prototype._knowledgeSession.call(app, "b");
  assert.equal(second.draft, "");
  assert.equal(second.selected.size, 0);
  assert.equal(second.turns.length, 0);
  assert.equal(ShrecknetShell.prototype._knowledgeSession.call(app, "a"), first);
});

test("library cover failures preserve books and clear revoked image references", async () => {
  const resource = {kind: "knowledgeLibrary", world: {id: "world"}, sources: [{id: "book", title: "Rules", cover_url: "/protected", coverUrl: "blob:revoked"}]};
  const app = {resource, _serviceUrl: () => "https://rules.example", _loadObjectUrl: async (_, loader) => loader(), client: {getKnowledgeCover: async () => { throw Error("unavailable"); }}};
  const result = await ShrecknetShell.prototype._presentResource.call(app);
  assert.equal(result.sources[0].title, "Rules");
  assert.equal(result.sources[0].coverUrl, null);
});
