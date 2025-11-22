"""Tests for markdown code block stripping in architect generation."""

from __future__ import annotations

import pytest

from app.tasks.architect_generation_v2 import (
    _strip_markdown_code_blocks,
    _normalize_timeline_event_entry,
)


def test_strip_markdown_code_blocks_with_html():
    """Test stripping markdown code blocks from HTML content."""
    input_text = "```html\n<article><h2>Title</h2><p>Content</p></article>\n```"
    expected = "<article><h2>Title</h2><p>Content</p></article>"
    result = _strip_markdown_code_blocks(input_text)
    assert result == expected


def test_strip_markdown_code_blocks_without_language():
    """Test stripping markdown code blocks without language identifier."""
    input_text = "```\n<p>Some content</p>\n```"
    expected = "<p>Some content</p>"
    result = _strip_markdown_code_blocks(input_text)
    assert result == expected


def test_strip_markdown_code_blocks_plain_text():
    """Test that plain text without code blocks is unchanged."""
    input_text = "Plain text without markdown"
    result = _strip_markdown_code_blocks(input_text)
    assert result == input_text


def test_strip_markdown_code_blocks_html_without_markdown():
    """Test that HTML without markdown delimiters is unchanged."""
    input_text = "<p>HTML without markdown</p>"
    result = _strip_markdown_code_blocks(input_text)
    assert result == input_text


def test_strip_markdown_code_blocks_empty():
    """Test handling of empty string."""
    result = _strip_markdown_code_blocks("")
    assert result == ""


def test_strip_markdown_code_blocks_multiline_html():
    """Test stripping markdown from multiline HTML content."""
    input_text = """```html
<article>
  <h2>Jessie Williams</h2>
  <section>
    <h3>Background</h3>
    <p>Student turned investigator.</p>
  </section>
</article>
```"""
    expected = """<article>
  <h2>Jessie Williams</h2>
  <section>
    <h3>Background</h3>
    <p>Student turned investigator.</p>
  </section>
</article>"""
    result = _strip_markdown_code_blocks(input_text)
    assert result == expected


def test_normalize_timeline_event_strips_markdown_from_title():
    """Test that timeline event normalization strips markdown from title."""
    entry = {
        "title": "```html\n<h2>Event Title</h2>\n```",
        "description": "Event description",
        "order": 1,
    }
    result = _normalize_timeline_event_entry(entry, chunk_index=0, fallback_order=1)
    assert result is not None
    assert result["title"] == "<h2>Event Title</h2>"


def test_normalize_timeline_event_strips_markdown_from_description():
    """Test that timeline event normalization strips markdown from description."""
    entry = {
        "title": "Event Title",
        "description": "```html\n<p>Event description with HTML</p>\n```",
        "order": 1,
    }
    result = _normalize_timeline_event_entry(entry, chunk_index=0, fallback_order=1)
    assert result is not None
    assert result["description"] == "<p>Event description with HTML</p>"


def test_normalize_timeline_event_strips_markdown_from_both():
    """Test that timeline event normalization strips markdown from both fields."""
    entry = {
        "title": "```\nEvent Title\n```",
        "description": "```html\n<p>Event description</p>\n```",
        "order": 1,
        "related_aliases": ["Alice", "Bob"],
    }
    result = _normalize_timeline_event_entry(entry, chunk_index=0, fallback_order=1)
    assert result is not None
    assert result["title"] == "Event Title"
    assert result["description"] == "<p>Event description</p>"
    assert result["related_aliases"] == ["Alice", "Bob"]


def test_normalize_timeline_event_handles_plain_text():
    """Test that plain text in timeline events works correctly."""
    entry = {
        "title": "Plain Title",
        "description": "Plain description without HTML or markdown",
        "order": 2,
    }
    result = _normalize_timeline_event_entry(entry, chunk_index=1, fallback_order=2)
    assert result is not None
    assert result["title"] == "Plain Title"
    assert result["description"] == "Plain description without HTML or markdown"
