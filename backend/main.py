import json
import logging
import os
import re
import difflib
import ipaddress
import html
from contextlib import contextmanager
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
from typing import Any

import requests
import trafilatura
import uvicorn
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from groq import Groq
from pydantic import BaseModel
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode, urljoin, unquote


# Alternative AI providers
try:
    from google import genai
except ImportError:
    genai = None

try:
    from huggingface_hub import InferenceClient
except ImportError:
    InferenceClient = None


# Basic logging keeps useful information without excessive output.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s"
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@contextmanager
def timed_stage(stage: str, **fields: Any):
    """Log elapsed time for one audit stage without changing its result."""
    started = perf_counter()
    try:
        yield
    finally:
        details = " ".join(
            f"{key}={value!r}" for key, value in fields.items() if value is not None
        )
        logging.info(
            "TIMING stage=%s elapsed_seconds=%.3f%s",
            stage,
            perf_counter() - started,
            f" {details}" if details else "",
        )


# Load environment variables.
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
HF_TOKEN = os.getenv("HF_TOKEN")


# AI model configuration.
GROQ_MODEL = "openai/gpt-oss-120b"
GEMINI_MODEL = "gemini-3.6-flash"
HF_MODEL = "openai/gpt-oss-120b"


# Shared HTTP settings prevent repeated configuration.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/142.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_TIMEOUT = 7
GOOGLE_NEWS_TIMEOUT = 6
MAX_NEWS_RESULTS = 5  # keep each claim bounded and avoid long audit runs
MAX_WEB_SEARCH_RESULTS = 3
MAX_EVIDENCE_SOURCES = 5
MINIMUM_EVIDENCE_COVERAGE = 0.50
MINIMUM_EVIDENCE_TEXT_LENGTH = 400
ORIGINAL_TITLE_DUPLICATE_THRESHOLD = 0.90
ARTICLE_DUPLICATE_TEXT_OVERLAP = 0.45


# Verification results used throughout the application.
ALLOWED_STATUSES = {
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "UNSUPPORTED",
    "CONTRADICTED",
    "UNVERIFIED",
}

STATUS_SCORES = {
    "SUPPORTED": 100,
    "PARTIALLY_SUPPORTED": 65,
    "UNSUPPORTED": 25,
    "CONTRADICTED": 0,
}


# FastAPI application.
app = FastAPI(title="OpenNews Auditor")


# Frontend paths.
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

app.mount(
    "/static",
    StaticFiles(directory=str(FRONTEND_DIR)),
    name="static"
)


@app.get("/")
def home() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


# AI clients are optional because fallback providers are supported.
groq_client = None
gemini_client = None
hf_client = None


def initialize_ai_clients() -> None:
    """Initialize every AI provider available in the .env file."""
    global groq_client, gemini_client, hf_client

    if GROQ_API_KEY:
        try:
            # Fall back to the next configured provider immediately when Groq
            # is rate-limited instead of waiting through SDK retries.
            groq_client = Groq(api_key=GROQ_API_KEY, max_retries=0)
            logging.info("Groq API enabled.")
        except Exception as error:
            logging.warning(f"Groq initialization failed: {error}")
    else:
        logging.warning("GROQ_API_KEY not found. Groq disabled.")

    if GEMINI_API_KEY and genai:
        try:
            gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            logging.info("Gemini API enabled.")
        except Exception as error:
            logging.warning(f"Gemini initialization failed: {error}")

    if HF_TOKEN and InferenceClient:
        try:
            hf_client = InferenceClient(
                api_key=HF_TOKEN,
                provider="auto"
            )
            logging.info("Hugging Face API enabled.")
        except Exception as error:
            logging.warning(
                f"Hugging Face initialization failed: {error}"
            )

    if not any([groq_client, gemini_client, hf_client]):
        logging.warning(
            "No AI provider is configured. Set GROQ_API_KEY, "
            "GEMINI_API_KEY, or HF_TOKEN before running an audit."
        )


initialize_ai_clients()

class ArticleRequest(BaseModel):
    url: str


def clean_json_markdown(text: str) -> str:
    """Remove Markdown code fences around JSON."""
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_json(text: str) -> Any:
    """Extract valid JSON even when the AI adds surrounding text."""
    cleaned = clean_json_markdown(text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    for opening, closing in [("{", "}"), ("[", "]")]:
        start = cleaned.find(opening)
        end = cleaned.rfind(closing)

        if start == -1 or end == -1 or end <= start:
            continue

        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            continue

    raise ValueError("The AI response did not contain valid JSON.")


def ask_groq(prompt: str) -> str:
    """Send a prompt to Groq."""
    if not groq_client:
        raise RuntimeError("Groq is not configured.")

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.1
    )

    result = response.choices[0].message.content

    if not result:
        raise ValueError("Groq returned an empty response.")

    return clean_json_markdown(result)


def ask_gemini(prompt: str) -> str:
    """Send a prompt to Gemini."""
    if not gemini_client:
        raise RuntimeError("Gemini is not configured.")

    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt
    )

    result = response.text

    if not result:
        raise ValueError("Gemini returned an empty response.")

    return clean_json_markdown(result)


def ask_huggingface(prompt: str) -> str:
    """Send a prompt to Hugging Face."""
    if not hf_client:
        raise RuntimeError("Hugging Face is not configured.")

    response = hf_client.chat.completions.create(
        model=HF_MODEL,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.1,
        max_tokens=4096
    )

    result = response.choices[0].message.content

    if not result:
        raise ValueError(
            "Hugging Face returned an empty response."
        )

    return clean_json_markdown(result)


