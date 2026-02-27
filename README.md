# Scrapling Kit

Universal web scraping + AI extraction toolkit. Fetches any website, strips junk, extracts structured data with LLMs. Includes web search with deep extraction.

## Setup

```bash
source /root/tools/scrapling-env/bin/activate
cd /root/tools/scrapling-kit
```

## Configuration

Edit `.env`:
```env
OPENROUTER_API_KEY=sk-or-...
BRAVE_API_KEY=BSA...
DEFAULT_MODEL=auto-free
```

## Usage

### Single URL Extraction

```bash
# Default extraction (all business info, markdown output)
python3 extract.py https://example.com

# Custom query + JSON output
python3 extract.py https://example.com -q "Extract contact info and services" -f json

# Use a specific model
python3 extract.py https://example.com -m deepseek-v3 -f json

# Force headless browser for JS-rendered sites
python3 extract.py https://example.com --browser

# Raw output only (for piping)
python3 extract.py https://example.com --raw

# Append metadata
python3 extract.py https://example.com --meta
```

### Web Search

```bash
# Search only — returns markdown table with titles, sources, links
python3 extract.py --search "cuba latest news"

# Filter by freshness
python3 extract.py --search "cuba latest news" --freshness pd   # pd=day, pw=week, pm=month, py=year

# Deep extract top N results (sequential)
python3 extract.py --search "cuba latest news" --deep 5 -m deepseek-v3

# Deep extract in parallel (faster)
python3 extract.py --search "cuba latest news" --deep 5 -m deepseek-v3 --parallel

# Custom extraction query for deep mode
python3 extract.py --search "cuba latest news" --deep 3 -q "Extract key facts, quotes, and timeline" -m deepseek-v3

# Control result count
python3 extract.py --search "plastic surgery miami" --count 20 --deep 10 --parallel
```

### List Models

```bash
python3 extract.py --models
```

### Python API

```python
from extract import extract, search_extract, search, list_models

# Single URL
result = extract('https://example.com', query='Extract contact info', format='json', model='deepseek-v3')
print(result['result'])       # LLM extraction
print(result['cost_estimate']) # Cost in USD

# Search only
results = search('cuba latest news', count=10, freshness='pd')
for r in results:
    print(f"{r['title']} — {r['source']} — {r['url']}")

# Search + deep extract
result = search_extract(
    query='cuba latest news',
    deep=5,
    model='deepseek-v3',
    parallel=True,
    extraction_query='Extract key facts and quotes',
)
print(result['markdown'])      # Full markdown document
print(result['totals']['cost']) # Total cost
```

## How It Works

### Fetch Pipeline
1. **Basic HTTP fetch** with spoofed browser headers (~1s)
2. **Content check** — if <500 chars, site is JS-rendered
3. **Auto-fallback** to StealthyFetcher headless browser (~5s)
4. **Junk stripping** — removes scripts, styles, SVGs, hidden elements
5. **Text extraction** — everything visible: nav, content, footer
6. **Link extraction** — phones, emails, social, internal pages (auto-categorized)

### Search + Deep Extract Pipeline
1. **Brave Search API** → N results with titles, URLs, descriptions
2. **For each deep result:** fetch → clean → send to LLM
3. **Output:** combined markdown with summary table + per-article extractions + metadata

## Models

### Free Tier
| Alias | Model | Context |
|---|---|---|
| `auto-free` | OpenRouter Free Router | 200K |
| `hermes-405b` | Hermes 3 405B | 131K |
| `qwen3-80b` | Qwen3 Next 80B | 262K |
| `llama-70b` | Llama 3.3 70B | 128K |
| `gemma-27b` | Gemma 3 27B | 131K |

### Paid Tier
| Alias | Model | Context | $/MTok In |
|---|---|---|---|
| `gpt-5.2` | GPT-5.2 Chat | 128K | $1.75 |
| `gpt-4.1-mini` | GPT-4.1 Mini | 128K | $0.40 |
| `gemini-3.1-pro` | Gemini 3.1 Pro | 1M | $2.00 |
| `gemini-3-flash` | Gemini 3 Flash | 1M | $0.50 |
| `sonnet` | Claude Sonnet 4.6 | 1M | $3.00 |
| `qwen3.5-397b` | Qwen3.5 397B | 262K | $0.55 |
| `deepseek-v3` | DeepSeek V3 | 196K | $0.27 |

## Files

| File | Lines | Purpose |
|---|---|---|
| `extract.py` | ~450 | AI extraction + search + CLI. Main entry point. |
| `fetch.py` | ~120 | URL fetcher with auto browser fallback. |
| `extractor.py` | ~275 | Engine: junk stripping, text extraction, link categorization. |
| `.env` | — | API keys and default model config. |

## Cost Examples

| Task | Model | Tokens | Cost | Time |
|---|---|---|---|---|
| Single clinic extraction | deepseek-v3 | 3K→640 | $0.002 | 15s |
| News article extraction | deepseek-v3 | 12K→1K | $0.005 | 28s |
| 3 articles deep extract (seq) | deepseek-v3 | 4K→1.4K | $0.003 | 90s |
| 3 articles deep extract (parallel) | deepseek-v3 | 4K→1.3K | $0.003 | 48s |
| Single clinic extraction | auto-free | 3K→2K | FREE | 16s |
