import json
import logging
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests
import trafilatura
import uvicorn

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from groq import Groq
from pydantic import BaseModel


# ============================================================
# OPTIONAL AI PROVIDERS
# ============================================================

try:
    from google import genai
except ImportError:
    genai = None

try:
    from huggingface_hub import InferenceClient
except ImportError:
    InferenceClient = None


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s"
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
HF_TOKEN = os.getenv("HF_TOKEN")


# ============================================================
# AI MODEL SETTINGS
# ============================================================

GROQ_MODEL = "openai/gpt-oss-120b"

# Current Gemini API model.
GEMINI_MODEL = "gemini-3.8-flash"

# Hugging Face model.
# Hugging Face automatically selects an available inference
# provider when provider="auto" is used.
HF_MODEL = "openai/gpt-oss-120b"


# ============================================================
# HTTP SETTINGS
# ============================================================

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

DEFAULT_TIMEOUT = 15
GOOGLE_NEWS_TIMEOUT = 15

MAX_NEWS_RESULTS = 8
MAX_EVIDENCE_SOURCES = 5

# Do not score an article unless at least this percentage
# of claims received usable external evidence.
MINIMUM_EVIDENCE_COVERAGE = 0.50


# ============================================================
# VERIFICATION STATUS
# ============================================================

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


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="OpenNews Auditor"
)


# ============================================================
# FRONTEND
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

app.mount(
    "/static",
    StaticFiles(directory=str(FRONTEND_DIR)),
    name="static"
)


@app.get("/")
def home():
    return FileResponse(
        str(FRONTEND_DIR / "index.html")
    )


# ============================================================
# AI CLIENTS
# ============================================================

groq_client = None
gemini_client = None
hf_client = None


if GROQ_API_KEY:
    groq_client = Groq(
        api_key=GROQ_API_KEY
    )
    logging.info("Groq API enabled.")
else:
    logging.warning(
        "GROQ_API_KEY not found. Groq disabled."
    )


if GEMINI_API_KEY and genai:
    try:
        gemini_client = genai.Client(
            api_key=GEMINI_API_KEY
        )
        logging.info("Gemini API enabled.")
    except Exception as e:
        logging.warning(
            f"Gemini initialization failed: {e}"
        )


if HF_TOKEN and InferenceClient:
    try:
        hf_client = InferenceClient(
            api_key=HF_TOKEN,
            provider="auto"
        )
        logging.info(
            "Hugging Face API enabled."
        )
    except Exception as e:
        logging.warning(
            f"Hugging Face initialization failed: {e}"
        )


if not any(
    [
        groq_client,
        gemini_client,
        hf_client
    ]
):
    raise ValueError(
        "No AI provider is configured. "
        "Add GROQ_API_KEY, GEMINI_API_KEY, "
        "or HF_TOKEN to the .env file."
    )


# ============================================================
# REQUEST MODEL
# ============================================================

class ArticleRequest(BaseModel):
    url: str


# ============================================================
# GENERAL HELPERS
# ============================================================