def ask_ai(
    prompt: str,
    purpose: str = "AI analysis"
) -> str:
    """Try each configured AI provider until one succeeds."""
    providers = [
        ("Groq", ask_groq, groq_client),
        ("Gemini", ask_gemini, gemini_client),
        ("Hugging Face", ask_huggingface, hf_client),
    ]

    errors = []

    for provider_name, provider_function, client in providers:
        if not client:
            continue

        try:
            logging.info(
                f"AI provider: {provider_name} for {purpose}"
            )
            started = perf_counter()
            result = provider_function(prompt)

            logging.info(
                "%s succeeded elapsed_seconds=%.3f retry_wait_seconds=0.000 purpose=%s",
                provider_name,
                perf_counter() - started,
                purpose,
            )

            return result

        except Exception as error:
            error_text = str(error)
            errors.append(
                f"{provider_name}: {error_text}"
            )

            logging.warning(
                f"{provider_name} failed: {error_text}"
            )

    raise RuntimeError(
        f"All AI providers failed for {purpose}. "
        f"Errors: {' | '.join(errors)}"
    )

#download the webpage 
def validate_public_http_url(url: str) -> str:
    """Validate a user-supplied URL before requesting it."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Please enter a complete article URL beginning with http:// or https://.")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("Local or private network URLs cannot be audited.")
    # Reject literal private/loopback/link-local IPs.
    import ipaddress
    try:
        address = ipaddress.ip_address(hostname)
        if not address.is_global:
            raise ValueError("Local or private network URLs cannot be audited.")
    except ValueError as error:
        if str(error) == "Local or private network URLs cannot be audited.":
            raise
        # A non-IP hostname is expected; DNS resolution is left to requests.
    return url.strip()


def fetch_url(url: str) -> str:
    url = validate_public_http_url(url)
    try:
        response = requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()
        final_url = validate_public_http_url(response.url)
        # Do not return content from a redirect into a local/private URL.
        if final_url != response.url:
            raise ValueError("Redirect destination is not allowed.")
        return response.text
    except requests.RequestException as error:
        raise RuntimeError(f"Could not access article URL: {error}") from error


# extracts the headline , do trafilatura first and if that fail use beautifulSoup
def extract_headline(html: str) -> str:
    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata:
            headline = getattr(metadata, "title", None)

            if headline:
                return headline
    except Exception:
        pass
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")

    return (
        title_tag.get_text(" ", strip=True)
        if title_tag
        else ""
    )

# when Trafilatura fail to extract this is activated 
def fallback_extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(
        ["script","style","noscript","nav","footer","header"]
    ):
        tag.decompose()
    return soup.get_text(" ", strip=True)

# extract the main article text
def extract_article_text(html: str) -> str:
    article_text = trafilatura.extract(
        html,include_comments=False,include_tables=False,
        include_links=False,favor_precision=True)

    if article_text and len(article_text) >= 200:
        return article_text

    logging.warning(
        "Trafilatura extracted little text. "
        "Using fallback extraction."
    )

    fallback_text = fallback_extract_text(html)

    if not fallback_text:
        raise RuntimeError(
            "Could not extract article text."
        )

    return fallback_text

# download and extract the article
def fetch_article(url: str) -> dict[str, str]:
    logging.info(f"Fetching article: {url}")
    with timed_stage("article_fetch", url=url):
        html = fetch_url(url)
        canonical_url = ""
        canonical_tag = BeautifulSoup(html, "html.parser").select_one('link[rel="canonical"]')
        if canonical_tag and canonical_tag.get("href"):
            candidate = urljoin(url, canonical_tag["href"].strip())
            try:
                canonical_url = validate_public_http_url(candidate)
            except ValueError:
                canonical_url = ""
        return {
            "text": extract_article_text(html),
            "headline": extract_headline(html),
            "url": url,
            "canonical_url": canonical_url,
        }

def extract_claims( # uses AI to extract the main claims 
    article_text: str
) -> list[dict[str, Any]]:
    article_for_ai = article_text[:30000]

    prompt = f"""
You are the claim extraction component of a news auditing system.

Read the article below and identify the main factual claims that
can be independently checked using external evidence.

Extract between 5 and 10 important claims.

Only extract claims that are factual and potentially verifiable.

Do NOT decide whether a claim is true or false.

Do NOT add facts that are not present in the article.

Ignore opinions, predictions, rhetorical questions, and purely
subjective statements unless they contain a specific factual claim.

Return ONLY valid JSON.

Use this exact format:

{{
  "claims": [
    {{
      "claim": "A specific factual statement",
      "type": "event|location|number|person|organization|policy|historical|other",
      "entities": ["important entity"],
      "evidence_in_article": "Short description of where the article supports the claim",
      "source_attribution": "Named source if the article attributes the claim, otherwise empty string",
      "verification_required": true
    }}
  ]
}}

ARTICLE:

