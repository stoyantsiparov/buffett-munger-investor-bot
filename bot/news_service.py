"""Free real-time stock news & regulatory event fetcher (Google News RSS + Yahoo Finance RSS, 0 API keys required)."""

from __future__ import annotations

import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

_NEWS_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}
USER_AGENT = "Mozilla/5.0 (compatible; BuffettMungerInvestorBot/1.3.0)"


def fetch_live_ticker_news(ticker: str, company_name: str | None = None, limit: int = 5) -> list[dict[str, str]]:
    """Fetch the latest real-time news headlines for a stock ticker from Yahoo Finance RSS & Google News RSS."""
    clean_tk = ticker.strip().upper()
    now = time.time()
    cached = _NEWS_CACHE.get(clean_tk)
    if cached and (now - cached[0]) < 900:
        return cached[1][:limit]

    headlines: list[dict[str, str]] = []
    seen_titles: set[str] = set()

    short_company = ""
    if company_name and company_name.upper() != clean_tk:
        first_tok = company_name.replace(",", "").replace(".", "").split()[0]
        if first_tok.lower() not in ("the", "inc", "corp"):
            short_company = first_tok
        elif len(company_name.split()) > 1:
            short_company = company_name.replace(",", "").replace(".", "").split()[1]

    q_term = f"{clean_tk} {short_company} stock".strip()
    rss_urls = [
        f"https://news.google.com/rss/search?q={urllib.parse.quote(q_term)}&hl=en-US&gl=US&ceid=US:en",
        f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={urllib.parse.quote(clean_tk)}&region=US&lang=en-US",
    ]

    match_tokens = {clean_tk.lower()}
    if short_company and len(short_company) >= 3:
        match_tokens.add(short_company.lower())

    for url in rss_urls:
        if len(headlines) >= limit:
            break
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                xml_bytes = resp.read()
            root = ET.fromstring(xml_bytes)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                pub_date = (item.findtext("pubDate") or "").strip()
                if not title:
                    continue
                low_t = title.lower()
                if "yahoo.com" in url and not any(tok in low_t for tok in match_tokens):
                    continue
                norm = low_t[:65]
                if norm in seen_titles:
                    continue
                seen_titles.add(norm)
                short_date = pub_date[:16] if pub_date else ""
                headlines.append({"title": title, "date": short_date})
                if len(headlines) >= limit:
                    break
        except Exception:
            continue

    if headlines:
        _NEWS_CACHE[clean_tk] = (now, headlines)
    return headlines[:limit]


def format_news_context_for_ai(ticker_news: dict[str, list[dict[str, str]]]) -> str:
    """Format fetched news headlines into a compact string for the Gemini/Gemma AI prompt."""
    blocks: list[str] = []
    for tk, items in ticker_news.items():
        if not items:
            continue
        joined = " | ".join(f"[{x['date']}] {x['title']}" for x in items[:5])
        blocks.append(f"Live News ({tk}): {joined}")
    return "\n".join(blocks)
