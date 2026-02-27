#!/usr/bin/env python3
"""
MCP server for Monsoft Scrapper.

Exposes the scraping toolkit as MCP tools for AI clients
(Claude Desktop, Cursor, Windsurf, etc.).

Transports:
    stdio           — local clients launch this directly
    sse             — remote clients connect over HTTP
    streamable-http — newer MCP transport over HTTP

Usage:
    # Local (stdio) — default, used by Claude Desktop / Cursor
    python3 mcp_server.py

    # Remote (SSE) — runs HTTP server
    python3 mcp_server.py --sse --port 8808

    # Remote (Streamable HTTP)
    python3 mcp_server.py --streamable-http --port 8808

    # Bind to all interfaces (for remote access)
    python3 mcp_server.py --sse --host 0.0.0.0 --port 8808

Claude Desktop config (stdio):
    {
        "mcpServers": {
            "monsoft-scrapper": {
                "command": "/root/tools/scrapling-env/bin/python3",
                "args": ["/root/tools/scrapling-kit/mcp_server.py"]
            }
        }
    }

Remote client config (SSE):
    {
        "mcpServers": {
            "monsoft-scrapper": {
                "url": "https://your-server.com:8808/sse"
            }
        }
    }
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Load env before importing project modules
load_dotenv(Path(__file__).parent / ".env", override=True)

from mcp.server.fastmcp import FastMCP

from extract import (
    extract,
    search,
    search_extract,
    list_models,
    MODELS,
    DEFAULT_MODEL,
)

# ─── Server Setup ─────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="monsoft-scrapper",
    instructions=(
        "Web scraping and AI-powered data extraction toolkit. "
        "Use 'extract' to pull structured data from any URL, "
        "'search' to find web pages, 'search_extract' for search + deep extraction, "
        "and 'list_models' to see available LLM models."
    ),
)


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def tool_extract(
    url: str,
    query: str = "",
    format: str = "markdown",
    model: str = "",
    force_browser: bool = False,
) -> str:
    """
    Extract structured data from a URL using an LLM.

    Fetches the webpage, cleans the HTML, and sends it to an LLM
    for intelligent extraction based on your query.

    Args:
        url: The URL to extract data from.
        query: What to extract (e.g., "Extract contact info", "List all products").
              If empty, extracts all business information.
        format: Output format — "json", "markdown", or "text".
        model: LLM model alias (e.g., "deepseek-v3", "gpt-4.1-mini", "auto-free").
               Run list_models to see options. Defaults to auto-free.
        force_browser: Force headless browser fetch (for JS-heavy sites).

    Returns:
        JSON string with extraction result, metadata (tokens, cost, latency).
    """
    try:
        result = extract(
            url=url,
            query=query or None,
            format=format,
            model=model or None,
            force_browser=force_browser,
        )
        result.pop("links", None)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "status": "error"})


@mcp.tool()
def tool_search(
    query: str,
    count: int = 10,
    freshness: str = "",
) -> str:
    """
    Search the web via Brave Search API.

    Returns a list of search results with titles, URLs, and descriptions.
    Use search_extract for search + automatic content extraction.

    Args:
        query: Search query string.
        count: Number of results to return (1-20).
        freshness: Filter by time — "pd" (past day), "pw" (past week),
                   "pm" (past month), "py" (past year). Empty for no filter.

    Returns:
        JSON array of search results with title, url, description, source, published.
    """
    try:
        results = search(
            query=query,
            count=count,
            freshness=freshness or None,
        )
        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "status": "error"})


@mcp.tool()
def tool_search_extract(
    query: str,
    count: int = 10,
    deep: int = 3,
    extraction_query: str = "",
    format: str = "markdown",
    model: str = "",
    parallel: bool = True,
    freshness: str = "",
) -> str:
    """
    Search the web and extract content from top results.

    Combines web search with deep extraction: searches for your query,
    then fetches and extracts structured data from the top N results.

    Args:
        query: Search query string.
        count: Number of search results to fetch (1-20).
        deep: Number of top results to deep-extract (0 = search only).
        extraction_query: What to extract from each page. If empty, extracts
                         key facts, quotes, people, dates, and context.
        format: Output format — "json", "markdown", or "text".
        model: LLM model alias. Defaults to auto-free.
        parallel: Run extractions in parallel (faster) or sequential.
        freshness: Time filter — "pd", "pw", "pm", "py", or empty.

    Returns:
        JSON string with search results, extractions, markdown summary, and usage totals.
    """
    try:
        result = search_extract(
            query=query,
            count=count,
            deep=deep,
            extraction_query=extraction_query or None,
            fmt=format,
            model=model or None,
            parallel=parallel,
            freshness=freshness or None,
        )
        output = {
            "query": result["query"],
            "results_count": len(result["results"]),
            "results": result["results"],
            "extractions": [
                {
                    "title": e["title"],
                    "url": e["url"],
                    "source": e["source"],
                    "status": e["status"],
                    "extraction": e["extraction"],
                }
                for e in result.get("extractions", [])
            ],
            "totals": result["totals"],
            "markdown": result["markdown"],
        }
        return json.dumps(output, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "status": "error"})


@mcp.tool()
def tool_list_models() -> str:
    """
    List all available LLM models.

    Returns free and paid models with their aliases, names, context windows,
    and pricing. Use the alias when specifying a model in other tools.

    Returns:
        JSON string with "free" and "paid" model lists, plus the default model alias.
    """
    free = []
    paid = []
    for alias, info in MODELS.items():
        entry = {
            "alias": alias,
            "name": info["name"],
            "context": info["context"],
            "cost_input_per_mtok": info["cost_input"],
            "cost_output_per_mtok": info["cost_output"],
        }
        if info["tier"] == "free":
            free.append(entry)
        else:
            paid.append(entry)

    return json.dumps({
        "default_model": DEFAULT_MODEL,
        "free": free,
        "paid": paid,
    }, ensure_ascii=False)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MCP server for Monsoft Scrapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python3 mcp_server.py                          # stdio (local)
  python3 mcp_server.py --sse --port 8808        # SSE (remote)
  python3 mcp_server.py --streamable-http        # Streamable HTTP
  python3 mcp_server.py --sse --host 0.0.0.0     # Bind all interfaces""",
    )
    parser.add_argument(
        "--sse", action="store_true",
        help="Run with SSE transport (HTTP server for remote clients)",
    )
    parser.add_argument(
        "--streamable-http", action="store_true",
        help="Run with Streamable HTTP transport",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1, use 0.0.0.0 for remote)",
    )
    parser.add_argument(
        "--port", type=int, default=8808,
        help="Port for SSE/HTTP transport (default: 8808)",
    )

    args = parser.parse_args()

    # Configure host/port on the server settings
    mcp.settings.host = args.host
    mcp.settings.port = args.port

    if args.sse:
        print(f"🚀 MCP server starting (SSE) on {args.host}:{args.port}", file=sys.stderr)
        print(f"   SSE endpoint: http://{args.host}:{args.port}/sse", file=sys.stderr)
        mcp.run(transport="sse")
    elif args.streamable_http:
        print(f"🚀 MCP server starting (Streamable HTTP) on {args.host}:{args.port}", file=sys.stderr)
        print(f"   Endpoint: http://{args.host}:{args.port}/mcp", file=sys.stderr)
        mcp.run(transport="streamable-http")
    else:
        # stdio — no output to stderr except from tools
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
