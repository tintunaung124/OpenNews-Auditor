import json
import logging
import os
import re
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

DEFAULT_TIMEOUT = 15
GOOGLE_NEWS_TIMEOUT = 15
MAX_NEWS_RESULTS = 8
MAX_EVIDENCE_SOURCES = 5
MINIMUM_EVIDENCE_COVERAGE = 0.50


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
            groq_client = Groq(api_key=GROQ_API_KEY)
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
        raise ValueError(
            "No AI provider is configured. "
            "Add GROQ_API_KEY, GEMINI_API_KEY, or HF_TOKEN "
            "to the .env file."
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

            result = provider_function(prompt)

            logging.info(
                f"{provider_name} succeeded."
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
def fetch_url(url: str) -> str:
    try:
        response = requests.get(
            url,headers=DEFAULT_HEADERS,timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return response.text
    except requests.RequestException as error:
        raise RuntimeError(
            f"Could not access article URL: {error}"
        )

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
    html = fetch_url(url)
    return {
        "text": extract_article_text(html),"headline": extract_headline(html),
        "url": url}

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
    claim: dict[str, Any]
) -> str:
    """Build a search query locally without another AI call."""
    claim_text = claim.get("claim", "")

    if not claim_text:
        return ""

    query_parts = []

    entities = claim.get("entities", [])

    if isinstance(entities, list):
        query_parts.extend(
            entity.strip()
            for entity in entities
            if isinstance(entity, str)
            and entity.strip()
        )

    stop_words = {
        "the", "a", "an", "is", "are", "was", "were",
        "be", "been", "being", "has", "have", "had",
        "that", "this", "these", "those", "and", "or",
        "but", "of", "to", "in", "on", "for", "with",
        "from", "by", "as", "after", "before", "during",
        "into", "its", "their", "it", "they", "he",
        "she", "who", "which", "according", "said"
    }

    words = claim_text.split()

    for word in words:
        cleaned = word.strip(
            ".,!?;:()[]{}\\\"'“”‘’"
        )

        if not cleaned:
            continue

        if cleaned.lower() in stop_words:
            continue

        if len(cleaned) <= 2:
            continue

        if cleaned not in query_parts:
            query_parts.append(cleaned)

        if len(query_parts) >= 10:
            break

    return " ".join(query_parts[:10])


def fetch_google_news(query: str) -> list[dict[str, Any]]:
    """Search Google News RSS for external reports."""
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

        response.raise_for_status()

    except requests.RequestException as error:
        logging.error(
            f"Google News RSS request error: {error}"
        )
        return []

    soup = BeautifulSoup(
        response.content,
        "xml"
    )

    results = []
    seen_urls = set()

    for item in soup.find_all("item"):
        title_tag = item.find("title")
        link_tag = item.find("link")

        if not title_tag or not link_tag:
            continue

        google_url = link_tag.get_text(strip=True)

        if not google_url or google_url in seen_urls:
            continue

        seen_urls.add(google_url)

        description_tag = item.find("description")
        source_tag = item.find("source")
        published_tag = item.find("pubDate")

        description = ""

        if description_tag:
            description = BeautifulSoup(
                description_tag.get_text(),
                "html.parser"
            ).get_text(" ", strip=True)

        source = (
            source_tag.get_text(strip=True)
            if source_tag
            else ""
        )

        published = (
            published_tag.get_text(strip=True)
            if published_tag
            else ""
        )

        results.append(
            {
                "title": title_tag.get_text(strip=True),
                "description": description,
                "url": google_url,
                "google_url": google_url,
                "source": source,
                "published": published,
                "is_google_news_link": True
            }
        )

        if len(results) >= MAX_NEWS_RESULTS:
            break

    return results


def search_news(
    claim: dict[str, Any]
) -> list[dict[str, Any]]:
    """Search external news reports for one claim."""
    claim_text = claim.get("claim", "")

    logging.info(
        f"Checking claim: {claim_text}"
    )

    search_query = generate_search_query(claim)

    logging.info(
        f"Generated search query: {search_query}"
    )

    if not search_query:
        return []

    articles = fetch_google_news(search_query)

    logging.info(
        f"Articles collected: {len(articles)}"
    )

    return articles


def build_google_news_evidence(
    article: dict[str, Any]
) -> dict[str, Any]:
    """Convert Google News metadata into evidence."""
    title = article.get("title", "")
    source = article.get("source", "")
    published = article.get("published", "")
    description = article.get("description", "")

    return {
        "title": title,
        "description": description,
        "url": article.get("url", ""),
        "source": source,
        "published": published,
        "text": (
            f"Headline: {title}\n"
            f"Publisher: {source}\n"
            f"Published: {published}\n"
            f"Description: {description}"
        ),
        "evidence_type": "Google News RSS metadata"
    }


def build_article_evidence(
    article: dict[str, Any]
) -> dict[str, Any] | None:
    """Download and extract evidence from a normal article."""
    url = article.get("url", "")

    if not url:
        return None

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
            return None

        return {
            "title": article.get("title", ""),
            "description": article.get("description", ""),
            "url": url,
            "source": article.get("source", ""),
            "published": article.get("published", ""),
            "text": text[:12000],
            "evidence_type": "External article"
        }

    except requests.RequestException as error:
        logging.warning(
            f"Could not download evidence from {url}: {error}"
        )
        return None


def collect_evidence(
    articles: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Collect usable evidence from search results."""
    evidence = []

    for article in articles:
        if len(evidence) >= MAX_EVIDENCE_SOURCES:
            break

        if article.get("is_google_news_link"):
            evidence.append(
                build_google_news_evidence(article)
            )
            continue

        article_evidence = build_article_evidence(article)

        if article_evidence:
            evidence.append(article_evidence)

    return evidence


def build_claim_evidence_text(
    claims: list[dict[str, Any]]
) -> str:
    """Format all claims and evidence for the batch AI request."""
    sections = []

    for index, claim in enumerate(claims, start=1):
        evidence = claim.get("_evidence", [])

        evidence_sections = [
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
{item.get('text', '')[:1000]}
"""
            for evidence_index, item in enumerate(
                evidence,
                start=1
            )
        ]

        evidence_text = (
            "\n".join(evidence_sections)
            if evidence_sections
            else "No usable external evidence was found."
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

Evidence from original article:
{claim.get('evidence_in_article', '')}

Source attribution:
{claim.get('source_attribution', '')}

External evidence:
{evidence_text}
"""
        )

    return "\n".join(sections)


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

7. A Google News RSS headline alone is weak evidence.

8. Do not invent facts.

9. Do not assume multiple articles repeating the same statement
   independently verify it.

10. Compare exact details such as people, places, dates,
    numbers, organizations, events, relationships, and actions.

11. The reason MUST be specific to the individual claim.

12. Do NOT use the same generic reason for multiple claims
    when the evidence differs.

13. The reason should normally be 1-3 sentences.

14. For PARTIALLY_SUPPORTED claims, explicitly identify both
    the supported portion and the missing or unconfirmed portion.

15. For SUPPORTED claims, explain which evidence directly
    confirms the important details.

16. For UNSUPPORTED claims, explain why the provided evidence
    does not establish the claim.

17. For CONTRADICTED claims, explain the specific conflict.

18. For UNVERIFIED claims, explain that the available evidence
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


def verify_claims(
    claims: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Search for evidence and verify claims in small AI batches."""
    if not claims:
        return []

    prepared_claims = []

    for index, claim in enumerate(claims, start=1):
        logging.info(f"Searching evidence for claim {index}.")

        articles = search_news(claim)
        evidence = collect_evidence(articles)

        claim_copy = dict(claim)
        claim_copy["_evidence"] = evidence
        prepared_claims.append(claim_copy)

    # Keep verification requests small enough for the AI provider limits.
    batch_size = 3
    all_verifications = {}

    for batch_start in range(0, len(prepared_claims), batch_size):
        batch = prepared_claims[
            batch_start:batch_start + batch_size
        ]

        first_claim_number = batch_start + 1
        last_claim_number = batch_start + len(batch)

        logging.info(
            f"Verifying claims {first_claim_number}-{last_claim_number}."
        )

        claims_text = build_claim_evidence_text(batch)

        try:
            raw = ask_ai(
                build_verification_prompt(claims_text),
                purpose=(
                    f"batch claim verification "
                    f"({first_claim_number}-{last_claim_number})"
                )
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
                f"Batch verification error for claims "
                f"{first_claim_number}-{last_claim_number}: {error}"
            )
            verifications = []

        # AI claim indexes are local to the current batch. Convert them
        # back to the original claim indexes before combining the results.
        for item in verifications:
            try:
                local_index = int(item.get("claim_index"))

                if not 1 <= local_index <= len(batch):
                    continue

                global_index = batch_start + local_index
                all_verifications[global_index] = item

            except (TypeError, ValueError, AttributeError):
                continue

    verified_claims = []

    for index, claim in enumerate(
        prepared_claims,
        start=1
    ):
        evidence = claim.pop("_evidence", [])

        verification = all_verifications.get(
            index,
            {
                "claim_index": index,
                "status": "UNVERIFIED",
                "reason": (
                    "The AI verification service "
                    "could not evaluate this claim."
                ),
                "supporting_sources": []
            }
        )

        status = str(
            verification.get(
                "status",
                "UNVERIFIED"
            )
        ).upper().strip()

        if status not in ALLOWED_STATUSES:
            status = "UNVERIFIED"

        reason = str(
            verification.get(
                "reason",
                ""
            )
        ).strip()

        if not reason:
            reason = (
                "No detailed explanation was "
                "provided for this verification result."
            )

        supporting_sources = verification.get(
            "supporting_sources",
            []
        )

        if not isinstance(supporting_sources, list):
            supporting_sources = []

        verification.update(
            {
                "claim_index": index,
                "status": status,
                "reason": reason,
                "supporting_sources": supporting_sources
            }
        )

        claim.update(
            {
                "verification": verification,
                "verification_result": status,
                "verification_reason": reason,
                "evidence_count": len(evidence),
                "evidence_sources": [
                    {
                        "title": item.get("title", ""),
                        "source": item.get("source", ""),
                        "published": item.get("published", ""),
                        "url": item.get("url", ""),
                        "evidence_type": item.get(
                            "evidence_type",
                            ""
                        )
                    }
                    for item in evidence
                ]
            }
        )

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
    logging.info("Starting article audit.")

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

    verified_claims = verify_claims(claims)

    score = calculate_score(
        verified_claims
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

    return {
        "url": url,
        "headline": headline,
        "trust_score": score,
        "verification_status": verification_status,
        "explanation": explanation,
        "headline_analysis": headline_analysis,
        "extracted_claims": verified_claims,
        "claims": verified_claims
    }


@app.post("/audit")
def audit(
    request: ArticleRequest
) -> dict[str, Any]:
    """FastAPI endpoint used by the frontend."""
    try:
        return audit_article(
            request.url
        )

    except Exception as error:
        logging.error(
            f"Audit endpoint error: {error}"
        )

        return {
            "error": f"Article analysis failed: {error}"
        }


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