{article_for_ai}
"""

    try:
        with timed_stage("claim_extraction"):
            raw = ask_ai(
                prompt,
                purpose="claim extraction"
            )

        data = extract_json(raw)

        if isinstance(data, dict):
            claims = data.get("claims", [])
        elif isinstance(data, list):
            claims = data
        else:
            claims = []

        return claims[:10] if isinstance(claims, list) else []

    except Exception as error:
        logging.error(
            f"Claim extraction error: {error}"
        )

        raise RuntimeError(
            "Could not extract factual claims from the article."
        )


def generate_search_query(
    claim: dict[str, Any],
    original_url: str = "",
) -> str:
    """Build a focused query from the claim, preferring named entities."""
    claim_text = str(claim.get("claim", "")).strip()
    if not claim_text:
        return ""

    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "has", "have", "had", "that", "this", "these", "those",
        "and", "or", "but", "of", "to", "in", "on", "for", "with",
        "from", "by", "as", "after", "before", "during", "into", "its",
        "their", "it", "they", "he", "she", "who", "which", "according",
        "said", "says", "reported", "reports", "claim", "claims", "claimed",
        "announced", "stated", "reportedly", "allegedly", "would", "could",
        "should", "will", "may", "might", "about", "over", "under", "more",
        "than", "also", "not", "no", "new", "one", "two", "first", "last",
    }

    parts: list[str] = []
    entity_tokens: set[str] = set()
    entities = claim.get("entities", [])
    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, str):
                continue
            entity = re.sub(r"\s+", " ", entity).strip(" \t\r\n.,!?;:")
            if entity and entity.casefold() not in {p.strip('"').casefold() for p in parts}:
                # Quoting multi-word entities helps search engines preserve names.
                parts.append(f'"{entity}"' if " " in entity else entity)
                entity_tokens.update(
                    token.casefold() for token in re.findall(r"[\w'-]+", entity)
                )

    for word in re.findall(r"[\w'-]+", claim_text, flags=re.UNICODE):
        cleaned = word.strip("-'_")
        if len(cleaned) < 3 or cleaned.casefold() in stop_words:
            continue
        if cleaned.casefold() in entity_tokens:
            continue
        if cleaned.casefold() not in {p.strip('"').casefold() for p in parts}:
            parts.append(cleaned)
        if len(parts) >= 8:
            break

    # Keep the query concise; overly long queries can suppress useful results.
    query = " ".join(parts[:8])
    original_host = (urlparse(original_url).hostname or "").lower()
    if original_host.startswith("www."):
        original_host = original_host[4:]
    if original_host and "." in original_host and not original_host.endswith("google.com"):
        query += f" -site:{original_host}"
    return query.strip()


def _parse_rss_articles(response_content: bytes, provider: str) -> list[dict[str, Any]]:
    """Parse a news RSS response into normalized candidate records."""
    try:
        soup = BeautifulSoup(response_content, "xml")
    except Exception as error:
        logging.warning("Could not parse %s RSS: %s", provider, error)
        return []

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in soup.find_all("item"):
        title_tag = item.find("title")
        link_tag = item.find("link")
        if not title_tag or not link_tag:
            continue

        link = link_tag.get_text(strip=True)
        title = title_tag.get_text(" ", strip=True)
        if not link or not title or link in seen:
            continue
        seen.add(link)

        description_tag = item.find("description")
        source_tag = item.find("source")
        published_tag = item.find("pubDate")
        description = ""
        if description_tag:
            description = BeautifulSoup(
                description_tag.get_text(), "html.parser"
            ).get_text(" ", strip=True)

        source = source_tag.get_text(strip=True) if source_tag else ""
        source_url = source_tag.get("url", "") if source_tag else ""
        published = published_tag.get_text(strip=True) if published_tag else ""
        host = (urlparse(link).hostname or "").lower()

        results.append({
            "title": title,
            "description": description,
            "url": link,
            "google_url": link if provider == "Google News" else "",
            # Do not label a search-engine wrapper as the publisher. The
            # actual publisher is determined after the article URL is resolved.
            "source": source if source and not _is_search_wrapper_host((urlparse(source_url).hostname or "")) and source.casefold() not in {"bing", "bing news", "google news"} else "",
            "source_url": source_url,
            "published": published,
            "provider": provider,
            "is_google_news_link": provider == "Google News",
        })
        if len(results) >= MAX_NEWS_RESULTS:
            break
    return results


def fetch_google_news(query: str) -> list[dict[str, Any]]:
    """Search Google News RSS for candidate external reports."""
    endpoint = "https://news.google.com/rss/search"
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    try:
        response = requests.get(
            endpoint, params=params, headers=DEFAULT_HEADERS,
            timeout=GOOGLE_NEWS_TIMEOUT,
        )
        response.raise_for_status()
        return _parse_rss_articles(response.content, "Google News")
    except requests.RequestException as error:
        logging.warning("Google News RSS request failed: %s", error)
        return []


def fetch_bing_news(query: str) -> list[dict[str, Any]]:
    """Fallback news search; Bing RSS often supplies publisher URLs directly."""
    endpoint = "https://www.bing.com/news/search"
    params = {"q": query, "format": "rss", "setlang": "en-US", "cc": "US"}
    try:
        response = requests.get(
            endpoint, params=params, headers=DEFAULT_HEADERS,
            timeout=GOOGLE_NEWS_TIMEOUT,
        )
        response.raise_for_status()
        return _parse_rss_articles(response.content, "Bing News")
    except requests.RequestException as error:
        logging.warning("Bing News RSS request failed: %s", error)
        return []



# Legacy Bing HTML and DuckDuckGo scraping helpers were removed.
# Normal audits use the bounded Bing News / Google News RSS workflow only.

def search_news(
    claim: dict[str, Any],
    original_url: str = "",
) -> list[dict[str, Any]]:
    """Use bounded RSS searches; avoid slow/block-prone HTML search scraping."""
    claim_text = str(claim.get("claim", "")).strip()
    logging.info("Checking claim: %s", claim_text)
    query = generate_search_query(claim, original_url)
    if not query:
        return []

    # Bing News RSS usually supplies direct publisher URLs. Google News RSS is
    # retained as a backup, but opaque Google URLs are not repeatedly searched
    # through other providers if they cannot be resolved.
    with timed_stage("rss_search", query=query):
        with ThreadPoolExecutor(max_workers=2) as executor:
            bing_future = executor.submit(fetch_bing_news, query)
            google_future = executor.submit(fetch_google_news, query)
            bing_results = bing_future.result()
            google_results = google_future.result()
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Prefer Bing's publisher URLs. Only add a small number of Google News
    # wrappers, because some cannot be resolved by automated clients.
    for article in bing_results:
        key = normalize_url(str(article.get("url", "")))
        if key and key not in seen:
            seen.add(key)
            merged.append(article)
        if len(merged) >= MAX_NEWS_RESULTS:
            break
    if len(merged) < 3:
        for article in google_results:
            key = normalize_url(str(article.get("url", "")))
            if key and key not in seen:
                seen.add(key)
                merged.append(article)
            if len(merged) >= MAX_NEWS_RESULTS:
                break

    # Only retry once with a short, broad query when both RSS feeds are empty.
    if not merged:
        words = [w for w in re.findall(r"[\w'-]+", claim_text) if len(w) > 3]
        broad_query = " ".join(words[:7])
        if broad_query and broad_query.casefold() != query.casefold():
            with timed_stage("rss_search_retry", query=broad_query):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    bing_future = executor.submit(fetch_bing_news, broad_query)
                    google_future = executor.submit(fetch_google_news, broad_query)
                    bing_results = bing_future.result()
                    google_results = google_future.result()
            for article in (bing_results + google_results):
                key = normalize_url(str(article.get("url", "")))
                if key and key not in seen:
                    seen.add(key)
                    merged.append(article)
                if len(merged) >= MAX_NEWS_RESULTS:
                    break

    logging.info(
        "Bounded RSS search: Bing RSS=%s, Google RSS=%s, merged=%s; HTML search fallbacks disabled",
        len(bing_results), len(google_results), len(merged),
    )
    return merged

def _is_search_wrapper_host(host: str) -> bool:
    host = (host or "").lower().split(":", 1)[0]
    return (
        host in {"google.com", "www.google.com", "news.google.com"}
        or host.endswith(".google.com")
        or host in {"bing.com", "www.bing.com", "duckduckgo.com", "www.duckduckgo.com"}
        or host.endswith(".duckduckgo.com")
    )


def _embedded_target_url(url: str) -> str | None:
    """Extract a publisher URL from common search redirect query parameters."""
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in ("url", "u", "target", "to", "r"):
            for value in query.get(key, []):
                candidate = unquote(value).strip()
                if candidate.startswith(("http://", "https://")):
                    host = (urlparse(candidate).hostname or "").lower()
                    if host and not _is_search_wrapper_host(host):
                        return candidate
    except Exception:
        return None
    return None


def resolve_publisher_url(url: str, expected_title: str = "") -> str | None:
    """Resolve search-provider wrappers to a publisher URL where possible."""
    if not url:
        return None
    try:
        url = validate_public_http_url(url)
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not _is_search_wrapper_host(host):
            return url

        embedded = _embedded_target_url(url)
        if embedded:
            return validate_public_http_url(embedded)

        response = requests.get(
            url, headers=DEFAULT_HEADERS, timeout=4,
            allow_redirects=True,
        )
        response.raise_for_status()
        final_url = response.url
        final_host = (urlparse(final_url).hostname or "").lower()
        if not _is_search_wrapper_host(final_host):
            response.close()
            return validate_public_http_url(final_url)

        # Some Google News links render a wrapper page instead of redirecting.
        page_html = response.text
        page = BeautifulSoup(page_html, "html.parser")
        response.close()
        for selector, attribute in [
            ('link[rel="canonical"]', "href"),
            ('meta[property="og:url"]', "content"),
            ('meta[name="citation_public_url"]', "content"),
            ('meta[itemprop="url"]', "content"),
            ('[data-n-au]', "data-n-au"),
            ('[data-n-url]', "data-n-url"),
        ]:
            tag = page.select_one(selector)
            candidate = tag.get(attribute, "").strip() if tag else ""
            if candidate:
                candidate = html.unescape(candidate).replace("\\u003d", "=").replace("\\u0026", "&").replace("\\/", "/")
                candidate = urljoin(final_url, candidate)
                candidate_host = (urlparse(candidate).hostname or "").lower()
                if candidate_host and not _is_search_wrapper_host(candidate_host):
                    try:
                        return validate_public_http_url(candidate)
                    except ValueError:
                        pass

        # Google sometimes embeds the publisher URL in page data rather than an anchor.
        decoded_html = html.unescape(page_html).replace("\\u003d", "=").replace("\\u0026", "&").replace("\\/", "/")
        embedded_candidates = re.findall(r"(?i)(?:https?:)?//[^\s\"'<>\\]+", decoded_html)
        title_words_for_match = {
            word.casefold() for word in re.findall(r"[\w'-]+", expected_title) if len(word) > 3
        }
        for raw_candidate in embedded_candidates:
            candidate = raw_candidate if raw_candidate.startswith("http") else "https:" + raw_candidate
            candidate = candidate.rstrip(".,);]")
            candidate_host = (urlparse(candidate).hostname or "").lower()
            if not candidate_host or _is_search_wrapper_host(candidate_host):
                continue
            try:
                candidate = validate_public_http_url(candidate)
            except ValueError:
                continue
            # Prefer URLs that resemble the expected article title; otherwise continue
            # to the anchor matching below rather than selecting an arbitrary external URL.
            url_words = {word.casefold() for word in re.findall(r"[\w'-]+", urlparse(candidate).path) if len(word) > 3}
            if title_words_for_match and len(title_words_for_match & url_words) / len(title_words_for_match) >= 0.25:
                return candidate

        # Last resort: prefer an external link whose visible text resembles the
        # RSS headline; never blindly use the source homepage as article evidence.
        title_words = {
            word.casefold() for word in re.findall(r"[\w'-]+", expected_title)
            if len(word) > 3
        }
        best_url = None
        best_overlap = 0.0
        for anchor in page.find_all("a", href=True):
            candidate = urljoin(final_url, anchor["href"])
            candidate_host = (urlparse(candidate).hostname or "").lower()
            if not candidate_host or _is_search_wrapper_host(candidate_host):
                continue
            anchor_words = {
                word.casefold() for word in re.findall(
                    r"[\w'-]+", anchor.get_text(" ", strip=True)
                ) if len(word) > 3
            }
            overlap = len(title_words & anchor_words) / max(1, len(title_words))
            if overlap > best_overlap:
                best_overlap, best_url = overlap, candidate
        if best_url and best_overlap >= 0.5:
            return validate_public_http_url(best_url)

        logging.info("Could not resolve search-provider URL: %s", url)
        return None
    except (requests.RequestException, ValueError) as error:
        logging.warning("Could not resolve publisher URL %s: %s", url, error)
        return None


def _normalized_text(text: str) -> str:
    text = (text or "").casefold()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _title_similarity(first: str, second: str) -> float:
    a, b = _normalized_text(first), _normalized_text(second)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _text_overlap(first: str, second: str, n: int = 5) -> float:
    """Measure copied/syndicated text using word-shingle containment."""
    a = _normalized_text(first).split()
    b = _normalized_text(second).split()
    if min(len(a), len(b)) < n:
        return 0.0
    shingles_a = {tuple(a[i:i+n]) for i in range(len(a) - n + 1)}
    shingles_b = {tuple(b[i:i+n]) for i in range(len(b) - n + 1)}
    denominator = max(1, min(len(shingles_a), len(shingles_b)))
    return len(shingles_a & shingles_b) / denominator


def _looks_like_same_article(
    candidate: dict[str, Any],
    original_article: dict[str, Any] | None = None,
    accepted_articles: list[dict[str, Any]] | None = None,
) -> bool:
    """Conservatively reject exact copies and obvious syndicated duplicates."""
    comparisons: list[dict[str, Any]] = list(accepted_articles or [])
    if original_article:
        comparisons.append(original_article)
    title = str(candidate.get("title", ""))
    body = str(candidate.get("text", ""))
    for previous in comparisons:
        previous_title = str(previous.get("headline") or previous.get("title", ""))
        previous_body = str(previous.get("text", ""))
        title_ratio = _title_similarity(title, previous_title)
        text_overlap = _text_overlap(body, previous_body)
        if title_ratio >= ORIGINAL_TITLE_DUPLICATE_THRESHOLD:
            logging.info("Skipping likely duplicate article by title: %s", title)
            return True
        if text_overlap >= ARTICLE_DUPLICATE_TEXT_OVERLAP:
            logging.info("Skipping likely syndicated/copied article: %s", title)
            return True
    return False


def build_article_evidence(
    article: dict[str, Any],
) -> dict[str, Any] | None:
    """Download full publisher article text; RSS metadata alone is never evidence."""
    submitted_url = str(article.get("url", "")).strip()
    if not submitted_url:
        return None

    with timed_stage("url_resolution", url=submitted_url):
        publisher_url = resolve_publisher_url(
            submitted_url, expected_title=str(article.get("title", ""))
        )
    if not publisher_url:
        # Do not trigger multiple secondary search-engine requests for every
        # failed Google News URL. This was the main source of 10-minute audits.
        logging.info("Skipping unresolved search-provider URL without secondary scraping: %s", submitted_url)
        return None

    try:
        with timed_stage("evidence_download", url=publisher_url):
            response = requests.get(
                publisher_url, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT,
                allow_redirects=True,
            )
            response.raise_for_status()
            final_url = validate_public_http_url(response.url)
            final_host = (urlparse(final_url).hostname or "").lower()
            if _is_search_wrapper_host(final_host):
                response.close()
                return None

            html = response.text
            response.close()
            text = trafilatura.extract(
                html, include_comments=False, include_tables=False,
                include_links=False, favor_precision=True,
            ) or ""
            # Try the existing HTML fallback when Trafilatura cannot extract enough.
            if len(text.strip()) < MINIMUM_EVIDENCE_TEXT_LENGTH:
                fallback = fallback_extract_text(html)
                if len(fallback.strip()) > len(text.strip()):
                    text = fallback
            if not text or len(text.strip()) < MINIMUM_EVIDENCE_TEXT_LENGTH:
                logging.info("Skipping result with insufficient article text: %s", final_url)
                return None

            metadata = trafilatura.extract_metadata(html)
            title = (getattr(metadata, "title", None) if metadata else None) or article.get("title", "")
            rss_title = str(article.get("title", "") or "").strip()
            # Search results can occasionally point to a similarly named but unrelated page.
            if rss_title and title and _title_similarity(rss_title, str(title)) < 0.22:
                logging.info("Skipping title-mismatched search result: %s", final_url)
                return None
            canonical_tag = BeautifulSoup(html, "html.parser").select_one('link[rel="canonical"]')
            canonical_url = ""
            if canonical_tag and canonical_tag.get("href"):
                candidate = urljoin(final_url, canonical_tag["href"].strip())
                candidate_host = (urlparse(candidate).hostname or "").lower()
                if candidate_host and not _is_search_wrapper_host(candidate_host):
                    try:
                        canonical_url = validate_public_http_url(candidate)
                    except ValueError:
                        canonical_url = ""

            evidence_url = canonical_url or final_url

            # Use the publisher's own metadata or the resolved article domain.
            # Never expose Bing/Google News as the publisher just because the RSS
            # search result was served through that provider.
            metadata_site = str(getattr(metadata, "sitename", "") or "").strip() if metadata else ""
            rss_source = str(article.get("source", "") or "").strip()
            rss_source_host = (urlparse(rss_source).hostname or "").lower()
            invalid_source_names = {"bing", "bing news", "google news", "www.bing.com", "bing.com", "news.google.com"}
            if metadata_site and metadata_site.casefold() not in invalid_source_names:
                source = metadata_site
            elif rss_source and rss_source.casefold() not in invalid_source_names and not _is_search_wrapper_host(rss_source_host):
                source = rss_source
            else:
                publisher_host = (urlparse(evidence_url).hostname or urlparse(final_url).hostname or "").lower()
                if publisher_host.startswith("www."):
                    publisher_host = publisher_host[4:]
                source = publisher_host or "External publisher"

            return {
                "title": str(title),
                "description": article.get("description", ""),
                "url": evidence_url,
                "final_url": final_url,
                "source": source,
                "published": article.get("published", ""),
                "text": text[:12000],
                "evidence_type": "Full external article",
                "provider": article.get("provider", ""),
            }
    except (requests.RequestException, ValueError) as error:
        logging.warning("Could not download evidence from %s: %s", publisher_url, error)
        return None


def collect_evidence(
    articles: list[dict[str, Any]],
    original_url: str,
    original_article: dict[str, Any] | None = None,
    evidence_cache: dict[str, dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    """Collect full-text evidence, excluding original and syndicated duplicates."""
    evidence: list[dict[str, Any]] = []
    evidence_cache = evidence_cache if evidence_cache is not None else {}
    original_normalized_urls = {normalize_url(original_url)}
    if original_article:
        for original_candidate in (
            original_article.get("url", ""),
            original_article.get("canonical_url", ""),
        ):
            normalized_candidate = normalize_url(str(original_candidate))
            if normalized_candidate:
                original_normalized_urls.add(normalized_candidate)
    seen_urls: set[str] = set()
    seen_domains: set[str] = set()
    ordered_items: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    pending: list[tuple[int, dict[str, Any], str]] = []

    for article in articles:
        raw_url = str(article.get("url", "")).strip()
        if not raw_url:
            continue
        if normalize_url(raw_url) in original_normalized_urls:
            logging.info("Skipping submitted article before downloading: %s", raw_url)
            continue

        cache_key = normalize_url(raw_url)
        item = evidence_cache.get(cache_key) if cache_key in evidence_cache else None
        ordered_items.append((article, item))
        if cache_key not in evidence_cache:
            pending.append((len(ordered_items) - 1, article, cache_key))

    # Resolve and download independent candidates concurrently, but keep the
    # original RSS order when applying evidence and duplicate filters below.
    if pending:
        with ThreadPoolExecutor(max_workers=min(4, len(pending))) as executor:
            futures = {
                index: executor.submit(build_article_evidence, article)
                for index, article, _ in pending
            }
            for index, _, cache_key in pending:
                item = futures[index].result()
                ordered_items[index] = (ordered_items[index][0], item)
                evidence_cache[cache_key] = item

    for article, item in ordered_items:
        if not item:
            continue

        resolved_url = item.get("url", "")
        normalized_url = normalize_url(resolved_url)
        final_normalized = normalize_url(item.get("final_url", ""))
        if normalized_url in original_normalized_urls or final_normalized in original_normalized_urls:
            logging.info("Skipping the submitted article after resolving redirects: %s", resolved_url)
            continue
        if not normalized_url or normalized_url in seen_urls or final_normalized in seen_urls:
            continue

        # Reject same article syndicated under a different publisher/domain.
        if _looks_like_same_article(item, original_article, evidence):
            continue

        domain = (urlparse(resolved_url).hostname or "").lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if domain and domain in seen_domains:
            continue

        seen_urls.add(normalized_url)
        if final_normalized:
            seen_urls.add(final_normalized)
        if domain:
            seen_domains.add(domain)
        evidence.append(item)

    return evidence


def build_claim_evidence_text(
    claims: list[dict[str, Any]]
) -> str:
    """Format only independently retrieved external article evidence."""
    sections = []

    for index, claim in enumerate(claims, start=1):
        evidence = claim.get("_evidence", [])
        evidence_sections = []

        for evidence_index, item in enumerate(evidence, start=1):
            evidence_sections.append(
                f"""
