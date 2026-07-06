from __future__ import annotations

import html
import json
import logging
import re
from typing import Any

from app.core.config import Settings
from app.integrations.clients import ShreckLLMClient, ShrecknetProviderClient
from app.jobs.prompts import (
    COMPANION_POLICY_PROMPT,
    DOWNSTREAM_LIBRARIAN_PROMPT,
    PLANNER_PROMPT,
    SYNTHESIS_PROMPT,
    TURN_REFLECTION_PROMPT,
)

logger = logging.getLogger(__name__)


class HeraldOrchestrator:
    def __init__(self, settings: Settings, llm_client: ShreckLLMClient, provider_client: ShrecknetProviderClient):
        self.settings = settings
        self.llm_client = llm_client
        self.provider_client = provider_client

    @staticmethod
    def _debug_text(value: Any, *, limit: int = 4000) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit]}... [truncated {len(text) - limit} chars]"

    @staticmethod
    def query_has_named_subject(text: str) -> bool:
        query = str(text or "").strip()
        if not query:
            return False
        ignored_tokens = {
            "and",
            "how",
            "what",
            "when",
            "where",
            "who",
            "why",
            "sanity",
            "character",
            "characters",
            "rule",
            "rules",
            "mechanic",
            "mechanics",
            "game",
            "system",
        }
        for match in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b", query):
            token = match.group(0).strip()
            if token.lower() not in ignored_tokens:
                return True
        return False

    @classmethod
    def is_generic_rules_query(cls, text: str) -> bool:
        query = str(text or "").strip()
        lowered = query.lower()
        rules_terms = (
            "rule",
            "rules",
            "mechanic",
            "mechanics",
            "sanity",
            "occupation",
            "stat",
            "stats",
            "dice",
            "roll",
            "recover",
            "combat",
            "skill",
            "points",
        )
        canon_terms = ("story", "canon", "scene", "timeline", "event", "background", "history", "who is")
        return any(term in lowered for term in rules_terms) and not any(term in lowered for term in canon_terms) and not cls.query_has_named_subject(query)

    @classmethod
    def query_mentions_rules(cls, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        rules_terms = (
            "rule",
            "rules",
            "mechanic",
            "mechanics",
            "sanity",
            "occupation",
            "stat",
            "stats",
            "dice",
            "roll",
            "recover",
            "combat",
            "skill",
            "points",
            "book",
            "page",
            "system",
        )
        return any(term in lowered for term in rules_terms)

    @classmethod
    def is_lore_identity_query(cls, text: str) -> bool:
        query = str(text or "").strip()
        if not query:
            return False
        return cls.query_has_named_subject(query) and not cls.query_mentions_rules(query)

    @staticmethod
    def normalize_entity_name(name: str) -> str:
        return re.sub(r"\s+", " ", str(name or "").strip())

    @classmethod
    def extract_named_entities_from_text(cls, text: str) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        seen: set[str] = set()
        for match in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b", str(text or "")):
            name = cls.normalize_entity_name(match.group(0))
            if not name or name.lower() in {"and", "how", "what", "when", "where", "who", "why"}:
                continue
            lowered = name.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            entities.append({"name": name, "type": "subject", "confidence": 0.7})
        return entities

    @staticmethod
    def query_uses_pronoun(text: str) -> bool:
        return bool(re.search(r"\b(he|she|they|him|her|them|his|hers|their|that book|this system|that system)\b", str(text or ""), re.IGNORECASE))

    @staticmethod
    def extract_json_object(raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _clamp_style_value(value: Any, *, default: float = 0.5) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(0.0, min(1.0, parsed))

    @staticmethod
    def default_companion_policy(query: str) -> dict[str, Any]:
        return {
            "chat_goal": "Help the user effectively with the current conversation topic.",
            "turn_intention": "Answer the user query clearly and grounded.",
            "conversation_mode": "general_assistant",
            "user_need": "direct_answer",
            "needs_knowledge_tools": True,
            "suggested_response_style": {
                "directness": 0.7,
                "technical_depth": 0.7,
                "playfulness": 0.3,
                "initiative": 0.5,
            },
            "open_threads": [str(query or "").strip()] if str(query or "").strip() else [],
            "next_best_actions": ["answer_user_query"],
        }

    @classmethod
    def normalize_companion_policy(cls, raw: dict[str, Any], *, query: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return cls.default_companion_policy(query)
        style_raw = raw.get("suggested_response_style") if isinstance(raw.get("suggested_response_style"), dict) else {}
        return {
            "chat_goal": str(raw.get("chat_goal") or "").strip() or cls.default_companion_policy(query)["chat_goal"],
            "turn_intention": str(raw.get("turn_intention") or "").strip() or cls.default_companion_policy(query)["turn_intention"],
            "conversation_mode": str(raw.get("conversation_mode") or "general_assistant").strip() or "general_assistant",
            "user_need": str(raw.get("user_need") or "direct_answer").strip() or "direct_answer",
            "needs_knowledge_tools": bool(raw.get("needs_knowledge_tools", True)),
            "suggested_response_style": {
                "directness": cls._clamp_style_value(style_raw.get("directness"), default=0.7),
                "technical_depth": cls._clamp_style_value(style_raw.get("technical_depth"), default=0.7),
                "playfulness": cls._clamp_style_value(style_raw.get("playfulness"), default=0.3),
                "initiative": cls._clamp_style_value(style_raw.get("initiative"), default=0.5),
            },
            "open_threads": [str(item).strip() for item in (raw.get("open_threads") or []) if str(item).strip()][:20],
            "next_best_actions": [str(item).strip() for item in (raw.get("next_best_actions") or []) if str(item).strip()][:10],
        }

    async def plan_companion_policy(
        self,
        *,
        query: str,
        conversation_context: dict[str, Any] | None,
        chat_state: dict[str, Any] | None,
        rapport_profile: dict[str, Any] | None,
        debug_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = conversation_context or {}
        prompt = COMPANION_POLICY_PROMPT.format(
            query=query,
            conversation_summary=str(context.get("summary_used") or "none"),
            recent_conversation=self.format_recent_messages(context.get("recent_messages_used") or []),
            active_context=self.format_active_context(
                context.get("active_entities_used") or [],
                context.get("resolved_subject"),
            ),
            chat_state=json.dumps(chat_state or {}, ensure_ascii=True),
            rapport_profile=json.dumps(rapport_profile or {}, ensure_ascii=True),
        )
        logger.info(
            "companion policy llm request usage_tag=%s query=%s prompt=%s",
            "companion_orchestrator.policy",
            self._debug_text(query, limit=1000),
            self._debug_text(prompt),
        )
        if isinstance(debug_trace, dict):
            debug_trace["policy"] = {
                "usage_tag": "companion_orchestrator.policy",
                "provider": self.settings.model_personal_companion_policy.provider,
                "model": self.settings.model_personal_companion_policy.name,
                "prompt": prompt,
            }
        try:
            raw = await self.llm_client.chat(
                provider_id=self.settings.model_personal_companion_policy.provider,
                model=self.settings.model_personal_companion_policy.name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.settings.policy_temperature,
                usage_tag="companion_orchestrator.policy",
            )
            logger.info(
                "companion policy llm response usage_tag=%s response=%s",
                "companion_orchestrator.policy",
                self._debug_text(raw),
            )
            if isinstance(debug_trace, dict) and isinstance(debug_trace.get("policy"), dict):
                debug_trace["policy"]["response"] = raw
            return self.normalize_companion_policy(self.extract_json_object(raw), query=query)
        except Exception as exc:
            logger.exception("companion policy llm failed error=%s", exc)
            if isinstance(debug_trace, dict) and isinstance(debug_trace.get("policy"), dict):
                debug_trace["policy"]["error"] = str(exc)
            return self.default_companion_policy(query)

    @staticmethod
    def default_turn_reflection() -> dict[str, Any]:
        return {
            "answered_user": True,
            "confidence": 0.6,
            "user_state_estimate": {
                "engagement": "medium",
                "frustration": "low",
                "confusion": "low",
                "boredom": "low",
            },
            "response_quality": {
                "too_verbose": False,
                "too_dry": False,
                "missed_question": False,
                "needs_more_concrete_next_step": False,
            },
            "proactivity": {
                "should_be_proactive": False,
                "proactivity_type": "none",
                "proactive_message": "",
            },
            "chat_state_patch": {
                "chat_goal": "",
                "current_intention": "",
                "open_threads_add": [],
                "open_threads_resolved": [],
                "next_best_actions": [],
            },
            "rapport_patch": [],
        }

    @classmethod
    def normalize_turn_reflection(cls, raw: dict[str, Any]) -> dict[str, Any]:
        defaults = cls.default_turn_reflection()
        if not isinstance(raw, dict):
            return defaults
        user_state = raw.get("user_state_estimate") if isinstance(raw.get("user_state_estimate"), dict) else {}
        quality = raw.get("response_quality") if isinstance(raw.get("response_quality"), dict) else {}
        proactivity = raw.get("proactivity") if isinstance(raw.get("proactivity"), dict) else {}
        chat_patch = raw.get("chat_state_patch") if isinstance(raw.get("chat_state_patch"), dict) else {}
        rapport_patch: list[dict[str, Any]] = []
        for item in raw.get("rapport_patch") or []:
            if not isinstance(item, dict):
                continue
            trait = str(item.get("trait") or "").strip()
            if not trait:
                continue
            try:
                delta = float(item.get("delta") or 0.0)
            except (TypeError, ValueError):
                delta = 0.0
            try:
                confidence = float(item.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            rapport_patch.append(
                {
                    "trait": trait,
                    "delta": max(-1.0, min(1.0, delta)),
                    "confidence": max(0.0, min(1.0, confidence)),
                    "reason": str(item.get("reason") or "").strip(),
                }
            )
        return {
            "answered_user": bool(raw.get("answered_user", defaults["answered_user"])),
            "confidence": max(0.0, min(1.0, float(raw.get("confidence") or defaults["confidence"]))),
            "user_state_estimate": {
                "engagement": str(user_state.get("engagement") or defaults["user_state_estimate"]["engagement"]),
                "frustration": str(user_state.get("frustration") or defaults["user_state_estimate"]["frustration"]),
                "confusion": str(user_state.get("confusion") or defaults["user_state_estimate"]["confusion"]),
                "boredom": str(user_state.get("boredom") or defaults["user_state_estimate"]["boredom"]),
            },
            "response_quality": {
                "too_verbose": bool(quality.get("too_verbose", defaults["response_quality"]["too_verbose"])),
                "too_dry": bool(quality.get("too_dry", defaults["response_quality"]["too_dry"])),
                "missed_question": bool(quality.get("missed_question", defaults["response_quality"]["missed_question"])),
                "needs_more_concrete_next_step": bool(
                    quality.get(
                        "needs_more_concrete_next_step",
                        defaults["response_quality"]["needs_more_concrete_next_step"],
                    )
                ),
            },
            "proactivity": {
                "should_be_proactive": bool(proactivity.get("should_be_proactive", False)),
                "proactivity_type": str(proactivity.get("proactivity_type") or "none").strip() or "none",
                "proactive_message": str(proactivity.get("proactive_message") or "").strip(),
            },
            "chat_state_patch": {
                "chat_goal": str(chat_patch.get("chat_goal") or "").strip(),
                "current_intention": str(chat_patch.get("current_intention") or "").strip(),
                "open_threads_add": [str(item).strip() for item in (chat_patch.get("open_threads_add") or []) if str(item).strip()][:20],
                "open_threads_resolved": [str(item).strip() for item in (chat_patch.get("open_threads_resolved") or []) if str(item).strip()][:20],
                "next_best_actions": [str(item).strip() for item in (chat_patch.get("next_best_actions") or []) if str(item).strip()][:10],
            },
            "rapport_patch": rapport_patch,
        }

    async def evaluate_turn_reflection(
        self,
        *,
        query: str,
        final_text: str,
        execution: dict[str, Any],
        chat_state: dict[str, Any] | None,
        rapport_profile: dict[str, Any] | None,
        debug_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        summary = json.dumps(
            {
                "completed_steps": execution.get("completed_steps") or [],
                "stopped_reason": execution.get("stopped_reason"),
            },
            ensure_ascii=True,
        )
        prompt = TURN_REFLECTION_PROMPT.format(
            query=query,
            final_text=final_text,
            execution_summary=summary,
            chat_state=json.dumps(chat_state or {}, ensure_ascii=True),
            rapport_profile=json.dumps(rapport_profile or {}, ensure_ascii=True),
        )
        logger.info(
            "companion reflection llm request usage_tag=%s query=%s prompt=%s",
            "companion_orchestrator.reflection",
            self._debug_text(query, limit=1000),
            self._debug_text(prompt),
        )
        if isinstance(debug_trace, dict):
            debug_trace["reflection"] = {
                "usage_tag": "companion_orchestrator.reflection",
                "provider": self.settings.model_personal_companion_reflection.provider,
                "model": self.settings.model_personal_companion_reflection.name,
                "prompt": prompt,
            }
        try:
            raw = await self.llm_client.chat(
                provider_id=self.settings.model_personal_companion_reflection.provider,
                model=self.settings.model_personal_companion_reflection.name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.settings.reflection_temperature,
                usage_tag="companion_orchestrator.reflection",
            )
            logger.info(
                "companion reflection llm response usage_tag=%s response=%s",
                "companion_orchestrator.reflection",
                self._debug_text(raw),
            )
            if isinstance(debug_trace, dict) and isinstance(debug_trace.get("reflection"), dict):
                debug_trace["reflection"]["response"] = raw
            return self.normalize_turn_reflection(self.extract_json_object(raw))
        except Exception as exc:
            logger.exception("companion reflection llm failed error=%s", exc)
            if isinstance(debug_trace, dict) and isinstance(debug_trace.get("reflection"), dict):
                debug_trace["reflection"]["error"] = str(exc)
            return self.default_turn_reflection()

    @staticmethod
    def default_plan(query: str) -> dict[str, Any]:
        q = str(query or "").lower()
        librarian_terms = ("rule", "rules", "mechanic", "mechanics", "occupation", "stat", "dice", "roll", "sanity", "recover")
        elder_terms = ("story", "character", "who", "when", "where", "what happened", "canon", "scene", "timeline")
        use_librarian = any(term in q for term in librarian_terms)
        use_elder = any(term in q for term in elder_terms)
        if use_librarian and HeraldOrchestrator.query_has_named_subject(str(query or "")):
            use_elder = True
        if HeraldOrchestrator.is_generic_rules_query(query):
            use_elder = False
            use_librarian = True
        if use_elder and use_librarian:
            return {
                "needs_tools": True,
                "no_tools_reason": "",
                "strategy": "sequential",
                "reason": "keyword_fallback_mixed_query",
                "steps": [
                    {
                        "step_id": "step-1",
                        "tool_job": "elder",
                        "goal": "Gather grounded canon context needed for the rules question.",
                        "query": query,
                        "depends_on": [],
                        "use_prior_context": False,
                        "success_requirements": ["grounded_subject_context"],
                        "on_failure": "stop",
                    },
                    {
                        "step_id": "step-2",
                        "tool_job": "librarian",
                        "goal": "Answer the rules question using the grounded canon context.",
                        "query": query,
                        "depends_on": ["step-1"],
                        "use_prior_context": True,
                        "success_requirements": ["rules_answer"],
                        "on_failure": "stop",
                    },
                ],
            }
        tool_job = "librarian" if use_librarian and not use_elder else "elder"
        return {
            "needs_tools": True,
            "no_tools_reason": "",
            "strategy": "parallel",
            "reason": "keyword_fallback_single_tool",
            "steps": [
                {
                    "step_id": "step-1",
                    "tool_job": tool_job,
                    "goal": "Answer the user question directly.",
                    "query": query,
                    "depends_on": [],
                    "use_prior_context": False,
                    "success_requirements": ["direct_answer"],
                    "on_failure": "stop",
                }
            ],
        }

    @staticmethod
    def format_recent_messages(messages: list[dict[str, Any]]) -> str:
        if not messages:
            return "none"
        return "\n".join(f"{str(item.get('role') or 'unknown')}: {str(item.get('content') or '').strip()}" for item in messages)

    @staticmethod
    def format_active_context(active_entities: list[dict[str, Any]], last_resolved_subject: str | None) -> str:
        if not active_entities and not last_resolved_subject:
            return "none"
        lines = []
        if last_resolved_subject:
            lines.append(f"last_resolved_subject: {last_resolved_subject}")
        for entity in active_entities:
            if not isinstance(entity, dict):
                continue
            lines.append(
                f"entity: name={entity.get('name')} type={entity.get('type') or 'subject'} confidence={entity.get('confidence')}"
            )
        return "\n".join(lines) or "none"

    @staticmethod
    def format_available_tools(allocated_tools: dict[str, Any] | None) -> str:
        allocated = allocated_tools or {}
        lines: list[str] = []
        for job in ("elder", "librarian"):
            items = [item for item in (allocated.get(job) or []) if isinstance(item, dict)]
            if not items:
                lines.append(f"{job}: none")
                continue
            rendered = [f"{str(item.get('id') or '').strip()} ({str(item.get('name') or '').strip()})" for item in items if str(item.get("id") or "").strip()]
            lines.append(f"{job}: {', '.join(rendered) if rendered else 'none'}")
        return "\n".join(lines)

    def build_conversation_context(self, query: str, chat_payload: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(chat_payload or {})
        memory = payload.get("memory") if isinstance(payload.get("memory"), dict) else {}
        all_messages = [item for item in payload.get("messages") or [] if isinstance(item, dict)]
        recent_limit = max(1, int(self.settings.conversation_recent_messages_limit))
        recent_messages = all_messages[-recent_limit:]
        summary_used = str(memory.get("summary") or "").strip()
        active_entities = [item for item in (memory.get("active_entities") or []) if isinstance(item, dict)]
        last_resolved_subject = str(memory.get("last_resolved_subject") or "").strip() or None
        resolved_subject = None
        if self.query_has_named_subject(query):
            extracted = self.extract_named_entities_from_text(query)
            resolved_subject = extracted[0]["name"] if extracted else None
        elif self.query_uses_pronoun(query) and active_entities:
            resolved_subject = str(active_entities[0].get("name") or "").strip() or last_resolved_subject
        elif self.query_uses_pronoun(query):
            resolved_subject = last_resolved_subject
        rewritten_query = str(query or "")
        if resolved_subject and self.query_uses_pronoun(query):
            rewritten_query = re.sub(r"\b(he|she|they|him|her|them)\b", resolved_subject, rewritten_query, count=1, flags=re.IGNORECASE)
            rewritten_query = re.sub(r"\b(his|hers|their)\b", f"{resolved_subject}'s", rewritten_query, count=1, flags=re.IGNORECASE)
        context = {
            "recent_messages_used": recent_messages,
            "summary_used": summary_used,
            "active_entities_used": active_entities,
            "resolved_subject": resolved_subject,
            "rewritten_query": rewritten_query,
            "last_resolved_subject": last_resolved_subject,
        }
        budget = max(500, int(self.settings.conversation_context_char_limit))
        while len(self.format_recent_messages(context["recent_messages_used"])) + len(summary_used) > budget and context["recent_messages_used"]:
            context["recent_messages_used"] = context["recent_messages_used"][1:]
        return context

    async def plan_query(
        self,
        query: str,
        *,
        conversation_context: dict[str, Any] | None = None,
        allocated_tools: dict[str, Any] | None = None,
        companion_policy: dict[str, Any] | None = None,
        debug_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = conversation_context or {}
        prompt = PLANNER_PROMPT.format(
            query=query,
            companion_policy=json.dumps(companion_policy or {}, ensure_ascii=True),
            available_tools=self.format_available_tools(allocated_tools),
            conversation_summary=str(context.get("summary_used") or "none"),
            recent_conversation=self.format_recent_messages(context.get("recent_messages_used") or []),
            active_context=self.format_active_context(
                context.get("active_entities_used") or [],
                context.get("resolved_subject"),
            ),
        )
        logger.info(
            "companion planner llm request usage_tag=%s query=%s prompt=%s",
            "companion_orchestrator.planning",
            self._debug_text(query, limit=1000),
            self._debug_text(prompt),
        )
        if isinstance(debug_trace, dict):
            debug_trace["planning"] = {
                "usage_tag": "companion_orchestrator.planning",
                "provider": self.settings.model_personal_companion_routing.provider,
                "model": self.settings.model_personal_companion_routing.name,
                "prompt": prompt,
            }
        try:
            raw = await self.llm_client.chat(
                provider_id=self.settings.model_personal_companion_routing.provider,
                model=self.settings.model_personal_companion_routing.name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.settings.routing_temperature,
                usage_tag="companion_orchestrator.planning",
            )
            logger.info(
                "companion planner llm response usage_tag=%s response=%s",
                "companion_orchestrator.planning",
                self._debug_text(raw),
            )
            if isinstance(debug_trace, dict) and isinstance(debug_trace.get("planning"), dict):
                debug_trace["planning"]["response"] = raw
            parsed = self.extract_json_object(raw)
            normalized = self.normalize_plan(parsed, query=query)
            logger.info(
                "companion planner normalized plan strategy=%s reason=%s steps=%s",
                normalized.get("strategy"),
                normalized.get("reason"),
                self._debug_text(json.dumps(normalized.get("steps") or [], ensure_ascii=True), limit=3000),
            )
            if "needs_tools" in normalized:
                return normalized
        except Exception as exc:
            logger.exception("companion planner llm failed error=%s", exc)
            if isinstance(debug_trace, dict) and isinstance(debug_trace.get("planning"), dict):
                debug_trace["planning"]["error"] = str(exc)
        return self.default_plan(query)

    @staticmethod
    def normalize_plan(raw: dict[str, Any], *, query: str) -> dict[str, Any]:
        strategy = str(raw.get("strategy") or "parallel").strip().lower()
        if strategy not in {"parallel", "sequential"}:
            strategy = "parallel"
        needs_tools_raw = raw.get("needs_tools")
        explicit_needs_tools = isinstance(needs_tools_raw, bool)
        if explicit_needs_tools and needs_tools_raw is False:
            return {
                "needs_tools": False,
                "no_tools_reason": str(raw.get("no_tools_reason") or raw.get("reason") or "Planner judged no tool call is required.").strip(),
                "strategy": "parallel",
                "reason": str(raw.get("reason") or "no_tools_needed").strip() or "no_tools_needed",
                "steps": [],
            }
        normalized_steps: list[dict[str, Any]] = []
        for index, step in enumerate(raw.get("steps") or [], start=1):
            if not isinstance(step, dict):
                continue
            tool_job = str(step.get("tool_job") or "").strip().lower()
            if tool_job not in {"elder", "librarian"}:
                continue
            step_id = str(step.get("step_id") or f"step-{index}")
            subquery = str(step.get("query") or query).strip()
            if not subquery:
                subquery = query
            depends_on = [str(item) for item in (step.get("depends_on") or []) if str(item).strip()]
            normalized_steps.append(
                {
                    "step_id": step_id,
                    "tool_job": tool_job,
                    "goal": str(step.get("goal") or "Answer part of the user query.").strip(),
                    "query": subquery,
                    "depends_on": depends_on,
                    "use_prior_context": bool(step.get("use_prior_context")),
                    "success_requirements": [str(item) for item in (step.get("success_requirements") or []) if str(item).strip()],
                    "on_failure": "stop",
                }
            )
        if not normalized_steps:
            if explicit_needs_tools:
                return {
                    "needs_tools": False,
                    "no_tools_reason": str(raw.get("no_tools_reason") or "Planner requested tools but returned no executable steps.").strip(),
                    "strategy": "parallel",
                    "reason": "planner_returned_no_steps",
                    "steps": [],
                }
            return HeraldOrchestrator.default_plan(query)
        if strategy == "parallel" and any(step.get("depends_on") for step in normalized_steps):
            strategy = "sequential"

        # Guard against invalid planner outputs:
        # - first step cannot depend on prior context
        # - any prior-context step must point to an already seen dependency step
        seen_step_ids: set[str] = set()
        for index, step in enumerate(normalized_steps, start=1):
            step_id = str(step.get("step_id") or f"step-{index}")
            dependencies = [str(item) for item in (step.get("depends_on") or []) if str(item).strip()]
            valid_dependencies = [dep for dep in dependencies if dep in seen_step_ids]
            uses_prior_context = bool(step.get("use_prior_context"))
            is_librarian_step = str(step.get("tool_job") or "") == "librarian"
            if index == 1:
                step["use_prior_context"] = False
                step["depends_on"] = []
            elif is_librarian_step:
                # Librarian can opportunistically consume prior canon context,
                # but it must not hard-depend on Elder.
                step["depends_on"] = []
            elif uses_prior_context:
                if not valid_dependencies:
                    step["use_prior_context"] = False
                    step["depends_on"] = []
                else:
                    step["depends_on"] = [valid_dependencies[0]]
            else:
                step["depends_on"] = []
            seen_step_ids.add(step_id)

        if HeraldOrchestrator.is_lore_identity_query(query):
            elder_only_steps = [step for step in normalized_steps if str(step.get("tool_job") or "") == "elder"]
            if elder_only_steps:
                normalized_steps = elder_only_steps
                strategy = "parallel"
            else:
                return HeraldOrchestrator.default_plan(query)

        return {
            "needs_tools": True,
            "no_tools_reason": "",
            "strategy": strategy,
            "reason": str(raw.get("reason") or "llm_plan").strip() or "llm_plan",
            "steps": normalized_steps,
        }

    @classmethod
    def rewrite_query_with_subject(cls, query: str, conversation_context: dict[str, Any] | None) -> str:
        context = conversation_context or {}
        resolved_subject = str(context.get("resolved_subject") or "").strip()
        rewritten_query = str(query or "")
        if not resolved_subject or not cls.query_uses_pronoun(rewritten_query):
            return rewritten_query
        rewritten_query = re.sub(r"\b(he|she|they|him|her|them)\b", resolved_subject, rewritten_query, count=1, flags=re.IGNORECASE)
        rewritten_query = re.sub(r"\b(his|hers|their)\b", f"{resolved_subject}'s", rewritten_query, count=1, flags=re.IGNORECASE)
        return rewritten_query

    @staticmethod
    def plan_selected_tools(plan: dict[str, Any], allocated: dict[str, Any]) -> dict[str, list[str]]:
        selected = {"elder": [], "librarian": []}
        available_by_job = {
            "elder": [str(item.get("id")) for item in allocated.get("elder", []) if item.get("id")],
            "librarian": [str(item.get("id")) for item in allocated.get("librarian", []) if item.get("id")],
        }
        for step in plan.get("steps") or []:
            job = str(step.get("tool_job") or "")
            if job not in selected or not available_by_job.get(job):
                continue
            agent_id = available_by_job[job][0]
            if agent_id not in selected[job]:
                selected[job].append(agent_id)
        return selected

    @staticmethod
    def build_canon_context(step_result: dict[str, Any]) -> dict[str, Any]:
        answer = str(step_result.get("answer") or "").strip()
        sources = [src for src in (step_result.get("sources") or []) if isinstance(src, dict)]
        subject = ""
        grounded_roles: list[str] = []
        grounded_traits: list[str] = []
        grounded_behaviors: list[str] = []
        grounded_uncertainties: list[str] = []
        named_nodes: list[str] = []
        for source in sources:
            node_name = str(source.get("node_name") or "").strip()
            if node_name:
                named_nodes.append(node_name)
                if not subject and source.get("node_label"):
                    subject = node_name
        sentences = [chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+", answer) if chunk.strip()]
        for sentence in sentences:
            lowered = sentence.lower()
            if any(token in lowered for token in ("unclear", "unknown", "not enough", "does not say", "no mention", "cannot tell")):
                grounded_uncertainties.append(sentence)
                continue
            if any(token in lowered for token in ("works", "office", "occupation", "job", "role", "profession", "colleague")):
                grounded_roles.append(sentence)
            elif any(token in lowered for token in ("careful", "secretive", "quiet", "aggressive", "kind", "politic", "social", "behavior", "personality")):
                grounded_traits.append(sentence)
            else:
                grounded_behaviors.append(sentence)
        if not subject and named_nodes:
            subject = named_nodes[0]
        evidence_note = "Named source nodes: " + ", ".join(named_nodes[:5]) if named_nodes else "No named source nodes available."
        return {
            "resolved_subject": subject,
            "grounded_traits": grounded_traits[:3],
            "grounded_roles": grounded_roles[:3],
            "grounded_behaviors": grounded_behaviors[:3],
            "grounded_uncertainties": grounded_uncertainties[:3],
            "evidence_note": evidence_note,
        }

    @staticmethod
    def canon_context_is_sufficient(context: dict[str, Any]) -> bool:
        if str(context.get("resolved_subject") or "").strip():
            if context.get("grounded_traits") or context.get("grounded_roles") or context.get("grounded_behaviors"):
                return True
        return False

    @staticmethod
    def format_canon_context(context: dict[str, Any]) -> str:
        return "\n".join(
            [
                f"resolved_subject: {context.get('resolved_subject') or 'unknown'}",
                f"grounded_traits: {json.dumps(context.get('grounded_traits') or [], ensure_ascii=True)}",
                f"grounded_roles: {json.dumps(context.get('grounded_roles') or [], ensure_ascii=True)}",
                f"grounded_behaviors: {json.dumps(context.get('grounded_behaviors') or [], ensure_ascii=True)}",
                f"grounded_uncertainties: {json.dumps(context.get('grounded_uncertainties') or [], ensure_ascii=True)}",
                f"evidence_note: {context.get('evidence_note') or 'none'}",
            ]
        )

    @staticmethod
    def build_librarian_query(*, subquery: str, canon_context: dict[str, Any]) -> str:
        return DOWNSTREAM_LIBRARIAN_PROMPT.format(
            subquery=subquery,
            canon_context=HeraldOrchestrator.format_canon_context(canon_context),
        )

    @staticmethod
    def infer_node_type(source: dict[str, Any]) -> str:
        node_label = str(source.get("node_label") or "").strip().lower()
        if node_label == "scene":
            return "scene"
        if node_label == "milestone":
            return "milestone"
        for chunk in source.get("evidence_chunks") or source.get("evidence") or []:
            chunk_type = str((chunk or {}).get("chunk_type") or "").strip().lower()
            if chunk_type.startswith("scene_"):
                return "scene"
            if chunk_type.startswith("milestone_"):
                return "milestone"
        return "general"

    @staticmethod
    def enrich_agent_responses(agent_responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for item in agent_responses:
            cloned = dict(item)
            sources: list[dict[str, Any]] = []
            for source in item.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                enriched_source = dict(source)
                if item.get("agent_job") == "librarian" or source.get("library_item_id") is not None:
                    enriched_source["source_type"] = "book"
                    enriched_source["node_type"] = str(enriched_source.get("node_type") or "general")
                    enriched_source["ontology_id"] = item.get("ontology_id")
                    enriched_source["agent_id"] = item.get("agent_id")
                    enriched_source["agent_name"] = item.get("agent_name")
                    enriched_source["agent_job"] = item.get("agent_job")
                else:
                    enriched_source["source_type"] = "canon"
                    enriched_source["node_type"] = HeraldOrchestrator.infer_node_type(enriched_source)
                sources.append(enriched_source)
            if sources:
                cloned["sources"] = sources
            enriched.append(cloned)
        return enriched

    @staticmethod
    def group_book_sources(agent_responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[int, dict[str, Any]] = {}
        for item in agent_responses:
            for source in item.get("sources") or []:
                if not isinstance(source, dict) or source.get("source_type") != "book":
                    continue
                library_item_id = int(source.get("library_item_id") or 0)
                if library_item_id <= 0:
                    continue
                current = grouped.setdefault(
                    library_item_id,
                    {
                        "source_type": "book",
                        "library_item_id": library_item_id,
                        "book_title": source.get("book_title"),
                        "book_authors": source.get("book_authors"),
                        "ontology_id": source.get("ontology_id"),
                        "pdf_url": source.get("pdf_url"),
                        "page_urls": [],
                        "pages": [],
                        "excerpt_count": 0,
                        "agent_id": source.get("agent_id"),
                        "agent_name": source.get("agent_name"),
                    },
                )
                page_number = int(source.get("page_number") or 0)
                page_url = str(source.get("page_url") or "").strip()
                current["excerpt_count"] += 1
                if page_number > 0 and page_number not in current["pages"]:
                    current["pages"].append(page_number)
                if page_url and page_url not in current["page_urls"]:
                    current["page_urls"].append(page_url)
        for value in grouped.values():
            value["pages"].sort()
        return sorted(grouped.values(), key=lambda item: (str(item.get("book_title") or ""), int(item.get("library_item_id") or 0)))

    @staticmethod
    def _build_book_label(source: dict[str, Any]) -> str:
        title = str(source.get("book_title") or f"Book {source.get('library_item_id') or ''}").strip()
        page = int(source.get("page_number") or 0)
        return f"{title}, p.{page}" if page > 0 else title

    @classmethod
    def append_missing_book_references(cls, final_text: str, agent_responses: list[dict[str, Any]]) -> str:
        text = str(final_text or "").strip()
        grouped = cls.group_book_sources(agent_responses)
        if not grouped:
            return text
        lowered = text.lower()
        missing_labels: list[str] = []
        for book in grouped:
            title = str(book.get("book_title") or "").strip()
            if title and title.lower() in lowered:
                continue
            pages = [page for page in (book.get("pages") or []) if isinstance(page, int)]
            label = title or f"Book {book.get('library_item_id')}"
            if pages:
                for page in pages:
                    missing_labels.append(f"{label}, p.{page}")
            else:
                missing_labels.append(label)
        if not missing_labels:
            return text
        return f"{text} Sources: {'; '.join(missing_labels)}."

    @staticmethod
    def build_final_references(final_text: str, agent_responses: list[dict[str, Any]]) -> dict[str, Any]:
        general_sources: dict[str, dict[str, Any]] = {}
        timeline_sources: list[dict[str, Any]] = []
        book_links: list[dict[str, Any]] = []
        final_text_lower = str(final_text or "").lower()
        for item in agent_responses:
            if not item.get("ok", True):
                continue
            item_sources = [src for src in (item.get("sources") or []) if isinstance(src, dict)]
            for source in item_sources:
                if source.get("source_type") != "book":
                    continue
                label = HeraldOrchestrator._build_book_label(source)
                occurrences = []
                start = 0
                while True:
                    index = final_text_lower.find(label.lower(), start)
                    if index < 0:
                        break
                    occurrences.append({"start": index, "end": index + len(label), "text": final_text[index : index + len(label)]})
                    start = index + len(label)
                book_links.append(
                    {
                        "source_type": "book",
                        "library_item_id": source.get("library_item_id"),
                        "book_title": source.get("book_title"),
                        "book_authors": source.get("book_authors"),
                        "ontology_id": source.get("ontology_id"),
                        "page_number": source.get("page_number"),
                        "page_url": source.get("page_url"),
                        "pdf_url": source.get("pdf_url"),
                        "agent_id": source.get("agent_id"),
                        "agent_name": source.get("agent_name"),
                        "label": label,
                        "occurrences": occurrences,
                    }
                )
            general_in_response = [src for src in item_sources if src.get("node_type") == "general"]
            for source in general_in_response:
                if source.get("source_type") == "book":
                    continue
                enriched_source = dict(source)
                enriched_source["agent_id"] = item.get("agent_id")
                enriched_source["agent_name"] = item.get("agent_name")
                general_sources[str(source.get("node_id") or "")] = enriched_source
            for source in item_sources:
                if source.get("node_type") not in {"scene", "milestone"}:
                    continue
                source_entity_instance_id = source.get("source_entity_instance_id")
                source_entity = general_sources.get(str(source_entity_instance_id or ""))
                timeline_sources.append(
                    {
                        "node_id": source.get("node_id"),
                        "node_name": source.get("node_name"),
                        "node_type": source.get("node_type"),
                        "scene_id": source.get("scene_id"),
                        "source_entity_instance_id": source_entity_instance_id,
                        "source_entity": {
                            "node_id": source_entity.get("node_id"),
                            "node_name": source_entity.get("node_name"),
                            "node_type": "general",
                        }
                        if source_entity
                        else None,
                        "agent_id": item.get("agent_id"),
                        "agent_name": item.get("agent_name"),
                        "evidence_chunks": source.get("evidence_chunks") or [],
                    }
                )
        inline_links = []
        final_text_lower = str(final_text or "").lower()
        for source in general_sources.values():
            node_name = str(source.get("node_name") or "").strip()
            if not node_name:
                continue
            occurrences = []
            start = 0
            while True:
                index = final_text_lower.find(node_name.lower(), start)
                if index < 0:
                    break
                occurrences.append({"start": index, "end": index + len(node_name), "text": final_text[index : index + len(node_name)]})
                start = index + len(node_name)
            inline_links.append(
                {
                    "node_id": source.get("node_id"),
                    "node_name": node_name,
                        "node_type": "general",
                        "source_type": "canon",
                        "agent_id": source.get("agent_id"),
                        "occurrences": occurrences,
                    }
                )
        return {
            "inline_links": inline_links,
            "timeline_sources": timeline_sources,
            "book_links": book_links,
            "book_sources": HeraldOrchestrator.group_book_sources(agent_responses),
        }

    @staticmethod
    def build_linked_final_text(final_text: str, references: dict[str, Any]) -> str:
        text = str(final_text or "")
        spans: list[dict[str, Any]] = []
        for link in references.get("inline_links") or []:
            if not isinstance(link, dict):
                continue
            node_id = str(link.get("node_id") or "").strip()
            node_name = str(link.get("node_name") or "").strip()
            node_type = str(link.get("node_type") or "").strip()
            if not node_id or not node_name or not node_type:
                continue
            for occurrence in link.get("occurrences") or []:
                if not isinstance(occurrence, dict):
                    continue
                start = occurrence.get("start")
                end = occurrence.get("end")
                if not isinstance(start, int) or not isinstance(end, int):
                    continue
                if start < 0 or end <= start or end > len(text):
                    continue
                spans.append(
                    {
                        "start": start,
                        "end": end,
                        "source_type": str(link.get("source_type") or "canon"),
                        "node_id": node_id,
                        "node_name": node_name,
                        "node_type": node_type,
                        "agent_id": link.get("agent_id"),
                        "scene_id": link.get("scene_id"),
                        "source_entity_instance_id": link.get("source_entity_instance_id"),
                    }
                )
        for link in references.get("book_links") or []:
            if not isinstance(link, dict):
                continue
            library_item_id = int(link.get("library_item_id") or 0)
            if library_item_id <= 0:
                continue
            for occurrence in link.get("occurrences") or []:
                if not isinstance(occurrence, dict):
                    continue
                start = occurrence.get("start")
                end = occurrence.get("end")
                if not isinstance(start, int) or not isinstance(end, int):
                    continue
                if start < 0 or end <= start or end > len(text):
                    continue
                spans.append(
                    {
                        "start": start,
                        "end": end,
                        "source_type": "book",
                        "library_item_id": library_item_id,
                        "book_title": link.get("book_title"),
                        "ontology_id": link.get("ontology_id"),
                        "page_number": link.get("page_number"),
                        "page_url": link.get("page_url"),
                        "pdf_url": link.get("pdf_url"),
                        "agent_id": link.get("agent_id"),
                    }
                )
        if not spans:
            return html.escape(text)
        spans.sort(key=lambda item: (int(item["start"]), -(int(item["end"]) - int(item["start"]))))
        selected: list[dict[str, Any]] = []
        cursor = 0
        for span in spans:
            if int(span["start"]) < cursor:
                continue
            selected.append(span)
            cursor = int(span["end"])
        fragments: list[str] = []
        cursor = 0
        for span in selected:
            start = int(span["start"])
            end = int(span["end"])
            if cursor < start:
                fragments.append(html.escape(text[cursor:start]))
            attrs = {"data-source-type": str(span.get("source_type") or "canon")}
            if span.get("source_type") == "book":
                attrs["data-library-item-id"] = str(span["library_item_id"])
                if span.get("book_title"):
                    attrs["data-book-title"] = str(span["book_title"])
                if span.get("ontology_id") is not None:
                    attrs["data-ontology-id"] = str(span["ontology_id"])
                if span.get("page_number"):
                    attrs["data-page-number"] = str(span["page_number"])
                if span.get("page_url"):
                    attrs["data-page-url"] = str(span["page_url"])
                if span.get("pdf_url"):
                    attrs["data-pdf-url"] = str(span["pdf_url"])
                if span.get("agent_id"):
                    attrs["data-agent-id"] = str(span["agent_id"])
            else:
                attrs["data-node-id"] = str(span["node_id"])
                attrs["data-node-name"] = str(span["node_name"])
                attrs["data-node-type"] = str(span["node_type"])
                if span.get("agent_id"):
                    attrs["data-agent-id"] = str(span["agent_id"])
                if span.get("scene_id"):
                    attrs["data-scene-id"] = str(span["scene_id"])
                if span.get("source_entity_instance_id"):
                    attrs["data-source-entity-instance-id"] = str(span["source_entity_instance_id"])
            attr_text = " ".join(f'{key}="{html.escape(value, quote=True)}"' for key, value in attrs.items())
            fragments.append(f"<a {attr_text}>{html.escape(text[start:end])}</a>")
            cursor = end
        if cursor < len(text):
            fragments.append(html.escape(text[cursor:]))
        return "".join(fragments)

    def update_conversation_memory(
        self,
        *,
        chat_payload: dict[str, Any] | None,
        query: str,
        final_text: str,
        agent_responses: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = dict(chat_payload or {})
        memory = payload.get("memory") if isinstance(payload.get("memory"), dict) else {}
        messages = [item for item in payload.get("messages") or [] if isinstance(item, dict)]
        active_entities: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source_name in self.extract_named_entities_from_text(query) + self.extract_named_entities_from_text(final_text):
            name = self.normalize_entity_name(source_name.get("name"))
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            active_entities.append({"name": name, "type": source_name.get("type") or "subject", "confidence": 0.8})
        for item in agent_responses:
            for source in item.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                source_name = self.normalize_entity_name(source.get("node_name") or source.get("book_title") or "")
                if not source_name or source_name.lower() in seen:
                    continue
                seen.add(source_name.lower())
                active_entities.append(
                    {
                        "name": source_name,
                        "type": "book" if source.get("source_type") == "book" else "subject",
                        "confidence": 0.9,
                    }
                )
        previous_entities = [item for item in (memory.get("active_entities") or []) if isinstance(item, dict)]
        for entity in previous_entities:
            name = self.normalize_entity_name(entity.get("name") or "")
            if name and name.lower() not in seen:
                active_entities.append(entity)
        active_entities = active_entities[:6]
        open_topics = []
        if query:
            open_topics.append(query)
        if messages and len(messages) > int(self.settings.conversation_summary_trigger_messages):
            older_messages = messages[:-int(self.settings.conversation_recent_messages_limit)]
            older_lines = [f"{item.get('role')}: {str(item.get('content') or '').strip()}" for item in older_messages[-6:]]
            summary = " | ".join(older_lines)
        else:
            summary = str(memory.get("summary") or "")
        return {
            "summary": summary[: max(200, int(self.settings.conversation_context_char_limit))],
            "active_entities": active_entities,
            "open_topics": open_topics[:5],
            "last_resolved_subject": active_entities[0]["name"] if active_entities and active_entities[0].get("type") != "book" else memory.get("last_resolved_subject"),
        }

    async def synthesize_final_answer(
        self,
        *,
        query: str,
        companion_name: str,
        companion_writing_style: str,
        plan: dict[str, Any],
        execution: dict[str, Any],
        agent_responses: list[dict[str, Any]],
        debug_trace: dict[str, Any] | None = None,
    ) -> str:
        step_lines: list[str] = []
        results_by_step = {str(item.get("step_id")): item for item in execution.get("completed_steps") or [] if isinstance(item, dict)}
        for step in plan.get("steps") or []:
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("step_id") or "")
            result = results_by_step.get(step_id)
            if not result:
                continue
            answer = str(result.get("answer") or "").strip()
            if answer:
                source_summary = ""
                if str(result.get("tool_job") or step.get("tool_job") or "") == "librarian":
                    grouped_books = self.group_book_sources([result])
                    if grouped_books:
                        labels = []
                        for book in grouped_books:
                            pages = ", ".join(f"p.{page}" for page in (book.get("pages") or []))
                            title = str(book.get("book_title") or f"Book {book.get('library_item_id')}")
                            labels.append(f"{title}{f' ({pages})' if pages else ''}")
                        source_summary = f" sources={'; '.join(labels)}"
                step_lines.append(
                    f"- [{step_id}] {step.get('tool_job')}: query={result.get('query_used')}{source_summary} answer={answer}"
                )
        stopped_reason = str(execution.get("stopped_reason") or "").strip()
        if stopped_reason:
            step_lines.append(f"- Execution stopped: {stopped_reason}")
        if not step_lines:
            return "I could not retrieve grounded evidence from the selected tools for this question. Please try a more specific question."
        prompt = SYNTHESIS_PROMPT.format(
            companion_name=companion_name,
            companion_writing_style=companion_writing_style,
            query=query,
            tool_responses="\n".join(step_lines),
        )
        logger.info(
            "companion synthesis llm request usage_tag=%s query=%s prompt=%s",
            "companion_orchestrator.synthesis",
            self._debug_text(query, limit=1000),
            self._debug_text(prompt),
        )
        if isinstance(debug_trace, dict):
            debug_trace["synthesis"] = {
                "usage_tag": "companion_orchestrator.synthesis",
                "provider": self.settings.model_personal_companion_synthesis.provider,
                "model": self.settings.model_personal_companion_synthesis.name,
                "prompt": prompt,
            }
        try:
            response = await self.llm_client.chat(
                provider_id=self.settings.model_personal_companion_synthesis.provider,
                model=self.settings.model_personal_companion_synthesis.name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.settings.synthesis_temperature,
                usage_tag="companion_orchestrator.synthesis",
            )
            logger.info(
                "companion synthesis llm response usage_tag=%s response=%s",
                "companion_orchestrator.synthesis",
                self._debug_text(response),
            )
            if isinstance(debug_trace, dict) and isinstance(debug_trace.get("synthesis"), dict):
                debug_trace["synthesis"]["response"] = response
            return response
        except Exception as exc:
            logger.exception("companion synthesis llm failed error=%s", exc)
            if isinstance(debug_trace, dict) and isinstance(debug_trace.get("synthesis"), dict):
                debug_trace["synthesis"]["error"] = str(exc)
            return "Based on available evidence:\n" + "\n".join(step_lines)

    def build_turn_payload(
        self,
        *,
        session_id: str,
        query: str,
        plan: dict[str, Any],
        execution: dict[str, Any],
        selected_tools: dict[str, list[str]],
        agent_responses: list[dict[str, Any]],
        final_text: str,
        conversation_context: dict[str, Any] | None = None,
        companion_policy: dict[str, Any] | None = None,
        turn_reflection: dict[str, Any] | None = None,
        chat_state: dict[str, Any] | None = None,
        rapport_profile: dict[str, Any] | None = None,
        rapport_patch_applied: list[dict[str, Any]] | None = None,
        llm_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        enriched_agent_responses = self.enrich_agent_responses(agent_responses)
        final_text = self.append_missing_book_references(final_text, enriched_agent_responses)
        final_references = self.build_final_references(final_text, enriched_agent_responses)
        linked_text = self.build_linked_final_text(final_text, final_references)
        routing = {
            "use_elder": bool(selected_tools.get("elder")),
            "use_librarian": bool(selected_tools.get("librarian")),
            "reason": str(plan.get("reason") or "planned_execution"),
        }
        return {
            "status": "done",
            "session_id": session_id,
            "query": query,
            "routing": routing,
            "selected_tools": selected_tools,
            "plan": plan,
            "execution": execution,
            "conversation_context": conversation_context or {},
            "companion_policy": companion_policy or {},
            "turn_reflection": turn_reflection or {},
            "chat_state": chat_state or {},
            "rapport_profile": rapport_profile or {},
            "rapport_patch_applied": rapport_patch_applied or [],
            "llm_trace": llm_trace or {},
            "agent_responses": enriched_agent_responses,
            "final": {
                "text": final_text,
                "linked_text": linked_text,
                "references": final_references,
            },
            "tool_failures": [
                {
                    "agent_id": item.get("agent_id"),
                    "agent_name": item.get("agent_name"),
                    "agent_job": item.get("agent_job"),
                    "error": item.get("error"),
                }
                for item in agent_responses
                if not item.get("ok", True)
            ],
        }
