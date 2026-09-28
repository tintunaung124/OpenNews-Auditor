from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from trafilatura import fetch_url, extract
from groq import Groq
from dotenv import load_dotenv

import os
import json


# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()


# ============================================================
# ARTICLE REQUEST
# ============================================================

class ArticleRequest(BaseModel):
    url: str


# ============================================================
# OPENNEWS AUDITOR
# ============================================================

class OpenNewsAuditor:

    def __init__(self):

        self.client = Groq(
            api_key=os.getenv("GROQ_API_KEY")
        )


    # ========================================================
    # FETCH ARTICLE
    # ========================================================

    def fetch_article(self, url):

        downloaded = fetch_url(url)

        if downloaded is None:
            return None

        return downloaded


    # ========================================================
    # EXTRACT ARTICLE TEXT
    # ========================================================

    def extract_article(self, downloaded):

        article = extract(downloaded)

        if article is None:
            return None

        return article


    # ========================================================
    # ANALYZE ARTICLE
    # ========================================================

    def analyze_article(self, article):

        prompt = f"""

PART 1: Main Claim Extraction

Extract ONLY the main factual claims from the article.

Follow these rules strictly:

1. Extract individual factual claims, NOT the article itself.

2. Each claim must be ONE concise sentence.

3. Extract only claims that can be independently verified using external evidence.

4. Do NOT copy paragraphs, sections, introductions, background information, or the full article.

5. Do NOT summarize the article.

6. Do NOT include personal stories unless they contain a specific independently verifiable factual claim.

7. Do NOT include opinions, emotions, predictions, rhetorical statements, or general descriptions.

8. Do NOT include the same claim more than once.

9. Focus on important claims involving:

   - specific people
   - organizations
   - laws or policies
   - events
   - dates
   - numbers/statistics
   - government actions
   - diplomatic actions
   - deportations or other documented actions

10. Prefer specific claims over vague statements.

11. Extract approximately 5-10 of the most important factual claims.

12. Never return the full article text as a claim.

13. The "claim" field must contain ONLY one individual factual claim, not multiple claims or paragraphs.

14. Keep each claim short and close to the meaning of the original article.

15. If the article contains fewer than 5 meaningful factual claims, extract only the claims that actually exist. Do not invent claims.

16. Do NOT assume that a claim is true simply because the article states it.

17. Preserve the meaning of the original claim without adding information that is not present in the article.

18. Separate different factual claims into separate entries whenever they can be independently verified.

For every extracted claim, return:

- Type
- Entities involved
- Evidence provided in the article
- Source Attribution type
- Whether Verification is Required

The purpose of this section is to identify the specific factual claims that will later be assessed for truthfulness and evidentiary support.


PART 2: Trust Assessment

Assess the overall factual reliability of the article based primarily on how TRUE and WELL-SUPPORTED its factual claims are.

The trust score MUST represent the degree to which the article's important factual claims are supported by reliable evidence.

When assessing the article:

1. Consider whether the factual claims are supported by evidence provided in the article.

2. Consider whether the article attributes claims to specific and identifiable sources.

3. Consider whether the claims are consistent with reliable, established facts and known information.

4. Identify claims that appear unsupported, misleading, exaggerated, internally inconsistent, or contradicted by reliable evidence.

5. Distinguish between:

   - Supported claims
   - Unsupported claims
   - Contradicted claims
   - Claims that cannot currently be verified

6. Do NOT assume that a claim is true merely because it is presented confidently or written in a professional style.

7. Do NOT assume that a claim is false merely because the article does not provide enough evidence.

8. If a claim cannot be verified with the available information, treat it as UNVERIFIED rather than automatically TRUE or FALSE.

9. Give greater importance to major factual claims than minor details.

10. Do not allow opinions, emotional language, writing quality, or political agreement/disagreement to determine the trust score unless they directly affect the factual reliability of the claims.

11. Do not reward an article simply for having many citations. Consider the quality and relevance of the evidence.

12. Do not penalize an article simply because it contains opinions, as long as those opinions are clearly presented as opinions and are not presented as factual claims.

13. The trust score should decrease when important claims are unsupported or contradicted by reliable evidence.

14. The trust score should increase when important claims are supported by specific, relevant, and reliable evidence.

15. The score should reflect the article as a whole, not just one individual claim.


TRUST SCORE:

Provide a numeric trust score from 0 to 100.

The score represents the overall factual reliability of the article's claims:

- 90-100: The important factual claims are strongly supported by reliable evidence, with little meaningful uncertainty.

- 70-89: Most important factual claims are supported, but there may be some minor unsupported, uncertain, or questionable claims.

- 50-69: The article contains a mixture of supported, unsupported, and/or unverified claims, creating significant uncertainty about its overall factual reliability.

- 30-49: Multiple important claims are poorly supported, misleading, or contradicted by reliable evidence.

- 0-29: Most important claims lack reliable support or are substantially contradicted by reliable evidence.


IMPORTANT:

The trust score is NOT a measure of:

- how persuasive the article is
- how professional the writing looks
- whether the article agrees with a particular political position
- whether the article contains emotional language
- whether the article is popular
- whether the article comes from a familiar publication

The trust score IS primarily a measure of:

- how true the factual claims appear to be
- how well those claims are supported by evidence
- how reliable the article's sources are
- whether important claims are contradicted by reliable evidence
- how much uncertainty remains around the article's factual claims

Provide a 2-3 sentence explanation of the overall trust score. The explanation must specifically mention the main reasons for the score, such as supported claims, unsupported claims, contradictions, or significant uncertainty.


OUTPUT FORMAT:

Your output MUST be a valid JSON object. Do not include markdown formatting, backticks, or extra text.

Use this exact JSON structure:

{{
    "llm_score": 85,

    "explanation": "The article's main factual claims are generally well-supported by identifiable evidence, although some claims remain insufficiently supported or unverified. The overall score reflects the balance between supported claims and the level of uncertainty surrounding the remaining claims.",

    "extracted_claims": [

        {{
            "claim": "The exact individual factual statement.",

            "type": "Fact",

            "entities": [
                "Entity 1",
                "Entity 2"
            ],

            "evidence_in_article": "Specific quote or attribution, or 'No evidence provided.'",

            "source_attribution": "Named source, organization, or 'No source attribution.'",

            "verification_required": "Yes"
        }}

    ]
}}


ARTICLE:

{article}

"""

        # ====================================================
        # SEND ARTICLE TO GROQ
        # ====================================================

        response = self.client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )


        # ====================================================
        # GET GROQ RESPONSE
        # ====================================================

        clean_text = response.choices[0].message.content.strip()


        # ====================================================
        # CLEAN LLM RESPONSE
        # ====================================================

        clean_text = clean_text.replace(
            "```json",
            ""
        ).replace(
            "```",
            ""
        ).strip()


        # ====================================================
        # CONVERT JSON TEXT TO PYTHON DATA
        # ====================================================

        analysis = json.loads(clean_text)

        return analysis


    # ========================================================
    # AUDIT ARTICLE
    # ========================================================

    def audit(self, url):

        # ====================================================
        # FETCH ARTICLE
        # ====================================================

        downloaded = self.fetch_article(url)

        if downloaded is None:

            return {
                "error": "Could not fetch article"
            }


        # ====================================================
        # EXTRACT ARTICLE
        # ====================================================

        article = self.extract_article(downloaded)

        if article is None:

            return {
                "error": "Could not extract article text"
            }


        # ====================================================
        # ANALYZE ARTICLE
        # ====================================================

        analysis = self.analyze_article(article)


        # ====================================================
        # RETURN RESULTS
        # ====================================================

        return {

            "url": url,

            "article": article,

            "llm_score": analysis.get(
                "llm_score",
                50
            ),

            "explanation": analysis.get(
                "explanation",
                "Analysis complete."
            ),

            "extracted_claims": analysis.get(
                "extracted_claims",
                []
            )
        }


