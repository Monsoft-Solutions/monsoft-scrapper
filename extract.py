#!/usr/bin/env python3
"""
AI-powered web data extractor with search capabilities.
Fetches any website, cleans it, sends to an LLM for structured extraction.
Can also search the web and extract from multiple results.

Usage:
    # Extract from a single URL
    python3 extract.py https://example.com
    python3 extract.py https://example.com -q "Extract contact info" -f json -m deepseek-v3

    # Search the web
    python3 extract.py --search "cuba latest news"

    # Search + deep extract top N results
    python3 extract.py --search "cuba latest news" --deep 5 -m deepseek-v3
    python3 extract.py --search "cuba latest news" --deep 3 --parallel -m gpt-4.1-mini

    # List models
    python3 extract.py --models
"""

import json
import os
import sys
import argparse
import time
import concurrent.futures
from pathlib import Path
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from fetch import fetch_clean

# Load .env from script directory (override=True to take precedence over system env)
load_dotenv(Path(__file__).parent / '.env', override=True)

OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
BRAVE_API_KEY = os.getenv('BRAVE_API_KEY', '')
BRAVE_URL = 'https://api.search.brave.com/res/v1/web/search'

# ─── Model Registry ──────────────────────────────────────────────────────────

MODELS = {
    # Free tier
    'auto-free': {
        'id': 'openrouter/free',
        'name': 'Free Models Router',
        'context': 200_000,
        'cost_input': 0,
        'cost_output': 0,
        'tier': 'free',
    },
    'hermes-405b': {
        'id': 'nousresearch/hermes-3-llama-3.1-405b:free',
        'name': 'Hermes 3 405B',
        'context': 131_072,
        'cost_input': 0,
        'cost_output': 0,
        'tier': 'free',
    },
    'qwen3-80b': {
        'id': 'qwen/qwen3-next-80b-a3b-instruct:free',
        'name': 'Qwen3 Next 80B',
        'context': 262_144,
        'cost_input': 0,
        'cost_output': 0,
        'tier': 'free',
    },
    'llama-70b': {
        'id': 'meta-llama/llama-3.3-70b-instruct:free',
        'name': 'Llama 3.3 70B',
        'context': 128_000,
        'cost_input': 0,
        'cost_output': 0,
        'tier': 'free',
    },
    'gemma-27b': {
        'id': 'google/gemma-3-27b-it:free',
        'name': 'Gemma 3 27B',
        'context': 131_072,
        'cost_input': 0,
        'cost_output': 0,
        'tier': 'free',
    },

    # Paid tier
    'gpt-5.2': {
        'id': 'openai/gpt-5.2-chat',
        'name': 'GPT-5.2 Chat',
        'context': 128_000,
        'cost_input': 1.75,
        'cost_output': 7.00,
        'tier': 'paid',
    },
    'gpt-4.1-mini': {
        'id': 'openai/gpt-4.1-mini',
        'name': 'GPT-4.1 Mini',
        'context': 128_000,
        'cost_input': 0.40,
        'cost_output': 1.60,
        'tier': 'paid',
    },
    'gemini-3.1-pro': {
        'id': 'google/gemini-3.1-pro-preview',
        'name': 'Gemini 3.1 Pro',
        'context': 1_048_576,
        'cost_input': 2.00,
        'cost_output': 8.00,
        'tier': 'paid',
    },
    'gemini-3-flash': {
        'id': 'google/gemini-3-flash-preview',
        'name': 'Gemini 3 Flash',
        'context': 1_048_576,
        'cost_input': 0.50,
        'cost_output': 2.00,
        'tier': 'paid',
    },
    'sonnet': {
        'id': 'anthropic/claude-sonnet-4.6',
        'name': 'Claude Sonnet 4.6',
        'context': 1_000_000,
        'cost_input': 3.00,
        'cost_output': 15.00,
        'tier': 'paid',
    },
    'qwen3.5-397b': {
        'id': 'qwen/qwen3.5-397b-a17b',
        'name': 'Qwen3.5 397B',
        'context': 262_144,
        'cost_input': 0.55,
        'cost_output': 2.19,
        'tier': 'paid',
    },
    'deepseek-v3': {
        'id': 'deepseek/deepseek-chat-v3-0324',
        'name': 'DeepSeek V3',
        'context': 196_608,
        'cost_input': 0.27,
        'cost_output': 1.10,
        'tier': 'paid',
    },
}