Evidence {evidence_index}
Evidence type: {item.get('evidence_type', '')}
Publisher: {item.get('source', '')}
Title: {item.get('title', '')}
Published: {item.get('published', '')}
URL: {item.get('url', '')}
Full article content:
{item.get('text', '')[:2500]}
"""
            )

        evidence_text = (
            "\\n".join(evidence_sections)
            if evidence_sections
            else "No usable full-text external evidence was found."
        )

        sections.append(
            f"""
==================================================
CLAIM {index}
==================================================
Claim:
{claim.get('claim', '')}
Type:
{claim.get('type', '')}
Entities:
{json.dumps(claim.get('entities', []))}
Independent external evidence:
{evidence_text}
"""
        )

    return "\\n".join(sections)


def build_verification_prompt(
    claims_text: str
) -> str:
    """Create the detailed claim verification prompt."""
    return f"""
You are the factual claim verification component of
a news auditing system.

Verify each claim using ONLY the external evidence provided below.

Do not use your general world knowledge as evidence.

IMPORTANT RULES:

1. SUPPORTED means the evidence directly confirms the main
   factual claim and its important details.

2. PARTIALLY_SUPPORTED means the evidence confirms some
   important parts of the claim, but does not confirm all
   of its details.

   For PARTIALLY_SUPPORTED, explain:
   - what part is supported
   - what part is not confirmed
   - which evidence supports the conclusion

