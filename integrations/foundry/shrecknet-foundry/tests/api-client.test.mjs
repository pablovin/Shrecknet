import assert from "node:assert/strict";
import test from "node:test";
import { ShrecknetApiError, ShrecknetClient } from "../scripts/api/shrecknet-client.js";

test("service URL validation accepts HTTPS bases and rejects credentials or unsafe transport", () => {
  assert.equal(
    ShrecknetClient.normalizeServiceUrl(" https://services.example/shreckrpg/ "),
    "https://services.example/shreckrpg",
  );
  assert.throws(() => ShrecknetClient.normalizeServiceUrl("http://services.example"), ShrecknetApiError);
  assert.throws(() => ShrecknetClient.normalizeServiceUrl("https://user:pass@services.example"), ShrecknetApiError);
  assert.throws(() => ShrecknetClient.normalizeServiceUrl("https://services.example?token=secret"), ShrecknetApiError);
});

test("authenticated service requests fail before fetch when no player token is present", async () => {
  const client = new ShrecknetClient({ serverUrl: "https://shrecknet.example" });
  const originalFetch = globalThis.fetch;
  let called = false;
  globalThis.fetch = async () => {
    called = true;
    return new Response("{}", { headers: { "content-type": "application/json" } });
  };

  try {
    await assert.rejects(client.listChronicles("https://rpg.example/api/shreckrpg"), (error) => {
      assert.ok(error instanceof ShrecknetApiError);
      assert.equal(error.status, 401);
      return true;
    });
    assert.equal(called, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Chronicle API calls use the signed-in player's token at the configured API base", async () => {
  const client = new ShrecknetClient({ serverUrl: "https://shrecknet.example", accessToken: "player-token" });
  const originalFetch = globalThis.fetch;
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url: String(url), options };
    return Response.json([]);
  };

  try {
    await client.listChronicles("https://rpg.example/api/shreckrpg");
    assert.equal(request.url, "https://rpg.example/api/shreckrpg/chronicles");
    assert.equal(request.options.headers.Authorization, "Bearer player-token");
    assert.equal(request.options.headers["X-Shrecknet-Integration-Key"], undefined);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Chronicle asset reads remain scoped to the selected Chronicle", async () => {
  const client = new ShrecknetClient({ serverUrl: "https://shrecknet.example", accessToken: "player-token" });
  await assert.rejects(
    client.getChronicleAsset("https://rpg.example/api/shreckrpg", 7, "/chronicles/8/assets/0123456789abcdef0123456789abcdef"),
    /Invalid Chronicle image URL/,
  );
});

test("Elder listing follows the Atlas pagination contract", async () => {
  const client = new ShrecknetClient({ serverUrl: "https://shrecknet.example", accessToken: "player-token" });
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    const skip = new URL(String(url)).searchParams.get("skip");
    return Response.json(skip === "0"
      ? Array.from({ length: 100 }, (_, index) => ({ id: `elder-${index}`, name: "Elder" }))
      : [{ id: "elder-100", name: "Elder" }]);
  };

  try {
    const agents = await client.listElders(12);
    assert.equal(agents.length, 101);
    assert.equal(new URL(requests[1]).searchParams.get("skip"), "100");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ShreckWorld queries keep source scope and Chronicle saves use revision sharing without game_id", async () => {
  const client = new ShrecknetClient({ serverUrl: "https://shrecknet.example", accessToken: "player-token" });
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url: String(url), options });
    return Response.json({ answer: "Evidence", citations: [] });
  };

  try {
    await client.queryKnowledge("https://rules.example/shreckworld-api", "world-1", "What is the rule?", "book-1");
    const chronicle = {
      id: 9,
      revision: 4,
      ontology_id: null,
      explicit_references: [],
      capabilities: { share: true },
    };
    await client.saveChronicle("https://rpg.example/api/shreckrpg", chronicle, "Session", "<p>Notes</p>", [2]);

    assert.equal(requests[0].url, "https://rules.example/shreckworld-api/shreckworlds/world-1/query");
    assert.deepEqual(JSON.parse(requests[0].options.body), {
      query: "What is the rule?", top_k: 6, include_trace: false, library_item_id: "book-1",
    });
    assert.equal(requests[1].options.method, "PUT");
    assert.deepEqual(JSON.parse(requests[1].options.body), {
      expected_revision: 4,
      title: "Session",
      content: "<p>Notes</p>",
      ontology_id: null,
      explicit_references: [],
      share_user_ids: [2],
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Atlas metadata includes hidden entity types without changing directory discovery", async () => {
  const client = new ShrecknetClient({ serverUrl: "https://shrecknet.example", accessToken: "player-token" });
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url: String(url), authorization: options.headers.Authorization });
    return Response.json([]);
  };
  try {
    await client.listEntityTypes(12);
    await client.listEntityTypes(12, { displayedOnly: false });
    await client.listEntityRelationships(12, 3);
    assert.deepEqual(requests.map((request) => request.url), [
      "https://shrecknet.example/ontologies/12/entities?display_on_world=true",
      "https://shrecknet.example/ontologies/12/entities",
      "https://shrecknet.example/ontologies/12/entities/3/relationships",
    ]);
    assert.ok(requests.every((request) => request.authorization === "Bearer player-token"));
  } finally { globalThis.fetch = originalFetch; }
});

test("companion requests planned concise answers scoped to several books", async () => {
  const client = new ShrecknetClient({ serverUrl: "https://shrecknet.example", accessToken: "player-token" });
  const originalFetch = globalThis.fetch;
  let body;
  globalThis.fetch = async (_, options) => { body = JSON.parse(options.body); return Response.json({}); };
  try {
    await client.queryKnowledge("https://rules.example", "world", "Rules?", ["a", "b"], {answer: true});
    assert.deepEqual(body, {query: "Rules?", top_k: 6, include_trace: false, library_item_ids: ["a", "b"], mode: "answer", retrieval_strategy: "planned", response_detail: "concise"});
    await client.queryKnowledge("https://rules.example", "world", "Rules?", [], {answer: true});
    assert.equal(body.library_item_ids, undefined);
    assert.equal(body.library_item_id, undefined);
  } finally { globalThis.fetch = originalFetch; }
});

test("network failures identify the service base without leaking authorization", async () => {
  const client = new ShrecknetClient({ serverUrl: "https://shrecknet.example", accessToken: "private-token" });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  try {
    await assert.rejects(client.listKnowledgeWorlds("https://rules.example/api"), (error) => {
      assert.equal(error.status, 0);
      assert.match(error.message, /https:\/\/rules.example\/api/);
      assert.match(error.message, /CORS/);
      assert.ok(!error.message.includes("private-token"));
      return true;
    });
  } finally { globalThis.fetch = originalFetch; }
});
