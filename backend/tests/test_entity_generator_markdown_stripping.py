"""Tests for markdown code block stripping in entity generator."""

from __future__ import annotations

from app.jobs.architect.entity_generator import EntityGenerator


def test_entity_generator_strip_markdown_code_blocks():
    """Test the _strip_markdown_code_blocks static method in EntityGenerator."""
    
    # Test with HTML in markdown code blocks
    input_text = "```html\n<article><h2>Title</h2><p>Content</p></article>\n```"
    expected = "<article><h2>Title</h2><p>Content</p></article>"
    result = EntityGenerator._strip_markdown_code_blocks(input_text)
    assert result == expected
    
    # Test with plain code blocks
    input_text = "```\n<p>Content</p>\n```"
    expected = "<p>Content</p>"
    result = EntityGenerator._strip_markdown_code_blocks(input_text)
    assert result == expected
    
    # Test with no markdown
    input_text = "<p>HTML without markdown</p>"
    result = EntityGenerator._strip_markdown_code_blocks(input_text)
    assert result == input_text
    
    # Test with plain text
    input_text = "Plain text without any markup"
    result = EntityGenerator._strip_markdown_code_blocks(input_text)
    assert result == input_text
    
    # Test with empty string
    result = EntityGenerator._strip_markdown_code_blocks("")
    assert result == ""
    
    # Test with None (should handle gracefully)
    result = EntityGenerator._strip_markdown_code_blocks(None)
    assert result == ""
