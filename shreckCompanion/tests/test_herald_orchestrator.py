from __future__ import annotations

import html

from app.jobs.herald_orchestrator import HeraldOrchestrator


def test_default_plan_builds_sequential_mixed_query_flow() -> None:
    plan = HeraldOrchestrator.default_plan("Based on the game rules, which occupations could Ernst have?")

    assert plan["strategy"] == "sequential"
    assert [step["tool_job"] for step in plan["steps"]] == ["elder", "librarian"]
    assert plan["steps"][1]["depends_on"] == ["step-1"]
    assert plan["steps"][1]["use_prior_context"] is True


def test_build_librarian_query_includes_bounded_canon_context() -> None:
    query = HeraldOrchestrator.build_librarian_query(
        subquery="Which occupations fit Ernst?",
        canon_context={
            "resolved_subject": "Ernst von Einsenwald",
            "grounded_traits": ["He is secretive."],
            "grounded_roles": ["He works in an office."],
            "grounded_behaviors": ["He stays quiet about the past."],
            "grounded_uncertainties": [],
            "evidence_note": "Named source nodes: Ernst von Einsenwald",
        },
    )

    assert "Rules sub-question:" in query
    assert "resolved_subject: Ernst von Einsenwald" in query
    assert 'grounded_roles: ["He works in an office."]' in query
    assert "Use ONLY rules evidence" in query


def test_build_linked_final_text_links_multiple_occurrences_and_escapes_attributes() -> None:
    final_text = 'Shrek met Shrek near Fiona & Friends.'
    references = {
        "inline_links": [
            {
                "node_id": "n1",
                "node_name": 'Shrek "Ogre"',
                "node_type": "general",
                "agent_id": "elder-1",
                "occurrences": [
                    {"start": 0, "end": 5, "text": "Shrek"},
                    {"start": 10, "end": 15, "text": "Shrek"},
                ],
            }
        ]
    }

    linked = HeraldOrchestrator.build_linked_final_text(final_text, references)

    assert linked.count("<a ") == 2
    assert 'data-node-id="n1"' in linked
    assert 'data-agent-id="elder-1"' in linked
    assert 'data-node-name="Shrek &quot;Ogre&quot;"' in linked
    assert linked.endswith(html.escape(" near Fiona & Friends."))


def test_build_linked_final_text_prefers_longest_non_overlapping_spans() -> None:
    final_text = "Ernst von Einsenwald arrived."
    references = {
        "inline_links": [
            {
                "node_id": "short",
                "node_name": "Ernst",
                "node_type": "general",
                "occurrences": [{"start": 0, "end": 5, "text": "Ernst"}],
            },
            {
                "node_id": "long",
                "node_name": "Ernst von Einsenwald",
                "node_type": "general",
                "occurrences": [{"start": 0, "end": 20, "text": "Ernst von Einsenwald"}],
            },
        ]
    }

    linked = HeraldOrchestrator.build_linked_final_text(final_text, references)

    assert linked.count("<a ") == 1
    assert 'data-node-id="long"' in linked
    assert "Ernst von Einsenwald</a> arrived." in linked


def test_build_linked_final_text_returns_escaped_plain_text_without_matches() -> None:
    final_text = "No links <here> & now."

    linked = HeraldOrchestrator.build_linked_final_text(final_text, {"inline_links": []})

    assert linked == "No links &lt;here&gt; &amp; now."
