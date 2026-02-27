"""
Clean text extractor for Scrapling pages.
Strips scripts, styles, SVGs, hidden elements — keeps everything visible.
Works regardless of whether a site uses <main>, semantic HTML, or divs-all-the-way-down.
"""

import re
from urllib.parse import urljoin, urlparse
from lxml import etree


# Tags that never contain visible content
JUNK_TAGS = frozenset([
    'script', 'style', 'noscript', 'svg', 'iframe', 'link', 'meta',
    'path', 'defs', 'clippath', 'symbol', 'use', 'canvas',
    'template', 'object', 'embed', 'applet', 'map', 'area',
])

# Social media domains
SOCIAL_DOMAINS = frozenset([
    'facebook.com', 'instagram.com', 'tiktok.com', 'twitter.com',
    'x.com', 'youtube.com', 'linkedin.com', 'yelp.com', 'realself.com',
    'pinterest.com', 'threads.net', 'snapchat.com',
])

# Skip these hrefs entirely
SKIP_PATTERNS = frozenset(['#', 'javascript:', 'void(0)', 'data:'])


def clean_text(page) -> str:
    """
    Extract clean visible text from a Scrapling page/response.
    Keeps nav, main, footer — strips scripts, styles, SVGs, hidden elements.
    """
    tree = page._root.__deepcopy__(None) if hasattr(page, '_root') else page.body._root.__deepcopy__(None)
    
    for tag in JUNK_TAGS:
        for el in tree.iter(tag):
            try:
                el.getparent().remove(el)
            except:
                pass
    
    for el in tree.iter():
        style = (el.get('style') or '').replace(' ', '').lower()
        if 'display:none' in style or 'visibility:hidden' in style:
            try:
                el.getparent().remove(el)
            except:
                pass
        if el.get('aria-hidden') == 'true':
            try:
                el.getparent().remove(el)
            except:
                pass
    
    for comment in tree.iter(etree.Comment):
        try:
            comment.getparent().remove(comment)
        except:
            pass
    
    text = etree.tostring(tree, method='text', encoding='unicode')
    
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' ?\n ?', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    
    return text


def extract_links(page, base_url: str = None) -> dict:
    """
    Extract ALL categorized links from a page.
    
    Returns:
        {
            'phones': ['239-898-2716', ...],
            'emails': ['info@example.com', ...],
            'social': [{'platform': 'facebook', 'url': '...'}, ...],
            'pages': [{'text': 'About Us', 'url': '...', 'category': 'about'}, ...],
        }
    """
    # Resolve base URL for relative links
    if not base_url:
        base_url = str(page.url) if hasattr(page, 'url') else ''
    
    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc.lower().replace('www.', '')
    
    links = {
        'phones': [],
        'emails': [],
        'social': [],
        'pages': [],
    }
    
    seen_urls = set()
    
    for a in page.css('a[href]'):
        href = (a.attrib.get('href') or '').strip()
        
        if not href or any(href.startswith(s) for s in SKIP_PATTERNS):
            continue
        
        # Get link text — try direct text, then nested text
        text = (a.text or '').strip()
        if not text:
            # Try to get text from child elements
            all_text = a.get_all_text(separator=' ', strip=True) if hasattr(a, 'get_all_text') else ''
            text = all_text.strip() if all_text else ''
        
        # Phone links
        if href.startswith('tel:'):
            phone = href.replace('tel:', '').replace('tel://', '').strip()
            if phone and phone not in links['phones']:
                links['phones'].append(phone)
            continue
        
        # Email links
        if href.startswith('mailto:'):
            email = href.replace('mailto:', '').split('?')[0].strip()
            if email and email not in links['emails']:
                links['emails'].append(email)
            continue
        
        # Resolve relative URLs
        full_url = urljoin(base_url, href) if not href.startswith('http') else href
        
        # Skip duplicates and anchors-only
        normalized = full_url.split('#')[0].rstrip('/')
        if normalized in seen_urls:
            continue
        seen_urls.add(normalized)
        
        parsed = urlparse(full_url)
        link_domain = parsed.netloc.lower().replace('www.', '')
        
        # Social media links
        if any(sd in link_domain for sd in SOCIAL_DOMAINS):
            platform = _detect_platform(link_domain)
            links['social'].append({
                'platform': platform,
                'url': full_url,
            })
            continue
        
        # Internal page links (same domain)
        if link_domain == base_domain or not link_domain:
            # Skip non-page resources
            path_lower = parsed.path.lower()
            if any(path_lower.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', 
                                                          '.svg', '.pdf', '.css', '.js', '.xml',
                                                          '.ico', '.woff', '.woff2', '.ttf')):
                continue
            
            # Categorize the page link
            category = _categorize_link(parsed.path, text)
            
            # Skip empty/useless links
            if not text or len(text) < 2 or len(text) > 120:
                # Still record it if it has a meaningful path
                if len(parsed.path) > 1:
                    text = text or _path_to_label(parsed.path)
                else:
                    continue
            
            links['pages'].append({
                'text': text,
                'url': full_url,
                'path': parsed.path,
                'category': category,
            })
    
    # Deduplicate pages by path, keep first occurrence (usually nav)
    seen_paths = set()
    unique_pages = []
    for p in links['pages']:
        if p['path'] not in seen_paths:
            seen_paths.add(p['path'])
            unique_pages.append(p)
    links['pages'] = unique_pages
    
    return links