DEFAULT_MODEL = os.getenv('DEFAULT_MODEL', 'auto-free')

# ─── Prompt Templates ────────────────────────────────────────────────────────

DEFAULT_QUERY = """Extract all business information from this website, including:
- Business name and type
- Phone number(s), email(s), address
- Services or products offered
- Team members / staff (names, titles, credentials)
- Pricing or price ranges if mentioned
- Hours of operation
- Unique selling points / differentiators
- Languages spoken
- Areas served

Only include information that is explicitly present in the content. Do not guess or fabricate."""

DEFAULT_SEARCH_QUERY = """Extract the key information from this article/page:
- Main topic / headline
- Key facts and data points
- Important quotes with attribution
- People mentioned (names and roles)
- Dates and timeline of events
- Background context

Only include information that is explicitly present in the content. Do not guess or fabricate."""

FORMAT_INSTRUCTIONS = {
    'json': 'Return your response as a valid JSON object. Use null for missing fields. Do not wrap in markdown code blocks.',
    'markdown': 'Return your response as clean, well-structured markdown with headers and bullet points.',
    'text': 'Return your response as plain text, clearly organized with labels.',
}


def build_prompt(text: str, links: dict, query: str, fmt: str) -> str:
    """Build the extraction prompt from page content + user query."""
    links_section = ''
    if links.get('phones'):
        links_section += f"\nPhone links found: {', '.join(links['phones'])}"
    if links.get('emails'):
        links_section += f"\nEmail links found: {', '.join(links['emails'])}"
    if links.get('social'):
        social_str = ', '.join(f"{s['platform']}: {s['url']}" for s in links['social'])
        links_section += f"\nSocial media: {social_str}"
    if links.get('pages'):
        by_cat = {}
        for p in links['pages']:
            by_cat.setdefault(p['category'], []).append(p)
        pages_parts = []
        for cat, pages in sorted(by_cat.items()):
            names = ', '.join(p['text'][:40] for p in pages[:5])
            suffix = f' (+{len(pages)-5} more)' if len(pages) > 5 else ''
            pages_parts.append(f"  {cat}: {names}{suffix}")
        links_section += f"\n\nSite pages found:\n" + '\n'.join(pages_parts)

    format_instruction = FORMAT_INSTRUCTIONS.get(fmt, FORMAT_INSTRUCTIONS['markdown'])

    prompt = f"""You are a data extraction assistant. Extract the requested information from the website content below.

{query}

{format_instruction}

--- EXTRACTED LINKS ---
{links_section.strip() if links_section.strip() else 'No links extracted.'}

--- WEBSITE CONTENT ---
{text}"""

    return prompt


# ─── LLM Caller ──────────────────────────────────────────────────────────────

