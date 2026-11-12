"""MCP server exposing turbopuffer ticket search to Claude Code (stdio transport).

Registered for this project in .mcp.json. Claude Code starts it as a local process.
Nothing here may print to stdout: stdout carries the MCP protocol.
"""

import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server import MCPServer  # noqa: E402

import search  # noqa: E402

INSTRUCTIONS = """\
You are a support agent answering questions from 100,000 past microsoft/vscode GitHub issues
(June 2023 to November 2026), stored in turbopuffer.

How to answer:
1. Call search_tickets. Hybrid mode is the default and usually best. Use bm25 mode for exact
   error strings, setting names or version numbers.
2. If results look off-topic, rephrase the query or add filters (state, state_reason, labels,
   dates) and search again. Common labels include macos, windows, linux, terminal, remote,
   freeze-slow-crash-leak, perf, extension-host, suggest, file-watcher, ssh.
3. Call get_ticket on the 2-4 most relevant hits to read the full text before answering.
4. Answer briefly and cite every claim with the ticket link, e.g. [#240002](url).
5. If the tickets don't actually answer the question, say so plainly. Never invent a fix
   that isn't in a ticket.
"""

mcp = MCPServer("vscode-ticket-search", instructions=INSTRUCTIONS)


@mcp.tool()
def search_tickets(
    query: str,
    mode: Literal["hybrid", "vector", "bm25"] = "hybrid",
    state: Literal["open", "closed"] | None = None,
    state_reason: Literal["completed", "not_planned", "duplicate"] | None = None,
    labels: list[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    limit: int = 10,
) -> dict:
    """Search past VS Code issues. Returns ranked tickets with title, state, labels, date, URL and a snippet.

    Args:
        query: Natural-language question or exact text to look for.
        mode: hybrid (vector + BM25 fused with RRF), vector (meaning) or bm25 (exact keywords).
        state: Only open or only closed tickets.
        state_reason: For closed tickets: completed (fixed), not_planned, or duplicate.
        labels: Tickets must have all of these labels, e.g. ["macos", "terminal"].
        created_after: Only tickets created on or after this date (YYYY-MM-DD).
        created_before: Only tickets created before this date (YYYY-MM-DD).
        limit: Number of results, 1-25.
    """
    results, ms = search.search(
        query, mode, max(1, min(limit, 25)),
        state=state, state_reason=state_reason, labels=labels,
        created_after=created_after, created_before=created_before,
    )
    return {"query": query, "mode": mode, "search_ms": round(ms), "count": len(results), "results": results}


@mcp.tool()
def get_ticket(ticket_id: int) -> dict:
    """Get one VS Code issue's full stored text (title + body, up to 8,000 chars) and metadata.

    Args:
        ticket_id: The GitHub issue number, e.g. 240002.
    """
    ticket = search.get_ticket(ticket_id)
    return ticket or {"error": f"Ticket #{ticket_id} is not in the index."}


if __name__ == "__main__":
    mcp.run("stdio")
