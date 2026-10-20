// Elder queries are synchronous. Recover saved turns after transport failures
// without repeating the question or switching to another conversation.
export async function submitElderTurn(client, {
  agentId, chatId, query, instanceId = null,
  isActive = () => true,
  onRecover = () => {},
  wait = () => new Promise((resolve) => setTimeout(resolve, 3000)),
  attempts = 200,
}) {
  const baseline = await client.getElderChat(chatId);
  if (!isActive()) return null;
  if (baseline.id !== chatId || String(baseline.agent_id) !== String(agentId)) {
    throw Object.assign(new Error("The Elder conversation is unavailable."), { status: 404 });
  }
  const previous = baseline.history || [];
  try {
    const response = await client.askElder(agentId, chatId, query, instanceId);
    if (!response || typeof response.answer !== "string") {
      throw Object.assign(new Error("Elder returned an invalid response."), { status: 0 });
    }
    return [...previous,
      { role: "user", content: query },
      { role: "assistant", content: response.answer, metadata: response },
    ];
  } catch (error) {
    if (![0, 500, 502, 504].includes(error.status)) throw error;
    onRecover();
    const checks = error.status === 500 ? 1 : attempts;
    for (let attempt = 0; attempt < checks; attempt++) {
      if (!isActive()) return null;
      if (attempt) await wait();
      if (!isActive()) return null;
      try {
        const detail = await client.getElderChat(chatId);
        if (detail.id !== chatId || String(detail.agent_id) !== String(agentId)) {
          throw Object.assign(new Error("The Elder conversation is unavailable."), { status: 404 });
        }
        const added = (detail.history || []).slice(previous.length);
        if (added.some((message, index) => message.role === "user" && message.content === query
          && added[index + 1]?.role === "assistant")) return detail.history;
      } catch (readError) {
        if ([401, 403, 404].includes(readError.status)) throw readError;
      }
    }
    throw new Error("The connection was interrupted. No completed reply was found yet. Reopen this conversation to check before sending the question again.");
  }
}

export function presentElderHistory(history) {
  return history.map((message) => ({
    role: message.role || message.type || "assistant",
    content: message.content || message.query || message.answer || "",
    citations: message.metadata?.sources || message.metadata?.context || message.citations || message.sources || [],
  }));
}

// Only explicit parent-page identifiers are navigable; node IDs may be graph IDs.
export function presentElderSources(sources) {
  return (Array.isArray(sources) ? sources : []).filter((source) => source && typeof source === "object").map((source) => ({
    title: source.node_name || source.title || source.node_alias || source.node_label || "Atlas source",
    instanceId: source.source_entity_instance_id || source.instance_id || null,
    excerpts: (Array.isArray(source.evidence_chunks) ? source.evidence_chunks : [])
      .filter((chunk) => typeof chunk?.text === "string" && chunk.text.trim()).map((chunk) => chunk.text),
    text: source.canonical_text || source.text || "",
  }));
}