def call_llm(prompt: str, model_alias: str) -> dict:
    """Call OpenRouter API and return response + usage stats."""
    if not OPENROUTER_API_KEY:
        raise ValueError('OPENROUTER_API_KEY not set. Check .env file.')

    model_info = get_model(model_alias)
    model_id = model_info['id']

    # Check context fit
    approx_tokens = len(prompt) // 4
    overhead = 2000
    max_input = model_info['context'] - overhead

    if approx_tokens > max_input:
        max_chars = max_input * 4
        print(f"⚠️  Content truncated: ~{approx_tokens} tokens exceeds {model_info['context']} context.", file=sys.stderr)
        marker = '--- WEBSITE CONTENT ---\n'
        idx = prompt.find(marker)
        if idx > 0:
            prefix = prompt[:idx + len(marker)]
            remaining_chars = max_chars - len(prefix) // 4 * 4
            prompt = prefix + prompt[idx + len(marker):][:remaining_chars] + '\n\n[Content truncated]'

    headers = {
        'Authorization': f'Bearer {OPENROUTER_API_KEY}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://openclaw.ai',
        'X-Title': 'Scrapling Kit Extractor',
    }

    payload = {
        'model': model_id,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.1,
    }

    start = time.time()
    response = httpx.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
    latency_ms = int((time.time() - start) * 1000)

    if response.status_code != 200:
        error_text = response.text[:500]
        raise RuntimeError(f'OpenRouter API error ({response.status_code}): {error_text}')

    data = response.json()
    content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
    usage = data.get('usage', {})
    tokens_input = usage.get('prompt_tokens', 0)
    tokens_output = usage.get('completion_tokens', 0)
    cost = (tokens_input * model_info['cost_input'] / 1_000_000) + \
           (tokens_output * model_info['cost_output'] / 1_000_000)

    return {
        'content': content,
        'model': model_alias,
        'model_id': model_id,
        'tokens_input': tokens_input,
        'tokens_output': tokens_output,
        'cost_estimate': round(cost, 6),
        'latency_ms': latency_ms,
    }


# ─── Search ───────────────────────────────────────────────────────────────────

def search(query: str, count: int = 10, freshness: str = None) -> list[dict]:
    """
    Search the web via Brave Search API.

    Args:
        query: Search query
        count: Number of results (1-20)
        freshness: Filter by time — 'pd' (day), 'pw' (week), 'pm' (month), 'py' (year)

    Returns:
        List of {title, url, description, source, published}
    """
    if not BRAVE_API_KEY:
        raise ValueError('BRAVE_API_KEY not set. Check .env file.')

    params = {
        'q': query,
        'count': min(count, 20),
    }
    if freshness:
        params['freshness'] = freshness

    headers = {
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip',
        'X-Subscription-Token': BRAVE_API_KEY,
    }

    response = httpx.get(BRAVE_URL, params=params, headers=headers, timeout=15)

    if response.status_code != 200:
        raise RuntimeError(f'Brave Search API error ({response.status_code}): {response.text[:300]}')

    data = response.json()
    results = []

    for item in data.get('web', {}).get('results', []):
        results.append({
            'title': item.get('title', ''),
            'url': item.get('url', ''),
            'description': item.get('description', ''),
            'source': item.get('meta_url', {}).get('hostname', '') or _domain_from_url(item.get('url', '')),
            'published': item.get('age', ''),
        })

    return results


def _domain_from_url(url: str) -> str:
    """Extract domain from URL."""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.replace('www.', '')
    except:
        return ''


def _extract_single(idx: int, result: dict, query: str, fmt: str, model: str) -> dict:
    """Extract from a single search result. Used by both sequential and parallel modes."""
    title = result['title']
    url = result['url']
    source = result['source']

    out = {
        'index': idx,
        'title': title,
        'url': url,
        'source': source,
        'published': result.get('published', ''),
        'status': 'success',
        'extraction': '',
        'tokens_input': 0,
        'tokens_output': 0,
        'cost_estimate': 0.0,
        'latency_ms': 0,
        'text_length': 0,
        'fetcher': '',
    }

    try:
        print(f"  [{idx}] Fetching {source}...", file=sys.stderr)
        page_data = fetch_clean(url)
        out['text_length'] = page_data['text_length']
        out['fetcher'] = page_data['fetcher']

        if page_data['text_length'] < 100:
            out['status'] = 'empty'
            out['extraction'] = f'*Could not extract content from {url}*'
            return out

        prompt = build_prompt(page_data['text'], page_data['links'], query, fmt)
        llm_result = call_llm(prompt, model)

        out['extraction'] = llm_result['content']
        out['tokens_input'] = llm_result['tokens_input']
        out['tokens_output'] = llm_result['tokens_output']
        out['cost_estimate'] = llm_result['cost_estimate']
        out['latency_ms'] = llm_result['latency_ms']

        cost_str = 'FREE' if llm_result['cost_estimate'] == 0 else f"${llm_result['cost_estimate']:.4f}"
        print(f"  [{idx}] ✅ {page_data['text_length']} chars → "
              f"{llm_result['tokens_input']}→{llm_result['tokens_output']} tok | "
              f"{cost_str} | {llm_result['latency_ms']}ms", file=sys.stderr)

    except Exception as e:
        out['status'] = 'error'
        out['extraction'] = f'*Error extracting from {url}: {e}*'
        print(f"  [{idx}] ❌ {e}", file=sys.stderr)

    return out


