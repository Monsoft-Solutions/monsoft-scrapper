# Scrapling Kit

Universal web scraping + AI extraction toolkit. Fetches any website, strips junk, extracts structured data with LLMs. Includes web search with deep extraction.

## Installation

```bash
# Clone the repo
git clone <repo-url> scrapling-kit
cd scrapling-kit

# Create Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install "scrapling[all]"

# Install browser engines (Chromium for JS-rendered sites)
scrapling install

# Configure API keys
cp .env.example .env
# Edit .env with your keys
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

# Deep extract top N results (parallel by default)
python3 extract.py --search "cuba latest news" --deep 5 -m deepseek-v3

# Deep extract sequentially (safer for free models / rate limits)
python3 extract.py --search "cuba latest news" --deep 5 -m auto-free --sequential

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
| `deepseek-v3` | DeepSeek V3.2 | 163K | $0.25 |

## MCP Server

The toolkit can be used as an [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server, allowing any MCP-compatible AI client (Claude Desktop, Cursor, Windsurf, etc.) to use the scraping tools directly.

### Tools Exposed

| Tool | Description |
|------|-------------|
| `tool_extract` | Extract structured data from any URL using an LLM |
| `tool_search` | Search the web via Brave Search API |
| `tool_search_extract` | Search + deep extract content from top results |
| `tool_list_models` | List available LLM models (free + paid) |

### Local Setup (stdio)

For local AI clients that launch the server directly:

```bash
python3 mcp_server.py
```

**Claude Desktop** (`~/.config/claude/claude_desktop_config.json`):
```json
{
    "mcpServers": {
        "monsoft-scrapper": {
            "command": "/path/to/venv/bin/python3",
            "args": ["/path/to/scrapling-kit/mcp_server.py"]
        }
    }
}
```

**Cursor** (`.cursor/mcp.json`):
```json
{
    "mcpServers": {
        "monsoft-scrapper": {
            "command": "/path/to/venv/bin/python3",
            "args": ["/path/to/scrapling-kit/mcp_server.py"]
        }
    }
}
```

### Remote Setup (SSE over HTTPS)

To expose the MCP server to remote clients over the network:

#### 1. Start the MCP server in SSE mode

```bash
python3 mcp_server.py --sse --host 127.0.0.1 --port 8808
```

#### 2. Create a systemd service (keeps it running)

Create `/etc/systemd/system/monsoft-scrapper-mcp.service`:

```ini
[Unit]
Description=Monsoft Scrapper MCP Server (SSE)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/path/to/scrapling-kit
ExecStart=/path/to/venv/bin/python3 /path/to/scrapling-kit/mcp_server.py --sse --host 127.0.0.1 --port 8808
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable monsoft-scrapper-mcp
sudo systemctl start monsoft-scrapper-mcp
```

#### 3. Set up a reverse proxy with HTTPS

The MCP server binds to localhost — you need a reverse proxy to expose it over HTTPS.

**Using Caddy** (recommended — automatic TLS):

Add to your `Caddyfile`:

```caddyfile
scrapper-mcp.yourdomain.com {
    reverse_proxy localhost:8808 {
        header_up Host localhost:8808
    }
}
```

> **Important:** The `header_up Host` directive is required. The MCP SSE server validates the Host header, and without this rewrite it will reject requests with a 421 error.

```bash
sudo systemctl reload caddy
```

**Using nginx:**

```nginx
server {
    listen 443 ssl;
    server_name scrapper-mcp.yourdomain.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8808;
        proxy_set_header Host localhost:8808;
        proxy_set_header X-Real-IP $remote_addr;

        # Required for SSE
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding off;
    }
}
```

#### 4. Point your DNS

Create an **A record** for your subdomain pointing to your server's IP.

If using Cloudflare, set the record to **DNS only** (no proxy) — Caddy/nginx handles TLS.

#### 5. Connect remote clients

```json
{
    "mcpServers": {
        "monsoft-scrapper": {
            "url": "https://scrapper-mcp.yourdomain.com/sse"
        }
    }
}
```

### CLI Options

```
python3 mcp_server.py                              # stdio (local)
python3 mcp_server.py --sse --port 8808            # SSE (remote)
python3 mcp_server.py --streamable-http            # Streamable HTTP
python3 mcp_server.py --sse --host 0.0.0.0         # Bind all interfaces (no reverse proxy)
```

## Usage Statistics

```bash
# Show overall usage stats
python3 extract.py --stats

# Stats filtered by model
python3 extract.py --stats -m deepseek-v3

# Show last N extractions
python3 extract.py --history 20
```

## User & API Key Management

```bash
# Create users
python3 manage.py user add "adriano" --email adriano@example.com
python3 manage.py user list

# Create API keys
python3 manage.py key create --user adriano --name "production"
python3 manage.py key create --user adriano --name "testing" --rate-limit 10 --models "auto-free,deepseek-v3"
python3 manage.py key list
python3 manage.py key revoke mss_...

# Per-user stats
python3 manage.py stats --user adriano
```

## Files

| File | Purpose |
|---|---|
| `extract.py` | AI extraction + search + CLI. Main entry point. |
| `fetch.py` | URL fetcher with auto browser fallback. |
| `extractor.py` | Engine: junk stripping, text extraction, link categorization. |
| `db.py` | SQLite logging database for extractions, searches, model usage. |
| `auth.py` | API key authentication, validation, rate limiting. |
| `manage.py` | CLI for user and API key management. |
| `mcp_server.py` | MCP server (stdio + SSE + streamable-http). |
| `.env` | API keys and default model config. |

## Cost Examples

| Task | Model | Tokens | Cost | Time |
|---|---|---|---|---|
| Single clinic extraction | deepseek-v3 | 3K→640 | $0.002 | 15s |
| News article extraction | deepseek-v3 | 12K→1K | $0.005 | 28s |
| 3 articles deep extract (seq) | deepseek-v3 | 4K→1.4K | $0.003 | 90s |
| 3 articles deep extract (parallel) | deepseek-v3 | 4K→1.3K | $0.003 | 48s |
| Single clinic extraction | auto-free | 3K→2K | FREE | 16s |