3. UNSUPPORTED means the available evidence does not provide
   sufficient support for the claim.

4. CONTRADICTED means reliable evidence directly conflicts
   with the claim.

5. UNVERIFIED means there is not enough usable evidence to
   determine whether the claim is supported.

6. Lack of evidence does NOT automatically mean false.

7. A Google News or Bing RSS headline/snippet alone is NOT evidence; use only the retrieved full article text.

8. Do not treat the submitted article, a copied/syndicated version of it, or repeated versions of the same report as independent corroboration.

9. Do not invent facts.

10. Do not assume multiple articles repeating the same statement
    independently verify it.

11. Compare exact details such as people, places, dates,
    numbers, organizations, events, relationships, and actions.

12. The reason MUST be specific to the individual claim.

13. Do NOT use the same generic reason for multiple claims
    when the evidence differs.

14. The reason should normally be 1-3 sentences.

15. For PARTIALLY_SUPPORTED claims, explicitly identify both
    the supported portion and the missing or unconfirmed portion.

16. For SUPPORTED claims, explain which evidence directly
    confirms the important details.

17. For UNSUPPORTED claims, explain why the provided evidence
    does not establish the claim.

18. For CONTRADICTED claims, explain the specific conflict.

19. For UNVERIFIED claims, explain that the available evidence
    was insufficient and do not call the claim false.