# ============================================================
# CREATE FASTAPI APP
# ============================================================

app = FastAPI()


# ============================================================
# CREATE AUDITOR
# ============================================================

auditor = OpenNewsAuditor()


# ============================================================
# FRONTEND SETUP
# ============================================================

app.mount(
    "/static",
    StaticFiles(directory="frontend"),
    name="static"
)


# ============================================================
# HOME PAGE
# ============================================================

@app.get("/")
def home():

    return FileResponse(
        "frontend/index.html"
    )


# ============================================================
# AUDIT ARTICLE
# ============================================================

@app.post("/audit")
def audit_article(request: ArticleRequest):

    # ========================================================
    # FAKE ARTICLE FOR DESIGN TESTING
    # ========================================================

    if request.url == "https://example.com/news-article":

        return {

            "url": request.url,

            "article": """
The government announced a new policy on Monday.

Officials stated that the policy will take effect next month
and will affect several organizations.

The announcement was made during a press conference in the capital.

The government said the policy is intended to improve public services.
""",

            "llm_score": 82,

            "explanation": (
                "The article's main factual claims are generally "
                "supported by identifiable information, although "
                "some details remain insufficiently supported or "
                "unverified. The score reflects the balance between "
                "supported claims and the uncertainty surrounding "
                "several claims."
            ),

            "extracted_claims": [

                {
                    "claim": "The government announced a new policy on Monday.",

                    "type": "Fact",

                    "entities": [
                        "Government"
                    ],

                    "evidence_in_article":
                        "The article states that the government announced a new policy on Monday.",

                    "source_attribution":
                        "Government officials",

                    "verification_required":
                        "Yes"
                },

                {
                    "claim": "The policy will take effect next month.",

                    "type": "Fact",

                    "entities": [
                        "Government",
                        "Policy"
                    ],

                    "evidence_in_article":
                        "Officials stated that the policy will take effect next month.",

                    "source_attribution":
                        "Government officials",

                    "verification_required":
                        "Yes"
                },

                {
                    "claim": "The policy will affect several organizations.",

                    "type": "Fact",

                    "entities": [
                        "Organizations",
                        "Government"
                    ],

                    "evidence_in_article":
                        "The article states that the policy will affect several organizations.",

                    "source_attribution":
                        "No specific source attribution.",

                    "verification_required":
                        "Yes"
                },

                {
                    "claim":
                        "The announcement was made during a press conference in the capital.",

                    "type": "Fact",

                    "entities": [
                        "Government"
                    ],

                    "evidence_in_article":
                        "The announcement was made during a press conference in the capital.",

                    "source_attribution":
                        "No specific source attribution.",

                    "verification_required":
                        "Yes"
                },

                {
                    "claim":
                        "The government said the policy is intended to improve public services.",

                    "type": "Statement",

                    "entities": [
                        "Government"
                    ],

                    "evidence_in_article":
                        "The government said the policy is intended to improve public services.",

                    "source_attribution":
                        "Government",

                    "verification_required":
                        "Yes"
                }

            ]
        }


    # ========================================================
    # REAL ARTICLE
    # ========================================================

    try:

        return auditor.audit(request.url)

    except Exception as e:

        return {

            "url": request.url,

            "error": f"Article analysis failed: {str(e)}"
        }