def search_extract(query: str, count: int = 10, deep: int = 0,
                   extraction_query: str = None, fmt: str = 'markdown',
                   model: str = None, parallel: bool = False,
                   freshness: str = None) -> dict:
    """
    Search the web and optionally extract content from results.

    Args:
        query: Search query
        count: Number of search results
        deep: Number of results to deep-extract (0 = search only)
        extraction_query: What to extract from each page
        fmt: Output format for extractions
        model: Model alias
        parallel: Run deep extractions in parallel
        freshness: Time filter — 'pd', 'pw', 'pm', 'py'

    Returns:
        {
            'query': str,
            'results': list,
            'extractions': list (if deep > 0),
            'markdown': str,
            'totals': {tokens_input, tokens_output, cost, time_ms},
        }
    """
    model = model or DEFAULT_MODEL
    extraction_query = extraction_query or DEFAULT_SEARCH_QUERY

    # 1. Search
    print(f"🔍 Searching: \"{query}\"...", file=sys.stderr)
    results = search(query, count=count, freshness=freshness)
    print(f"📋 {len(results)} results found", file=sys.stderr)

    if not results:
        return {
            'query': query,
            'results': [],
            'extractions': [],
            'markdown': f'# Search: "{query}"\n\nNo results found.',
            'totals': {'tokens_input': 0, 'tokens_output': 0, 'cost': 0, 'time_ms': 0},
        }

    # 2. Build results table markdown
    md_parts = []
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    md_parts.append(f'# Search: "{query}"')
    md_parts.append(f'*{len(results)} results | {now}*\n')

    md_parts.append('## Sources\n')
    md_parts.append('| # | Title | Source | Published |')
    md_parts.append('|---|---|---|---|')
    for i, r in enumerate(results, 1):
        title = r['title'][:80]
        md_parts.append(f"| {i} | [{title}]({r['url']}) | {r['source']} | {r['published']} |")

    # 3. Deep extraction
    extractions = []
    totals = {'tokens_input': 0, 'tokens_output': 0, 'cost': 0.0, 'time_ms': 0}

    if deep > 0:
        to_extract = results[:deep]
        model_info = get_model(model)
        tier_icon = '🆓' if model_info['tier'] == 'free' else '💰'
        mode_str = 'parallel' if parallel else 'sequential'
        print(f"\n🤖 Deep extracting {len(to_extract)} articles ({mode_str}) "
              f"with {model_info['name']} {tier_icon}...", file=sys.stderr)

        start_total = time.time()

        if parallel:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(deep, 5)) as executor:
                futures = {
                    executor.submit(
                        _extract_single, i + 1, r, extraction_query, fmt, model
                    ): i for i, r in enumerate(to_extract)
                }
                for future in concurrent.futures.as_completed(futures):
                    extractions.append(future.result())
            # Sort by original index
            extractions.sort(key=lambda x: x['index'])
        else:
            for i, r in enumerate(to_extract):
                ext = _extract_single(i + 1, r, extraction_query, fmt, model)
                extractions.append(ext)

        totals['time_ms'] = int((time.time() - start_total) * 1000)
        totals['tokens_input'] = sum(e['tokens_input'] for e in extractions)
        totals['tokens_output'] = sum(e['tokens_output'] for e in extractions)
        totals['cost'] = round(sum(e['cost_estimate'] for e in extractions), 6)

        # Add extractions to markdown
        md_parts.append(f'\n---\n')
        md_parts.append(f'## Extracted Content\n')
        md_parts.append(f'*{len(extractions)} articles extracted | '
                        f'Model: {model_info["name"]} {tier_icon} | '
                        f'Mode: {mode_str}*\n')

        for ext in extractions:
            md_parts.append(f'---\n')
            md_parts.append(f'### {ext["index"]}. {ext["title"]}')
            md_parts.append(f'*Source: {ext["source"]} | {ext["published"]} | '
                            f'[Link]({ext["url"]})*\n')
            md_parts.append(ext['extraction'])
            md_parts.append('')

        # Totals
        cost_str = 'FREE' if totals['cost'] == 0 else f"${totals['cost']:.4f}"
        md_parts.append(f'\n---\n')
        md_parts.append(f'### Metadata')
        md_parts.append(f'- **Total tokens:** {totals["tokens_input"]:,} in / {totals["tokens_output"]:,} out')
        md_parts.append(f'- **Total cost:** {cost_str}')
        md_parts.append(f'- **Total time:** {totals["time_ms"] / 1000:.1f}s')
        md_parts.append(f'- **Model:** {model_info["name"]} (`{model}`)')

        # Print summary
        print(f"\n📊 Total: {totals['tokens_input']:,}→{totals['tokens_output']:,} tokens | "
              f"Cost: {cost_str} | Time: {totals['time_ms'] / 1000:.1f}s", file=sys.stderr)

    markdown = '\n'.join(md_parts)

    return {
        'query': query,
        'results': results,
        'extractions': extractions,
        'markdown': markdown,
        'totals': totals,
    }


