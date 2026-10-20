// Reader links contain source identifiers only; the website owns reader authentication.
export function knowledgeReaderUrl(serverUrl, worldId = null, sourceId = null) {
  const url = new URL("/home/shreckworld/shreckworld/shreckworld", serverUrl);
  if (worldId) url.searchParams.set("collectionId", worldId);
  if (sourceId) url.searchParams.set("sourceId", sourceId);
  return url.href;
}

export function presentKnowledgeTurn(query, result, sourceIds) {
  return {
    query, sourceIds: [...sourceIds], answer: result.answer || "No supported answer was returned.",
    status: result.answer_status || "evidence",
    citations: Array.isArray(result.citations) ? result.citations : [],
    conflicts: Array.isArray(result.conflicts) ? result.conflicts : [],
    warnings: Array.isArray(result.source_warnings) ? result.source_warnings : [],
    missing: Array.isArray(result.missing_information) ? result.missing_information : [],
  };
}

export function formatKnowledgeTurn(turn) {
  return `${turn.answer}\n\nStatus: ${turn.status}${turn.warnings.map((w) => `\n${w.message}`).join("")}${turn.conflicts.map((c) => `\nSource conflict: ${c.description}`).join("")}${turn.missing.map((m) => `\nMissing information: ${m}`).join("")}\n\n${turn.citations.map((citation) => `${citation.title}${citation.page_number ? ` · page ${citation.page_number}` : ""}\n${citation.excerpt || ""}`).join("\n\n")}`;
}