def _detect_platform(domain: str) -> str:
    """Map domain to platform name."""
    platforms = {
        'facebook.com': 'facebook',
        'instagram.com': 'instagram',
        'tiktok.com': 'tiktok',
        'twitter.com': 'twitter',
        'x.com': 'twitter',
        'youtube.com': 'youtube',
        'linkedin.com': 'linkedin',
        'yelp.com': 'yelp',
        'realself.com': 'realself',
        'pinterest.com': 'pinterest',
        'threads.net': 'threads',
        'snapchat.com': 'snapchat',
    }
    for d, name in platforms.items():
        if d in domain:
            return name
    return 'other'


def _categorize_link(path: str, text: str) -> str:
    """Categorize a page link based on its path and text."""
    path_lower = path.lower()
    text_lower = text.lower() if text else ''
    combined = f"{path_lower} {text_lower}"
    
    categories = {
        'about': ('about', 'who-we-are', 'our-story', 'our-team', 'mission', 'history'),
        'contact': ('contact', 'get-in-touch', 'reach-us', 'location', 'directions', 'find-us'),
        'services': ('service', 'procedure', 'treatment', 'what-we-do', 'solution', 'offering'),
        'team': ('team', 'staff', 'doctor', 'surgeon', 'provider', 'specialist', 'meet-'),
        'pricing': ('pricing', 'price', 'cost', 'financing', 'payment', 'insurance', 'special', 'offer'),
        'portfolio': ('gallery', 'portfolio', 'before-after', 'photos', 'results', 'case'),
        'testimonials': ('testimonial', 'review', 'patient-stories', 'success-stories'),
        'blog': ('blog', 'news', 'article', 'post', 'resource', 'guide', 'learn', 'faq'),
        'careers': ('career', 'job', 'hiring', 'work-with-us', 'join'),
        'legal': ('privacy', 'terms', 'disclaimer', 'hipaa', 'policy', 'cookie', 'gdpr'),
        'booking': ('book', 'appointment', 'schedule', 'consult', 'request', 'reserve'),
    }
    
    for category, keywords in categories.items():
        if any(kw in combined for kw in keywords):
            return category
    
    return 'other'


def _path_to_label(path: str) -> str:
    """Convert a URL path to a readable label."""
    # /about-us/ → About Us
    name = path.strip('/').split('/')[-1]
    name = name.replace('-', ' ').replace('_', ' ')
    return name.title() if name else ''


if __name__ == '__main__':
    """Quick test — uses fetch.py for auto-fallback"""
    import sys
    from fetch import fetch_clean
    
    url = sys.argv[1] if len(sys.argv) > 1 else 'https://www.zuriplasticsurgery.com/'
    result = fetch_clean(url)
    links = result['links']
    
    print(f"URL: {url}")
    print(f"Fetcher: {result['fetcher']}")
    print(f"Text: {result['text_length']} chars (~{result['approx_tokens']} tokens)")
    print(f"Phones: {links['phones']}")
    print(f"Emails: {links['emails']}")
    
    print(f"\nSocial ({len(links['social'])}):")
    for s in links['social']:
        print(f"  {s['platform']:12} → {s['url']}")
    
    print(f"\nPages ({len(links['pages'])}):")
    by_cat = {}
    for p in links['pages']:
        by_cat.setdefault(p['category'], []).append(p)
    
    for cat, pages in sorted(by_cat.items()):
        print(f"\n  [{cat.upper()}]")
        for p in pages[:8]:
            print(f"    {p['text'][:50]:50} → {p['path']}")
        if len(pages) > 8:
            print(f"    ... +{len(pages)-8} more")