# ============================================================
# DEMO DATA
# ============================================================

# This endpoint is ONLY for testing the frontend design.
#
# It does NOT:
# - fetch a real article
# - use Trafilatura
# - call Groq
#
# Open:
# http://127.0.0.1:8000/demo
# ============================================================

@app.get("/demo")
def demo_audit():

    return {

        "url":
            "https://example.com/news-article",


        "article":
            """
The government announced a new policy on Monday.

Officials stated that the policy will take effect next month
and will affect several organizations.

The announcement was made during a press conference in the capital.

The government said the policy is intended to improve public services.
""",


        "llm_score":
            82,


        "explanation":
            (
                "The article's main factual claims are generally "
                "supported by identifiable information, although "
                "some details remain insufficiently supported or "
                "unverified. The score reflects the balance between "
                "supported claims and the uncertainty surrounding "
                "several claims."
            ),


        "extracted_claims": [

            {

                "claim":
                    "The government announced a new policy on Monday.",

                "type":
                    "Fact",

                "entities": [
                    "Government"
                ],

                "evidence_in_article":
                    (
                        "The article states that the government "
                        "announced a new policy on Monday."
                    ),

                "source_attribution":
                    "Government officials",

                "verification_required":
                    "Yes"
            },


            {

                "claim":
                    "The policy will take effect next month.",

                "type":
                    "Fact",

                "entities": [
                    "Government",
                    "Policy"
                ],

                "evidence_in_article":
                    (
                        "Officials stated that the policy will "
                        "take effect next month."
                    ),

                "source_attribution":
                    "Government officials",

                "verification_required":
                    "Yes"
            },


            {

                "claim":
                    "The policy will affect several organizations.",

                "type":
                    "Fact",

                "entities": [
                    "Organizations",
                    "Government"
                ],

                "evidence_in_article":
                    (
                        "The article states that the policy will "
                        "affect several organizations."
                    ),

                "source_attribution":
                    "No specific source attribution.",

                "verification_required":
                    "Yes"
            },


            {

                "claim":
                    (
                        "The announcement was made during a press "
                        "conference in the capital."
                    ),

                "type":
                    "Fact",

                "entities": [
                    "Government"
                ],

                "evidence_in_article":
                    (
                        "The announcement was made during a press "
                        "conference in the capital."
                    ),

                "source_attribution":
                    "No specific source attribution.",

                "verification_required":
                    "Yes"
            },


            {

                "claim":
                    (
                        "The government said the policy is intended "
                        "to improve public services."
                    ),

                "type":
                    "Statement",

                "entities": [
                    "Government"
                ],

                "evidence_in_article":
                    (
                        "The government said the policy is intended "
                        "to improve public services."
                    ),

                "source_attribution":
                    "Government",

                "verification_required":
                    "Yes"
            }

        ]
    }