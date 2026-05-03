"""Tests for the server-level ``instructions=`` preamble."""

from __future__ import annotations

from kanbantool_mcp import server

# Hard cap mirrors the brief: longer instructions get compressed by the LLM
# (sometimes silently truncated), so additions must displace existing content,
# not pile on. Locked here so a future "just one more line" PR has to confront
# the trade-off explicitly.
INSTRUCTIONS_WORD_CAP = 150


def test_instructions_under_word_cap() -> None:
    word_count = len(server._SERVER_INSTRUCTIONS.split())
    assert word_count <= INSTRUCTIONS_WORD_CAP, (
        f"_SERVER_INSTRUCTIONS is {word_count} words; cap is {INSTRUCTIONS_WORD_CAP}. "
        "Trim or move content to llms.txt."
    )


def test_instructions_attached_to_mcp() -> None:
    assert server.mcp.instructions == server._SERVER_INSTRUCTIONS


def test_instructions_covers_required_topics() -> None:
    # The brief enumerates six topics the preamble must mention. Locking each
    # one here so a refactor that drops a topic surfaces immediately rather
    # than silently regressing the LLM's mental model.
    text = server._SERVER_INSTRUCTIONS
    # (a) board-first discovery
    assert "list_boards" in text
    # (b) "me" → whoami; names → list_board_collaborators
    assert "whoami" in text
    assert "list_board_collaborators" in text
    # (c) recent_changes polls (no webhooks)
    assert "recent_changes" in text
    assert "webhooks" in text
    # (d) None semantics — omit-not-clear except set_custom_field
    assert "None" in text
    assert "omit" in text
    assert "set_custom_field" in text
    # (e) destructive ops carry destructiveHint annotation
    assert "destructiveHint" in text
    # (f) typed exception ladder
    assert "KanbanToolError" in text
    assert "KanbanToolPermissionError" in text
    assert "KanbanToolValidationError" in text
    assert "KanbanToolHTTPError" in text
    assert "KanbanToolTransportError" in text