Return ONLY valid JSON.

Use exactly this structure:

{{
  "verifications": [
    {{
      "claim_index": 1,
      "status": "SUPPORTED",
      "reason": "Detailed claim-specific explanation.",
      "supporting_sources": [
        "Publisher name"
      ]
    }}
  ]
}}

Allowed statuses:

SUPPORTED
PARTIALLY_SUPPORTED
UNSUPPORTED
CONTRADICTED
UNVERIFIED

CLAIMS AND EVIDENCE:

{claims_text}
"""
def normalize_url(url: str) -> str:
    """Normalize URLs for comparison while preserving meaningful query IDs."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    port = parsed.port
    netloc = hostname
    if port and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
        netloc = f"{hostname}:{port}"

    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/")
    # Treat common AMP URL variants as the same publisher article.
    path = re.sub(r"/(amp|amphtml)$", "", path, flags=re.IGNORECASE)
    ignored_parameters = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "utm_id", "utm_name", "fbclid", "gclid", "dclid", "mc_cid", "mc_eid",
        "ref", "ref_src", "ocid", "cmpid", "output", "amp",
    }
    query = parse_qs(parsed.query, keep_blank_values=True)
    filtered_query = {
        key: values for key, values in query.items()
        if key.lower() not in ignored_parameters
    }
    normalized_query = urlencode(sorted(filtered_query.items()), doseq=True)
    return urlunparse((parsed.scheme.lower() or "https", netloc, path, "", normalized_query, ""))