# ─── Single URL Extract ──────────────────────────────────────────────────────

def get_model(alias: str) -> dict:
    """Resolve model alias to model info."""
    if alias in MODELS:
        return MODELS[alias]
    for a, info in MODELS.items():
        if info['id'] == alias:
            return info
    raise ValueError(f"Unknown model: '{alias}'. Run with --models to see available options.")


def list_models() -> list[dict]:
    """Return list of available models."""
    return [
        {'alias': a, **{k: v for k, v in info.items()}}
        for a, info in MODELS.items()
    ]


def extract(url: str, query: str = None, format: str = 'markdown',
            model: str = None, force_browser: bool = False) -> dict:
    """Fetch a URL and extract structured data using an LLM."""
    model = model or DEFAULT_MODEL
    query = query or DEFAULT_QUERY

    print(f"🌐 Fetching {url}...", file=sys.stderr)
    page_data = fetch_clean(url, force_browser=force_browser)

    if not page_data['text'] or page_data['text_length'] < 100:
        raise RuntimeError(f"Failed to extract content from {url} (got {page_data['text_length']} chars)")

    print(f"📄 {page_data['text_length']} chars (~{page_data['approx_tokens']} tokens) "
          f"via {page_data['fetcher']}", file=sys.stderr)

    prompt = build_prompt(page_data['text'], page_data['links'], query, format)

    model_info = get_model(model)
    tier_icon = '🆓' if model_info['tier'] == 'free' else '💰'
    print(f"🤖 Sending to {model_info['name']} {tier_icon}...", file=sys.stderr)

    llm_result = call_llm(prompt, model)

    cost_str = 'FREE' if llm_result['cost_estimate'] == 0 else f"${llm_result['cost_estimate']:.6f}"
    print(f"✅ Done in {llm_result['latency_ms']}ms | "
          f"{llm_result['tokens_input']}→{llm_result['tokens_output']} tokens | "
          f"Cost: {cost_str}", file=sys.stderr)

    return {
        'url': url,
        'final_url': page_data['final_url'],
        'fetcher': page_data['fetcher'],
        'text_length': page_data['text_length'],
        'text_tokens': page_data['approx_tokens'],
        'model': llm_result['model'],
        'model_id': llm_result['model_id'],
        'tokens_input': llm_result['tokens_input'],
        'tokens_output': llm_result['tokens_output'],
        'cost_estimate': llm_result['cost_estimate'],
        'latency_ms': llm_result['latency_ms'],
        'links': page_data['links'],
        'result': llm_result['content'],
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────

def print_models():
    """Print available models table."""
    print("\n📋 Available Models\n")
    print("  FREE TIER:")
    print(f"  {'Alias':<16} {'Model':<50} {'Context':>10}")
    print(f"  {'─'*16} {'─'*50} {'─'*10}")
    for alias, info in MODELS.items():
        if info['tier'] == 'free':
            ctx = f"{info['context']:,}"
            default = ' ← default' if alias == DEFAULT_MODEL else ''
            print(f"  {alias:<16} {info['name']:<50} {ctx:>10}{default}")

    print(f"\n  PAID TIER:")
    print(f"  {'Alias':<16} {'Model':<50} {'Context':>10} {'$/MTok In':>10} {'$/MTok Out':>11}")
    print(f"  {'─'*16} {'─'*50} {'─'*10} {'─'*10} {'─'*11}")
    for alias, info in MODELS.items():
        if info['tier'] == 'paid':
            ctx = f"{info['context']:,}"
            default = ' ← default' if alias == DEFAULT_MODEL else ''
            print(f"  {alias:<16} {info['name']:<50} {ctx:>10} "
                  f"{info['cost_input']:>10.2f} {info['cost_output']:>11.2f}{default}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description='AI-powered web data extractor',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python3 extract.py https://example.com
  python3 extract.py https://example.com -q "Extract contact info" -f json
  python3 extract.py --search "cuba latest news"
  python3 extract.py --search "cuba latest news" --deep 5 -m deepseek-v3
  python3 extract.py --search "cuba latest news" --deep 3 --parallel -m gpt-4.1-mini
  python3 extract.py --models""",
    )
    parser.add_argument('url', nargs='?', help='URL to extract data from')
    parser.add_argument('-q', '--query', help='What to extract (default: all business info / article key facts)')
    parser.add_argument('-f', '--format', choices=['json', 'markdown', 'text'], default='markdown',
                        help='Output format (default: markdown)')
    parser.add_argument('-m', '--model', default=None, help=f'Model alias (default: {DEFAULT_MODEL})')
    parser.add_argument('--models', action='store_true', help='List available models')
    parser.add_argument('--raw', action='store_true', help='Print only LLM output (for piping)')
    parser.add_argument('--browser', action='store_true', help='Force headless browser fetch')
    parser.add_argument('--meta', action='store_true', help='Append extraction metadata as JSON')

    # Search options
    parser.add_argument('-s', '--search', metavar='QUERY', help='Search the web instead of fetching a URL')
    parser.add_argument('--deep', type=int, default=0, metavar='N',
                        help='Deep extract top N search results (default: 0 = search only)')
    parser.add_argument('--sequential', action='store_true', help='Run deep extractions sequentially (default: parallel)')
    parser.add_argument('--freshness', choices=['pd', 'pw', 'pm', 'py'],
                        help='Filter search results by time (pd=day, pw=week, pm=month, py=year)')
    parser.add_argument('--count', type=int, default=10, help='Number of search results (default: 10)')

    args = parser.parse_args()

    if args.models:
        print_models()
        return

    # Search mode
    if args.search:
        try:
            result = search_extract(
                query=args.search,
                count=args.count,
                deep=args.deep,
                extraction_query=args.query,
                fmt=args.format,
                model=args.model,
                parallel=not args.sequential,
                freshness=args.freshness,
            )
        except Exception as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)

        if args.raw and args.deep > 0:
            # Raw mode: just the extractions
            for ext in result['extractions']:
                print(f"\n--- {ext['title']} ({ext['source']}) ---\n")
                print(ext['extraction'])
        else:
            print(result['markdown'])
        return

    # Single URL mode
    if not args.url:
        parser.print_help()
        return

    try:
        result = extract(
            url=args.url,
            query=args.query,
            format=args.format,
            model=args.model,
            force_browser=args.browser,
        )
    except Exception as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    if args.raw:
        print(result['result'])
    else:
        print(result['result'])
        if args.meta:
            meta = {k: v for k, v in result.items() if k != 'result' and k != 'links'}
            print(f"\n---\n{json.dumps(meta, indent=2)}")


if __name__ == '__main__':
    main()
