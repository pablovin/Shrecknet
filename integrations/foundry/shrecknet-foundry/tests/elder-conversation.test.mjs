import assert from "node:assert/strict";
import test from "node:test";
import { presentElderHistory, presentElderSources, submitElderTurn } from "../scripts/apps/elder-conversation.js";

const baseline = { id: "chat", agent_id: "elder", history: [] };
const history = [
  { role: "user", content: "Why?" },
  { role: "assistant", content: "Saved answer", metadata: { sources: [{ node_name: "Source" }] } },
];
const disconnected = Object.assign(new Error("Unable to connect"), { status: 0 });

test("a successful Elder answer displays directly in its submitted conversation", async () => {
  let reads = 0;
  const result = await submitElderTurn({
    getElderChat: async (id) => { assert.equal(id, "chat"); reads++; return baseline; },
    askElder: async (...args) => { assert.deepEqual(args, ["elder", "chat", "Why?", "context"]); return { answer: "Direct answer", sources: [{ node_name: "Source" }] }; },
  }, { agentId: "elder", chatId: "chat", query: "Why?", instanceId: "context" });
  assert.equal(reads, 1);
  assert.deepEqual(presentElderHistory(result), [
    { role: "user", content: "Why?", citations: [] },
    { role: "assistant", content: "Direct answer", citations: [{ node_name: "Source" }] },
  ]);
});

test("a lost query response polls the same chat for a newly saved complete turn without resubmission", async () => {
  let reads = 0, sends = 0;
  const result = await submitElderTurn({
    getElderChat: async () => ({ ...baseline, history: ++reads < 3 ? [] : history }),
    askElder: async () => { sends++; throw disconnected; },
  }, { agentId: "elder", chatId: "chat", query: "Why?", attempts: 2, wait: async () => {} });
  assert.equal(sends, 1);
  assert.equal(reads, 3);
  assert.deepEqual(result, history);
});

test("old identical answers and incomplete turns do not count as completion", async () => {
  let reads = 0;
  await assert.rejects(submitElderTurn({
    getElderChat: async () => ({ ...baseline, history: ++reads === 1 ? history : [...history, { role: "user", content: "Why?" }] }),
    askElder: async () => { throw disconnected; },
  }, { agentId: "elder", chatId: "chat", query: "Why?", attempts: 1 }), /No completed reply/);
});

test("validation errors do not poll and access failures stop recovery immediately", async () => {
  let reads = 0;
  const client = {
    getElderChat: async () => { reads++; return baseline; },
    askElder: async () => { throw Object.assign(new Error("Invalid"), { status: 422 }); },
  };
  await assert.rejects(submitElderTurn(client, { agentId: "elder", chatId: "chat", query: "Why?" }), /Invalid/);
  assert.equal(reads, 1);
  client.askElder = async () => { throw disconnected; };
  client.getElderChat = async () => { if (++reads > 2) throw Object.assign(new Error("Unauthorized"), { status: 401 }); return baseline; };
  await assert.rejects(submitElderTurn(client, { agentId: "elder", chatId: "chat", query: "Why?" }), /Unauthorized/);
  assert.equal(reads, 3);
});

test("leaving the conversation stops recovery without applying a late answer", async () => {
  let active = true, reads = 0;
  const result = await submitElderTurn({
    getElderChat: async () => { reads++; return baseline; },
    askElder: async () => { active = false; throw disconnected; },
  }, { agentId: "elder", chatId: "chat", query: "Why?", isActive: () => active });
  assert.equal(result, null);
  assert.equal(reads, 1);
});

test("conversation identity must match before submitting or recovering a question", async () => {
  let sends = 0;
  await assert.rejects(submitElderTurn({
    getElderChat: async () => ({ ...baseline, agent_id: "another-elder" }),
    askElder: async () => { sends++; },
  }, { agentId: "elder", chatId: "chat", query: "Why?" }), /conversation is unavailable/);
  assert.equal(sends, 0);
});


test("source previews preserve evidence and navigate only explicit page identifiers", () => {
  const sources = presentElderSources([null, { node_id: "graph-only", node_name: "Raist", evidence_chunks: [{text: "Exact excerpt"}, {}] }, { source_entity_instance_id: "page", canonical_text: "Fallback" }]);
  assert.deepEqual(sources, [
    {title: "Raist", instanceId: null, excerpts: ["Exact excerpt"], text: ""},
    {title: "Atlas source", instanceId: "page", excerpts: [], text: "Fallback"},
  ]);
  assert.deepEqual(presentElderSources({}), []);
});

test("the workspace applies the direct reply without reloading the chat list", async () => {
  globalThis.foundry = { applications: { api: { ApplicationV2: class {}, HandlebarsApplicationMixin: (base) => base } } };
  const { ShrecknetShell } = await import("../scripts/apps/shrecknet-shell.js");
  let renders = 0;
  const app = {
    session: { user: { id: 1 } }, resource: { kind: "elder" },
    elderAgentId: "elder", elderChatId: "chat", connection: {},
    _form: () => new Map([["query", "Why?"]]),
    _withLoading: async (work) => work(),
    _handleError: async (error) => { throw error; },
    render: async () => { renders++; },
    client: {
      getElderChat: async () => baseline,
      askElder: async () => ({ answer: "Visible immediately", sources: [] }),
      listElderChats: () => { throw new Error("Must not switch conversations"); },
    },
  };
  await ShrecknetShell._askElder.call(app);
  assert.equal(app.elderHistory[1].content, "Visible immediately");
  assert.equal(app.elderChatId, "chat");
  assert.equal(app.isAskingElder, false);
  assert.equal(renders, 1);
});