def _clean_json_markdown(text: str) -> str:
    """
    Remove Markdown code fences if an AI model returns JSON
    inside ```json ... ```.
    """

    if not text:
        return ""

    text = text.strip()

    text = re.sub(
        r"^```json\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"^```\s*",
        "",
        text
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    return text.strip()


def _extract_json(text: str) -> Any:
    """
    Safely extract JSON from an AI response.
    """

    cleaned = _clean_json_markdown(text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find an object.
    object_match = re.search(
        r"\{.*\}",
        cleaned,
        flags=re.DOTALL
    )

    if object_match:
        try:
            return json.loads(
                object_match.group(0)
            )
        except json.JSONDecodeError:
            pass

    # Try to find an array.
    array_match = re.search(
        r"\[.*\]",
        cleaned,
        flags=re.DOTALL
    )

    if array_match:
        try:
            return json.loads(
                array_match.group(0)
            )
        except json.JSONDecodeError:
            pass

    raise ValueError(
        "The AI response did not contain valid JSON."
    )


# ============================================================
# GROQ
# ============================================================

def ask_groq(prompt: str) -> str:

    if not groq_client:
        raise RuntimeError(
            "Groq is not configured."
        )

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
        raise ValueError(
            "Groq returned an empty response."
        )

    return _clean_json_markdown(result)


# ============================================================
# GEMINI
# ============================================================

def ask_gemini(prompt: str) -> str:

    if not gemini_client:
        raise RuntimeError(
            "Gemini is not configured."
        )

    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt
    )

    result = response.text

    if not result:
        raise ValueError(
            "Gemini returned an empty response."
        )

    return _clean_json_markdown(result)


# ============================================================
# HUGGING FACE
# ============================================================

def ask_huggingface(prompt: str) -> str:

    if not hf_client:
        raise RuntimeError(
            "Hugging Face is not configured."
        )

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

    return _clean_json_markdown(result)


# ============================================================
# UNIVERSAL AI FALLBACK
# ============================================================

def ask_ai(
    prompt: str,
    purpose: str = "AI analysis"
) -> str:
    """
    Try AI providers in this order:

        1. Groq
        2. Gemini
        3. Hugging Face

    If one provider fails, automatically try the next one.
    """

    providers = [
        (
            "Groq",
            ask_groq,
            groq_client
        ),
        (
            "Gemini",
            ask_gemini,
            gemini_client
        ),
        (
            "Hugging Face",
            ask_huggingface,
            hf_client
        ),
    ]

    errors = []

    for provider_name, provider_function, client in providers:

        if not client:
            continue

        try:

            logging.info(
                f"AI provider: {provider_name} "
                f"for {purpose}"
            )

            result = provider_function(
                prompt
            )

            logging.info(
                f"{provider_name} succeeded."
            )

            return result

        except Exception as e:

            error_text = str(e)

            errors.append(
                f"{provider_name}: {error_text}"
            )

            if "429" in error_text:

                logging.warning(
                    f"{provider_name} rate limit reached."
                )

            else:

                logging.warning(
                    f"{provider_name} failed: "
                    f"{error_text}"
                )

            logging.info(
                f"Trying next AI provider..."
            )

    raise RuntimeError(
        f"All AI providers failed for "
        f"{purpose}. "
        f"Errors: {' | '.join(errors)}"
    )


# ============================================================
# ARTICLE EXTRACTION
# ============================================================

def fetch_article(
    url: str
) -> dict[str, str]:

    logging.info(
        f"Fetching article: {url}"
    )

    try:

        response = requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=DEFAULT_TIMEOUT
        )

        response.raise_for_status()

    except requests.RequestException as e:

        raise RuntimeError(
            f"Could not access article URL: {e}"
        )

    html = response.text

    article_text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        include_links=False,
        favor_precision=True
    )

    # Fallback if Trafilatura does not extract enough text.
    if not article_text or len(article_text) < 200:

        logging.warning(
            "Trafilatura extracted little text. "
            "Trying fallback extraction."
        )

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        for tag in soup(
            [
                "script",
                "style",
                "noscript",
                "nav",
                "footer",
                "header"
            ]
        ):
            tag.decompose()

        article_text = soup.get_text(
            " ",
            strip=True
        )

    if not article_text:
        raise RuntimeError(
            "Could not extract article text."
        )

    headline = ""

    try:

        metadata = trafilatura.extract_metadata(
            html
        )

        if metadata:

            headline = (
                getattr(
                    metadata,
                    "title",
                    None
                )
                or ""
            )

    except Exception:
        pass

    if not headline:

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        title_tag = soup.find(
            "title"
        )

        if title_tag:
            headline = title_tag.get_text(
                " ",
                strip=True
            )

    return {
        "text": article_text,
        "headline": headline,
        "url": url
    }


# ============================================================
# CLAIM EXTRACTION
# ============================================================

def extract_claims(
    article_text: str
) -> list[dict[str, Any]]:

    # Limit enormous articles.
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

        raw = ask_ai(
            prompt,
            purpose="claim extraction"
        )

        data = _extract_json(raw)

        if isinstance(data, dict):

            claims = data.get(
                "claims",
                []
            )

        elif isinstance(data, list):

            claims = data

        else:

            claims = []

        if not isinstance(
            claims,
            list
        ):
            claims = []

        return claims[:10]

    except Exception as e:

        logging.error(
            f"Claim extraction error: {e}"
        )

        raise RuntimeError(
            "Could not extract factual claims "
            "from the article."
        )


# ============================================================
# LOCAL SEARCH QUERY GENERATOR
# ============================================================

def generate_search_query(
    claim: dict[str, Any]
) -> str:
    """
    Generate a simple search query locally.

    IMPORTANT:
    This no longer uses an AI call.

    This reduces API usage significantly.
    """

    claim_text = claim.get(
        "claim",
        ""
    )

    if not claim_text:
        return ""

    # Important named entities first.
    entities = claim.get(
        "entities",
        []
    )

    query_parts = []

    if isinstance(
        entities,
        list
    ):

        for entity in entities:

            if not isinstance(
                entity,
                str
            ):
                continue

            entity = entity.strip()

            if entity and entity not in query_parts:

                query_parts.append(
                    entity
                )

    stop_words = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "has",
        "have",
        "had",
        "that",
        "this",
        "these",
        "those",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "from",
        "by",
        "as",
        "after",
        "before",
        "during",
        "into",
        "its",
        "their",
        "it",
        "they",
        "he",
        "she",
        "who",
        "which",
        "that",
        "according",
        "said"
    }

    words = claim_text.split()

    for word in words:

        cleaned = word.strip(
            ".,!?;:()[]{}\"'“”‘’"
        )

        if not cleaned:
            continue

        if cleaned.lower() in stop_words:
            continue

        if len(cleaned) <= 2:
            continue

        if cleaned not in query_parts:

            query_parts.append(
                cleaned
            )

        if len(query_parts) >= 10:
            break

    return " ".join(
        query_parts[:10]
    )


# ============================================================
# GOOGLE NEWS RSS
# ============================================================

def _fetch_google_news_articles(
    query: str
) -> list[dict[str, Any]]:

    print(
        "\nSearching Google News RSS..."
    )

    google_news_url = (
        "https://news.google.com/rss/search"
    )

    params = {
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en"
    }

    try:

        response = requests.get(
            google_news_url,
            params=params,
            headers=DEFAULT_HEADERS,
            timeout=GOOGLE_NEWS_TIMEOUT
        )

        print(
            "Google News RSS HTTP status: "
            f"{response.status_code}"
        )

        response.raise_for_status()

    except requests.RequestException as e:

        logging.error(
            f"Google News RSS request error: {e}"
        )

        return []

    soup = BeautifulSoup(
        response.content,
        "xml"
    )

    results = []
    seen_urls = set()

    items = soup.find_all(
        "item"
    )

    print(
        "Google News articles found: "
        f"{len(items)}"
    )

    for item in items:

        title_tag = item.find(
            "title"
        )

        link_tag = item.find(
            "link"
        )

        description_tag = item.find(
            "description"
        )

        source_tag = item.find(
            "source"
        )

        pub_date_tag = item.find(
            "pubDate"
        )

        if not title_tag or not link_tag:
            continue

        title = title_tag.get_text(
            strip=True
        )

        google_url = link_tag.get_text(
            strip=True
        )

        description = ""

        if description_tag:

            description = BeautifulSoup(
                description_tag.get_text(),
                "html.parser"
            ).get_text(
                " ",
                strip=True
            )

        source_name = ""

        if source_tag:

            source_name = source_tag.get_text(
                strip=True
            )

        published = ""

        if pub_date_tag:

            published = pub_date_tag.get_text(
                strip=True
            )

        if not google_url:
            continue

        if google_url in seen_urls:
            continue

        seen_urls.add(
            google_url
        )

        results.append(
            {
                "title": title,
                "description": description,
                "url": google_url,
                "google_url": google_url,
                "source": source_name,
                "published": published,
                "is_google_news_link": True
            }
        )

        if len(results) >= MAX_NEWS_RESULTS:
            break

    return results


# ============================================================
# NEWS SEARCH
# ============================================================

def search_news(
    claim: dict[str, Any]
) -> list[dict[str, Any]]:

    claim_text = claim.get(
        "claim",
        ""
    )

    print(
        "\nChecking claim: "
        f"{claim_text}"
    )

    search_query = generate_search_query(
        claim
    )

    print(
        "Generated search query: "
        f"{search_query}"
    )

    if not search_query:

        logging.warning(
            "Empty search query."
        )

        return []

    articles = _fetch_google_news_articles(
        search_query
    )

    print(
        "Total unique articles collected: "
        f"{len(articles)}"
    )

    return articles


# ============================================================
# COLLECT EVIDENCE
# ============================================================

def collect_evidence(
    articles: list[dict[str, Any]]
) -> list[dict[str, Any]]:

    evidence = []

    for article in articles:

        if len(evidence) >= MAX_EVIDENCE_SOURCES:
            break

        title = article.get(
            "title",
            ""
        )

        description = article.get(
            "description",
            ""
        )

        url = article.get(
            "url",
            ""
        )

        source_name = article.get(
            "source",
            ""
        )

        # ----------------------------------------------------
        # GOOGLE NEWS RSS RESULT
        # ----------------------------------------------------

        if article.get(
            "is_google_news_link"
        ):

            evidence.append(
                {
                    "title": title,
                    "description": description,
                    "url": url,
                    "source": source_name,
                    "published": article.get(
                        "published",
                        ""
                    ),
                    "text": (
                        f"Headline: {title}\n"
                        f"Publisher: {source_name}\n"
                        f"Published: "
                        f"{article.get('published', '')}\n"
                        f"Description: {description}"
                    ),
                    "evidence_type": (
                        "Google News RSS metadata"
                    )
                }
            )

            continue

        # ----------------------------------------------------
        # NORMAL ARTICLE URL
        # ----------------------------------------------------

        try:

            response = requests.get(
                url,
                headers=DEFAULT_HEADERS,
                timeout=DEFAULT_TIMEOUT
            )

            response.raise_for_status()

            text = trafilatura.extract(
                response.text,
                include_comments=False,
                include_tables=False,
                include_links=False,
                favor_precision=True
            )

            if not text:
                continue

            evidence.append(
                {
                    "title": title,
                    "description": description,
                    "url": url,
                    "source": source_name,
                    "published": article.get(
                        "published",
                        ""
                    ),
                    "text": text[:12000],
                    "evidence_type": "External article"
                }
            )

        except Exception as e:

            logging.warning(
                f"Could not extract evidence "
                f"from {url}: {e}"
            )

    return evidence


# ============================================================
# BATCH CLAIM VERIFICATION
# ============================================================

def verify_claims(
    claims: list[dict[str, Any]]
) -> list[dict[str, Any]]:

    if not claims:
        return []

    prepared_claims = []

    # --------------------------------------------------------
    # SEARCH FOR EVIDENCE
    # --------------------------------------------------------

    for index, claim in enumerate(
        claims,
        start=1
    ):

        print(
            f"\n========== CLAIM {index} =========="
        )

        articles = search_news(
            claim
        )

        evidence = collect_evidence(
            articles
        )

        claim_copy = dict(
            claim
        )

        claim_copy["_evidence"] = evidence

        prepared_claims.append(
            claim_copy
        )

    # --------------------------------------------------------
    # CREATE ONE BATCH PROMPT
    # --------------------------------------------------------

    claim_sections = []

    for index, claim in enumerate(
        prepared_claims,
        start=1
    ):

        evidence = claim.get(
            "_evidence",
            []
        )

        evidence_sections = []

        for evidence_index, item in enumerate(
            evidence,
            start=1
        ):

            evidence_sections.append(
                f"""
Evidence {evidence_index}

Evidence type:
{item.get('evidence_type', '')}

Publisher:
{item.get('source', '')}

Title:
{item.get('title', '')}

Published:
{item.get('published', '')}

URL:
{item.get('url', '')}

Content:
{item.get('text', '')[:7000]}
"""
            )

        if not evidence_sections:

            evidence_text = (
                "No usable external evidence "
                "was found."
            )

        else:

            evidence_text = "\n".join(
                evidence_sections
            )

        claim_sections.append(
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

Evidence from original article:
{claim.get('evidence_in_article', '')}

Source attribution:
{claim.get('source_attribution', '')}

External evidence:
{evidence_text}
"""
        )

    all_claims_text = "\n".join(
        claim_sections
    )

    prompt = f"""
You are the factual claim verification component of
a news auditing system.

Verify each claim using ONLY the external evidence
provided below.

Do not use your general world knowledge as evidence.

IMPORTANT RULES:

1. SUPPORTED means the evidence directly supports the
   specific claim.

2. PARTIALLY_SUPPORTED means only part of the claim
   is supported or important details are missing.

3. UNSUPPORTED means evidence was available but does
   not support the claim.

4. CONTRADICTED means reliable evidence directly
   conflicts with the claim.

5. UNVERIFIED means there is not enough usable evidence
   to determine whether the claim is supported.

6. Lack of evidence does NOT automatically mean false.

7. A Google News RSS headline alone is weak evidence.
   Do not treat a headline as complete proof of a claim.

8. Do not invent facts.

9. Do not assume multiple articles repeating the same
   statement independently verify it.

10. Compare exact details such as:
    - people
    - places
    - dates
    - numbers
    - organizations
    - events
    - relationships
    - actions

Return ONLY valid JSON.

Use exactly this structure:

{{
  "verifications": [
    {{
      "claim_index": 1,
      "status": "SUPPORTED",
      "reason": "Short factual explanation.",
      "supporting_sources": [
        "Publisher name"
      ]
    }}
  ]
}}

Allowed status values:

SUPPORTED
PARTIALLY_SUPPORTED
UNSUPPORTED
CONTRADICTED
UNVERIFIED

CLAIMS AND EVIDENCE:

{all_claims_text}
"""

    # --------------------------------------------------------
    # ASK AI
    # --------------------------------------------------------

    try:

        raw = ask_ai(
            prompt,
            purpose="batch claim verification"
        )

        data = _extract_json(
            raw
        )

        if isinstance(
            data,
            dict
        ):

            verifications = data.get(
                "verifications",
                []
            )

        elif isinstance(
            data,
            list
        ):

            verifications = data

        else:

            verifications = []

    except Exception as e:

        logging.error(
            f"Batch verification error: {e}"
        )

        verifications = []

    # --------------------------------------------------------
    # INDEX AI RESULTS
    # --------------------------------------------------------

    verification_map = {}

    for item in verifications:

        try:

            claim_index = int(
                item.get(
                    "claim_index"
                )
            )

            verification_map[
                claim_index
            ] = item

        except Exception:
            continue

    # --------------------------------------------------------
    # BUILD FINAL CLAIM RESULTS
    # --------------------------------------------------------

    verified_claims = []

    for index, claim in enumerate(
        prepared_claims,
        start=1
    ):

        evidence = claim.pop(
            "_evidence",
            []
        )

        verification = (
            verification_map.get(
                index
            )
        )

        if not verification:

            verification = {
                "claim_index": index,
                "status": (
                    "UNVERIFIED"
                ),
                "reason": (
                    "The AI verification "
                    "service could not "
                    "evaluate this claim."
                ),
                "supporting_sources": []
            }

        status = str(
            verification.get(
                "status",
                "UNVERIFIED"
            )
        ).upper().strip()

        if status not in ALLOWED_STATUSES:

            status = "UNVERIFIED"

        verification["status"] = status

        claim["verification"] = (
            verification
        )

        claim["verification_result"] = (
            status
        )

        claim["evidence_count"] = (
            len(evidence)
        )

        claim["evidence_sources"] = [
            {
                "title": item.get(
                    "title",
                    ""
                ),
                "source": item.get(
                    "source",
                    ""
                ),
                "published": item.get(
                    "published",
                    ""
                ),
                "url": item.get(
                    "url",
                    ""
                ),
                "evidence_type": item.get(
                    "evidence_type",
                    ""
                )
            }
            for item in evidence
        ]

        verified_claims.append(
            claim
        )

    return verified_claims


# ============================================================
# TRUST SCORE
# ============================================================

def calculate_score(
    claims: list[dict[str, Any]]
) -> float | None:

    if not claims:
        return None

    scored_claims = []

    for claim in claims:

        status = claim.get(
            "verification_result",
            "UNVERIFIED"
        )

        if status not in STATUS_SCORES:
            continue

        claim_text = claim.get(
            "claim",
            ""
        )

        # Longer factual claims often contain more
        # independently checkable information.
        weight = (
            1.25
            if len(claim_text) > 150
            else 1.0
        )

        scored_claims.append(
            (
                status,
                weight
            )
        )

    total_claims = len(
        claims
    )

    verified_claim_count = len(
        scored_claims
    )

    evidence_coverage = (
        verified_claim_count
        / total_claims
    )

    logging.info(
        f"Evidence coverage: "
        f"{verified_claim_count}/"
        f"{total_claims} "
        f"({evidence_coverage:.0%})"
    )

    # Not enough external evidence to responsibly
    # calculate a trust score.
    if evidence_coverage < MINIMUM_EVIDENCE_COVERAGE:

        logging.warning(
            "Insufficient evidence for "
            "trust score."
        )

        return None

    weighted_total = 0.0
    total_weight = 0.0

    for status, weight in scored_claims:

        weighted_total += (
            STATUS_SCORES[status]
            * weight
        )

        total_weight += weight

    if total_weight == 0:
        return None

    score = (
        weighted_total
        / total_weight
    )

    return round(
        score,
        1
    )


# ============================================================
# HEADLINE ANALYSIS
# ============================================================

def analyze_headline(
    headline: str,
    article_text: str
) -> dict[str, Any]:

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

    article_excerpt = article_text[:12000]

    prompt = f"""
You are analyzing a news headline for an auditing system.

Analyze the headline only in relation to the article content
provided below.

Do NOT determine whether the article is true or false.

Do NOT accuse the publisher of intentional deception.

Do NOT infer political affiliation.

Look for:

1. Potential bias
   Does the wording frame a person, organization, event,
   or issue in a noticeably positive or negative way?

2. Potentially misleading presentation
   Does the headline omit or distort important context
   compared with the article?

3. Sensationalism
   Does the wording use dramatic, exaggerated, emotional,
   or attention-seeking language?

Important:
A single negative or positive word does not automatically
mean the headline is biased.

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

{article_excerpt}
"""

    try:

        raw = ask_ai(
            prompt,
            purpose="headline analysis"
        )

        data = _extract_json(
            raw
        )

        if not isinstance(
            data,
            dict
        ):
            raise ValueError(
                "Invalid headline response."
            )

        return data

    except Exception as e:

        logging.error(
            f"Headline analysis error: {e}"
        )

        return {
            "headline": headline,
            "bias": {
                "status": "NO_CLEAR_INDICATORS",
                "reason": (
                    "Headline analysis "
                    "was unavailable."
                )
            },
            "misleading": {
                "status": "NO_CLEAR_INDICATORS",
                "reason": (
                    "Headline analysis "
                    "was unavailable."
                )
            },
            "sensationalism": {
                "status": "LOW",
                "reason": (
                    "Headline analysis "
                    "was unavailable."
                )
            }
        }


# ============================================================
# EXPLANATION
# ============================================================

def generate_explanation(
    score: float | None,
    claims: list[dict[str, Any]]
) -> str:

    counts = Counter(
        claim.get(
            "verification_result",
            "UNVERIFIED"
        )
        for claim in claims
    )

    supported = counts.get(
        "SUPPORTED",
        0
    )

    partially_supported = counts.get(
        "PARTIALLY_SUPPORTED",
        0
    )

    unsupported = counts.get(
        "UNSUPPORTED",
        0
    )

    contradicted = counts.get(
        "CONTRADICTED",
        0
    )

    unverified = counts.get(
        "UNVERIFIED",
        0
    )

    summary = f"""
Supported claims: {supported}
Partially supported claims: {partially_supported}
Unsupported claims: {unsupported}
Contradicted claims: {contradicted}
Unverified claims: {unverified}
"""

    if score is None:

        return (
            "The system could not calculate a trust "
            "score because there was not enough usable "
            "external evidence to verify a sufficient "
            "number of the article's claims."
            f"{summary}"
        )

    prompt = f"""
Write a short, neutral explanation of the article audit.

Do not use Markdown.

Do not use:
- bold
- headings
- bullet points
- ALL CAPS

Do not say that an UNVERIFIED claim is false.

Explain that the trust score reflects how well the
independently checkable claims were supported by the
available external evidence.

Trust score:
{score}/100

Claim results:
{summary}

Return only the explanation.
"""

    try:

        result = ask_ai(
            prompt,
            purpose="trust score explanation"
        )

        return result.strip()

    except Exception as e:

        logging.error(
            f"Explanation error: {e}"
        )

        return (
            f"The article received a trust score of "
            f"{score}/100 based on the available "
            f"external evidence. "
            f"{supported} claims were supported, "
            f"{partially_supported} were partially "
            f"supported, {unsupported} were unsupported, "
            f"{contradicted} were contradicted, and "
            f"{unverified} could not be verified."
        )


# ============================================================
# FULL ARTICLE AUDIT
# ============================================================

def audit_article(
    url: str
) -> dict[str, Any]:

    logging.info(
        "=========================================="
    )

    logging.info(
        "Starting article audit"
    )

    logging.info(
        "=========================================="
    )

    # --------------------------------------------------------
    # 1. FETCH ARTICLE
    # --------------------------------------------------------

    article = fetch_article(
        url
    )

    article_text = article[
        "text"
    ]

    headline = article[
        "headline"
    ]

    logging.info(
        f"Article text length: "
        f"{len(article_text)} characters"
    )

    logging.info(
        f"Headline: {headline}"
    )

    # --------------------------------------------------------
    # 2. EXTRACT CLAIMS
    # --------------------------------------------------------

    claims = extract_claims(
        article_text
    )

    logging.info(
        f"Claims extracted: "
        f"{len(claims)}"
    )

    if not claims:

        raise RuntimeError(
            "No factual claims could be extracted "
            "from the article."
        )

    # --------------------------------------------------------
    # 3. SEARCH + VERIFY CLAIMS
    # --------------------------------------------------------

    verified_claims = verify_claims(
        claims
    )

    # --------------------------------------------------------
    # 4. CALCULATE TRUST SCORE
    # --------------------------------------------------------

    score = calculate_score(
        verified_claims
    )

    if score is None:

        verification_status = (
            "INSUFFICIENT_EVIDENCE"
        )

    else:

        verification_status = (
            "SCORED"
        )

    # --------------------------------------------------------
    # 5. HEADLINE ANALYSIS
    # --------------------------------------------------------

    headline_analysis = analyze_headline(
        headline,
        article_text
    )

    # --------------------------------------------------------
    # 6. EXPLANATION
    # --------------------------------------------------------

    explanation = generate_explanation(
        score,
        verified_claims
    )

    # --------------------------------------------------------
    # 7. RESPONSE
    # --------------------------------------------------------

    return {
        "url": url,
        "headline": headline,
        "trust_score": score,
        "verification_status": (
            verification_status
        ),
        "explanation": explanation,
        "headline_analysis": (
            headline_analysis
        ),
        "extracted_claims": (
            verified_claims
        ),
        "claims": verified_claims
    }


# ============================================================
# API ENDPOINT
# ============================================================

@app.post("/audit")
def audit(
    request: ArticleRequest
) -> dict[str, Any]:

    try:

        return audit_article(
            request.url
        )

    except Exception as e:

        logging.error(
            f"Audit endpoint error: {e}"
        )

        return {
            "error": (
                f"Article analysis failed: "
                f"{str(e)}"
            )
        }


# ============================================================
# SERVER
# ============================================================

if __name__ == "__main__":

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
