#!/usr/bin/env python3
"""
Universal web page fetcher + clean text extractor.
Uses Scrapling for fetching, strips junk, returns clean visible text.

Usage:
    # CLI
    python3 fetch.py https://example.com
    python3 fetch.py https://example.com --json
    python3 fetch.py https://example.com --links

    # As module
    from fetch import fetch_clean
    result = fetch_clean('https://example.com')
    print(result['text'])
    print(result['links'])
"""

import json
import sys
import argparse
from scrapling.fetchers import Fetcher, StealthyFetcher
from extractor import clean_text, extract_links

# If basic fetch returns less than this, retry with browser
MIN_CONTENT_THRESHOLD = 500


def fetch_clean(url: str, timeout: int = 20, force_browser: bool = False) -> dict:
    """
    Fetch a URL and return clean text + extracted links.
    Auto-falls back to StealthyFetcher (headless browser) if basic fetch
    returns too little content (JS-rendered sites).
    
    Returns:
        {
            'url': str,
            'final_url': str,
            'status': int,
            'fetcher': str,
            'text': str,
            'text_length': int,
            'approx_tokens': int,
            'links': {phones, emails, social},
        }
    """
    fetcher_used = 'basic'
    
    if force_browser:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
        fetcher_used = 'stealth'
    else:
        page = Fetcher.get(url, stealthy_headers=True, timeout=timeout)
        
        # Check if we got enough content
        text_check = clean_text(page)
        if len(text_check) < MIN_CONTENT_THRESHOLD:
            # JS-rendered site — retry with headless browser
            try:
                page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
                fetcher_used = 'stealth (auto-fallback)'
            except Exception:
                # If stealth fails, return what we got from basic
                fetcher_used = 'basic (stealth fallback failed)'
    
    text = clean_text(page)
    final_url = str(page.url) if hasattr(page, 'url') else url
    links = extract_links(page, base_url=final_url)
    
    return {
        'url': url,
        'final_url': str(page.url) if hasattr(page, 'url') else url,
        'status': page.status,
        'fetcher': fetcher_used,
        'text': text,
        'text_length': len(text),
        'approx_tokens': len(text) // 4,
        'links': links,
    }


def main():
    parser = argparse.ArgumentParser(description='Fetch a URL and extract clean text')
    parser.add_argument('url', help='URL to fetch')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    parser.add_argument('--links', action='store_true', help='Show extracted links')
    parser.add_argument('--max-chars', type=int, default=0, help='Truncate text output')
    parser.add_argument('--timeout', type=int, default=20, help='Request timeout in seconds')
    parser.add_argument('--browser', action='store_true', help='Force headless browser (skip basic fetch)')
    args = parser.parse_args()
    
    result = fetch_clean(args.url, timeout=args.timeout, force_browser=args.browser)
    
    if args.json:
        if args.max_chars:
            result['text'] = result['text'][:args.max_chars]
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"URL: {result['url']}", file=sys.stderr)
        print(f"Status: {result['status']}", file=sys.stderr)
        print(f"Fetcher: {result['fetcher']}", file=sys.stderr)
        print(f"Length: {result['text_length']} chars (~{result['approx_tokens']} tokens)", file=sys.stderr)
        
        if args.links:
            links = result['links']
            if links['phones']: print(f"Phones: {', '.join(links['phones'])}", file=sys.stderr)
            if links['emails']: print(f"Emails: {', '.join(links['emails'])}", file=sys.stderr)
            if links['social']: print(f"Social: {', '.join(s['platform'] + ': ' + s['url'] for s in links['social'])}", file=sys.stderr)
            if links['pages']: print(f"Pages: {len(links['pages'])} internal links found", file=sys.stderr)
            print("---", file=sys.stderr)
        
        text = result['text']
        if args.max_chars:
            text = text[:args.max_chars]
        print(text)


if __name__ == '__main__':
    main()