def verify_claims(
    claims: list[dict[str, Any]],
    original_url: str,
    original_article: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Search for independent evidence and verify only claims with evidence."""
    if not claims:
        return []

    prepared_claims: list[dict[str, Any]] = []
    evidence_cache: dict[str, dict[str, Any] | None] = {}
    for index, claim in enumerate(claims, start=1):
        logging.info("Searching external evidence for claim %s.", index)
        articles = search_news(claim, original_url=original_url)
        evidence = collect_evidence(
            articles,
            original_url,
            original_article,
            evidence_cache=evidence_cache,
        )
        claim_copy = dict(claim)
        claim_copy["_evidence"] = evidence
        prepared_claims.append(claim_copy)
        logging.info(
            "Usable independent full-text sources for claim %s: %s",
            index, len(evidence),
        )

    # Only claims with actual external article text are sent to the verifier.
    # This prevents the model from generating verdicts from the claim alone.
    evidence_claims = [
        (index, claim) for index, claim in enumerate(prepared_claims, start=1)
        if claim.get("_evidence")
    ]
    all_verifications: dict[int, dict[str, Any]] = {}
    # Keep evidence prompts below Groq's token-per-minute request limit so a
    # large batch does not incur a slower provider fallback.
    batch_size = 3

    for batch_start in range(0, len(evidence_claims), batch_size):
        indexed_batch = evidence_claims[batch_start:batch_start + batch_size]
        batch_claims = [claim for _, claim in indexed_batch]
        claims_text = build_claim_evidence_text(batch_claims)
        try:
            with timed_stage(
                "ai_verification",
                claims=f"{indexed_batch[0][0]}-{indexed_batch[-1][0]}",
            ):
                raw = ask_ai(
                    build_verification_prompt(claims_text),
                    purpose=(
                        f"batch claim verification ({indexed_batch[0][0]}-"
                        f"{indexed_batch[-1][0]})"
                    ),
                )
            data = extract_json(raw)
            if isinstance(data, dict):
                verifications = data.get("verifications", [])
            elif isinstance(data, list):
                verifications = data
            else:
                verifications = []
        except Exception as error:
            logging.error(
                "Batch verification failed for claims %s-%s: %s",
                indexed_batch[0][0], indexed_batch[-1][0], error,
            )
            verifications = []

        for item in verifications:
            if not isinstance(item, dict):
                continue
            try:
                local_index = int(item.get("claim_index"))
            except (TypeError, ValueError):
                continue
            if 1 <= local_index <= len(indexed_batch):
                global_index = indexed_batch[local_index - 1][0]
                all_verifications[global_index] = item

    verified_claims: list[dict[str, Any]] = []
    for index, claim in enumerate(prepared_claims, start=1):
        evidence = claim.pop("_evidence", [])
        verification = all_verifications.get(index, {})
        status = str(verification.get("status", "UNVERIFIED")).upper().strip()
        if status not in ALLOWED_STATUSES:
            status = "UNVERIFIED"

        if not evidence:
            status = "UNVERIFIED"
            reason = (
                "No usable independent full-text external sources were found "
                "for this claim. This does not mean the claim is false."
            )
        else:
            reason = str(verification.get("reason", "")).strip()
            if not reason:
                status = "UNVERIFIED"
                reason = "The retrieved evidence was not sufficient to produce a reliable verdict."

        # Always derive the source list from pages that were actually retrieved.
        supporting_sources = [
            item.get("source") or item.get("title") or "External publisher"
            for item in evidence
        ]
        verification_result = {
            "claim_index": index,
            "status": status,
            "reason": reason,
            "supporting_sources": supporting_sources,
        }
        claim.update({
            "verification": verification_result,
            "verification_result": status,
            "verification_reason": reason,
            "evidence_count": len(evidence),
            "evidence_sources": [
                {
                    "title": item.get("title", ""),
                    "source": item.get("source", ""),
                    "published": item.get("published", ""),
                    "url": item.get("url", ""),
                    "evidence_type": item.get("evidence_type", ""),
                }
                for item in evidence
            ],
        })
        verified_claims.append(claim)

    return verified_claims


def calculate_score(
    claims: list[dict[str, Any]]
) -> float | None:
    """Calculate the trust score only when enough evidence exists."""
    if not claims:
        return None

    scored_claims = [
        (
            claim.get("verification_result"),
            1.25 if len(
                claim.get("claim", "")
            ) > 150 else 1.0
        )
        for claim in claims
        if claim.get("verification_result") in STATUS_SCORES
    ]

    coverage = len(scored_claims) / len(claims)

    logging.info(
        f"Evidence coverage: "
        f"{len(scored_claims)}/{len(claims)} "
        f"({coverage:.0%})"
    )

    if coverage < MINIMUM_EVIDENCE_COVERAGE:
        logging.warning(
            "Insufficient evidence for trust score."
        )
        return None

    weighted_total = sum(
        STATUS_SCORES[status] * weight
        for status, weight in scored_claims
    )

    total_weight = sum(
        weight
        for _, weight in scored_claims
    )

    return round(
        weighted_total / total_weight,
        1
    ) if total_weight else None


def analyze_headline(
    headline: str,
    article_text: str
) -> dict[str, Any]:
    """Analyze headline bias, misleading presentation, and sensationalism."""
    if not headline:
        return {
            "headline": "",
            "bias": {
                "status": "NO_CLEAR_INDICATORS",
                "reason": ""
            },
            "misleading": {
                "status": "NO_CLEAR_INDICATORS",
                "reason": ""
            },
            "sensationalism": {
                "status": "LOW",
                "reason": ""
            }
        }

    prompt = f"""
You are analyzing a news headline for an auditing system.

Analyze the headline only in relation to the article content.

Do NOT determine whether the article is true or false.

Do NOT accuse the publisher of intentional deception.

Do NOT infer political affiliation.

Look for:

1. Potential bias
2. Potentially misleading presentation
3. Sensationalism

A single negative or positive word does not automatically mean
the headline is biased.

Return ONLY valid JSON.

Use this exact structure:

{{
  "headline": "{headline}",
  "bias": {{
    "status": "NO_CLEAR_INDICATORS",
    "reason": "Short explanation."
  }},
  "misleading": {{
    "status": "NO_CLEAR_INDICATORS",
    "reason": "Short explanation."
  }},
  "sensationalism": {{
    "status": "LOW",
    "reason": "Short explanation."
  }}
}}

Allowed bias statuses:

NO_CLEAR_INDICATORS
POTENTIAL_BIAS

Allowed misleading statuses:

NO_CLEAR_INDICATORS
POTENTIALLY_MISLEADING

Allowed sensationalism:

LOW
MODERATE
HIGH

HEADLINE:

{headline}

ARTICLE:

{article_text[:12000]}
"""

    try:
        with timed_stage("headline_analysis"):
            raw = ask_ai(
                prompt,
                purpose="headline analysis"
            )

        data = extract_json(raw)

        if not isinstance(data, dict):
            raise ValueError(
                "Invalid headline response."
            )

        return data

    except Exception as error:
        logging.error(
            f"Headline analysis error: {error}"
        )

        return {
            "headline": headline,
            "bias": {
                "status": "NO_CLEAR_INDICATORS",
                "reason": "Headline analysis was unavailable."
            },
            "misleading": {
                "status": "NO_CLEAR_INDICATORS",
                "reason": "Headline analysis was unavailable."
            },
            "sensationalism": {
                "status": "LOW",
                "reason": "Headline analysis was unavailable."
            }
        }


def generate_explanation(
    score: float | None,
    claims: list[dict[str, Any]]
) -> str:
    """Generate the overall explanation shown above claim details."""
    counts = Counter(
        claim.get(
            "verification_result",
            "UNVERIFIED"
        )
        for claim in claims
    )

    supported = counts.get("SUPPORTED", 0)
    partially_supported = counts.get(
        "PARTIALLY_SUPPORTED",
        0
    )
    unsupported = counts.get("UNSUPPORTED", 0)
    contradicted = counts.get("CONTRADICTED", 0)
    unverified = counts.get("UNVERIFIED", 0)

    if score is None:
        return (
            "The system could not calculate a trust score because "
            "there was not enough usable external evidence to "
            "verify a sufficient number of the article's claims. "
            f"{supported} claims were supported, "
            f"{partially_supported} were partially supported, "
            f"{unsupported} were unsupported, "
            f"{contradicted} were contradicted, and "
            f"{unverified} could not be verified."
        )

    prompt = f"""
Write a short, neutral explanation of the article audit.

Do not use Markdown, headings, bullet points, bold text, or ALL CAPS.

Do not say that an UNVERIFIED claim is false.

Explain that the trust score reflects how well the independently
checkable claims were supported by the available external evidence.

The detailed explanation for each individual claim is displayed
separately, so summarize the overall result without repeating
every claim.

Trust score: {score}/100

Supported claims: {supported}
Partially supported claims: {partially_supported}
Unsupported claims: {unsupported}
Contradicted claims: {contradicted}
Unverified claims: {unverified}

Return only the explanation.
"""

    try:
        with timed_stage("score_explanation"):
            return ask_ai(
                prompt,
                purpose="trust score explanation"
            ).strip()

    except Exception as error:
        logging.error(
            f"Explanation error: {error}"
        )

        return (
            f"The article received a trust score of {score}/100 "
            "based on the available external evidence. "
            f"{supported} claims were supported, "
            f"{partially_supported} were partially supported, "
            f"{unsupported} were unsupported, "
            f"{contradicted} were contradicted, and "
            f"{unverified} could not be verified."
        )


def audit_article(
    url: str
) -> dict[str, Any]:
    """Run the complete article auditing pipeline."""
    audit_started = perf_counter()
    logging.info("Starting article audit using bounded RSS search (no DuckDuckGo/Bing HTML scraping).")

    article = fetch_article(url)

    article_text = article["text"]
    headline = article["headline"]

    logging.info(
        f"Article text length: {len(article_text)} characters."
    )

    claims = extract_claims(article_text)

    logging.info(
        f"Claims extracted: {len(claims)}."
    )

    if not claims:
        raise RuntimeError(
            "No factual claims could be extracted from the article."
        )

    verified_claims = verify_claims(
        claims,
        original_url=url,
        original_article=article,
    )

    score_started = perf_counter()
    score = calculate_score(verified_claims)
    logging.info(
        "TIMING stage=score_calculation elapsed_seconds=%.3f",
        perf_counter() - score_started,
    )

    verification_status = (
        "SCORED"
        if score is not None
        else "INSUFFICIENT_EVIDENCE"
    )

    headline_analysis = analyze_headline(
        headline,
        article_text
    )

    explanation = generate_explanation(
        score,
        verified_claims
    )

    result = {
        "url": url,
        "headline": headline,
        "trust_score": score,
        "verification_status": verification_status,
        "explanation": explanation,
        "headline_analysis": headline_analysis,
        "extracted_claims": verified_claims,
        "claims": verified_claims
    }
    logging.info(
        "TIMING stage=audit_total elapsed_seconds=%.3f claims=%s evidence_claims=%s score=%s",
        perf_counter() - audit_started,
        len(verified_claims),
        sum(1 for claim in verified_claims if claim.get("evidence_count", 0)),
        score,
    )
    return result


@app.post("/audit")
def audit(
    request: ArticleRequest
) -> dict[str, Any]:
    """FastAPI endpoint used by the frontend."""
    try:
        return audit_article(
            request.url
        )

    except ValueError as error:
        logging.warning("Invalid audit request: %s", error)
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        logging.exception("Audit endpoint error")
        raise HTTPException(
            status_code=500,
            detail=f"Article analysis failed: {error}",
        )


def main() -> None:
    """Start the FastAPI development server."""
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )


if __name__ == "__main__":
    main()
