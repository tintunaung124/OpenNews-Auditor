document.addEventListener("DOMContentLoaded", function () {

    const articleUrl = document.getElementById("article-url");
    const searchButton = document.getElementById("search-button");
    const resultDiv = document.getElementById("result");


    // ====================================================
    // CLEAN AI TEXT
    // ====================================================

    function cleanText(value) {
        if (!value) {
            return "";
        }

        return String(value)
            // Remove bold / italic Markdown
            .replace(/\*\*/g, "")
            .replace(/\*/g, "")

            // Remove Markdown headings
            .replace(/^#{1,6}\s*/gm, "")

            // Remove Markdown bullet points
            .replace(/^\s*[-•]\s*/gm, "")

            // Remove underscores used as Markdown
            .replace(/_/g, " ")

            // Remove extra spaces
            .replace(/\s+/g, " ")

            .trim();
    }


    // ====================================================
    // FORMAT STATUS LABEL
    // ====================================================

    function formatLabel(value) {

        if (!value) {
            return "Not analyzed";
        }

        let label = cleanText(value);

        label = label
            .replace(/[_-]+/g, " ")
            .toLowerCase()
            .replace(/\b\w/g, function (letter) {
                return letter.toUpperCase();
            });

        return label;
    }


    // ====================================================
    // GET TRUST SCORE
    // ====================================================

    function getTrustScore(data) {

        const possibleScore =
            data.llm_score ??
            data.trust_score ??
            data.score ??
            data.trustScore;

        if (
            possibleScore !== undefined &&
            possibleScore !== null
        ) {
            const number = Number(possibleScore);

            if (!Number.isNaN(number)) {
                return number;
            }
        }

        return null;
    }


    // ====================================================
    // ESCAPE HTML
    // ====================================================

    function escapeHtml(value) {

        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    // ====================================================
    // FORMAT SOURCE LIST
    // ====================================================

    function formatSources(supportingSources) {

        if (
            !Array.isArray(supportingSources) ||
            supportingSources.length === 0
        ) {
            return `
                <div class="claim-sources">
                    <strong>Supporting Sources:</strong>
                    <span>
                        No specific supporting sources
                        were identified.
                    </span>
                </div>
            `;
        }

        const sources = supportingSources
            .filter(function (source) {
                return (
                    source !== null &&
                    source !== undefined &&
                    String(source).trim() !== ""
                );
            })
            .map(function (source) {

                return `
                    <span class="source-tag">
                        ${escapeHtml(source)}
                    </span>
                `;

            })
            .join("");

        if (!sources) {
            return `
                <div class="claim-sources">
                    <strong>Supporting Sources:</strong>
                    <span>
                        No specific supporting sources
                        were identified.
                    </span>
                </div>
            `;
        }

        return `
            <div class="claim-sources">
                <strong>Supporting Sources:</strong>

                <div class="source-list">
                    ${sources}
                </div>
            </div>
        `;
    }


    // ====================================================
    // FORMAT DETAIL LIST
    // ====================================================

    function formatDetailList(title, details) {

        if (
            !Array.isArray(details) ||
            details.length === 0
        ) {
            return "";
        }

        const items = details
            .filter(function (item) {
                return (
                    item !== null &&
                    item !== undefined &&
                    String(item).trim() !== ""
                );
            })
            .map(function (item) {
                return `
                    <li>
                        ${escapeHtml(cleanText(item))}
                    </li>
                `;
            })
            .join("");

        if (!items) {
            return "";
        }

        return `
            <div class="claim-detail-list">
                <strong>${escapeHtml(title)}</strong>

                <ul>
                    ${items}
                </ul>
            </div>
        `;
    }


    // ====================================================
    // FORMAT EXPLANATION
    // ====================================================

    function formatExplanation(text) {

        if (!text) {
            return `
                <p>
                    No explanation available.
                </p>
            `;
        }

        const cleaned = String(text)
            .replace(/\*\*/g, "")
            .replace(/\*/g, "")
            .replace(/^#{1,6}\s*/gm, "")
            .replace(/^\s*[-•]\s*/gm, "")
            .trim();

        const sections = cleaned
            .split(/\n\s*\n/)
            .filter(function (section) {
                return section.trim() !== "";
            });

        if (sections.length === 0) {
            return `
                <p>
                    ${escapeHtml(cleanText(text))}
                </p>
            `;
        }

        return sections
            .map(function (section) {
                return `
                    <p>
                        ${escapeHtml(
                            section
                                .replace(/\s+/g, " ")
                                .trim()
                        )}
                    </p>
                `;
            })
            .join("");
    }


    // ====================================================
    // SEARCH / AUDIT FUNCTION
    // ====================================================

    async function runAudit() {

        const urlInput = articleUrl.value.trim();


        // =================================================
        // CHECK URL
        // =================================================

        if (!urlInput) {

            resultDiv.innerHTML = `
                <p>
                    Please enter a URL.
                </p>
            `;

            return;
        }


        // =================================================
        // LOADING
        // =================================================

        resultDiv.innerHTML = `
            <p>
                Analyzing article...
            </p>
        `;


        try {

            // =============================================
            // SEND URL TO BACKEND
            // =============================================

            const response = await fetch(
                "/audit",
                {
                    method: "POST",

                    headers: {
                        "Content-Type": "application/json"
                    },

                    body: JSON.stringify({
                        url: urlInput
                    })
                }
            );


            // =============================================
            // READ RESPONSE
            // =============================================

            const data = await response.json();

            console.log(
                "Backend response:",
                data
            );


            // =============================================
            // BACKEND ERROR
            // =============================================

            if (
                !response.ok ||
                data.error
            ) {

                resultDiv.innerHTML = `
                    <p>
                        ${escapeHtml(
                            data.error ||
                            "Something went wrong."
                        )}
                    </p>
                `;

                return;
            }


            // =============================================
            // TRUST SCORE
            // =============================================

            const score = getTrustScore(data);


            // =============================================
            // VERIFICATION STATUS
            // =============================================

            const verificationStatus =
                data.verification_status ||
                null;


            // =============================================
            // CLAIMS
            // =============================================

            const claims =
                data.extracted_claims ||
                data.claims ||
                [];


            // =============================================
            // HEADLINE ANALYSIS
            // =============================================

            const headlineAnalysis =
                data.headline_analysis ||
                data.headlineAnalysis ||
                {};


            // =============================================
            // HEADLINE
            // =============================================

            const headline =
                headlineAnalysis.headline ||
                data.headline ||
                data.title ||
                "No headline available.";


            // =============================================
            // BIAS
            // =============================================

            const bias =
                headlineAnalysis.bias ||
                {};

            const biasStatus =
                formatLabel(
                    bias.status ||
                    bias.label ||
                    "Not analyzed"
                );

            const biasReason =
                cleanText(
                    bias.reason ||
                    bias.explanation ||
                    "No bias analysis available."
                );


            // =============================================
            // MISLEADING
            // =============================================

            const misleading =
                headlineAnalysis.misleading ||
                {};

            const misleadingStatus =
                formatLabel(
                    misleading.status ||
                    misleading.label ||
                    "Not analyzed"
                );

            const misleadingReason =
                cleanText(
                    misleading.reason ||
                    misleading.explanation ||
                    "No misleading analysis available."
                );


            // =============================================
            // SENSATIONALISM
            // =============================================

            const sensationalism =
                headlineAnalysis.sensationalism ||
                {};

            const sensationalismStatus =
                formatLabel(
                    sensationalism.status ||
                    sensationalism.label ||
                    "Not analyzed"
                );

            const sensationalismReason =
                cleanText(
                    sensationalism.reason ||
                    sensationalism.explanation ||
                    "No sensationalism analysis available."
                );


            // =================================================
            // SCORE SECTION
            // =================================================

            let scoreSection = "";

            if (score !== null) {

                const safeScore =
                    Math.max(
                        0,
                        Math.min(
                            100,
                            score
                        )
                    );

                scoreSection = `
                    <div class="score-card">

                        <div class="score-label">
                            Trust Score
                        </div>

                        <div class="score-number">
                            ${escapeHtml(score)}
                            <span>/ 100</span>
                        </div>

                        <div class="score-bar">

                            <div
                                class="score-fill fill-good"
                                style="width: ${safeScore}%"
                            ></div>

                        </div>

                    </div>
                `;

            } else {

                scoreSection = `
                    <div class="score-card verification-card">

                        <div class="score-label">
                            Verification Status
                        </div>

                        <div class="verification-message">
                            ${escapeHtml(
                                verificationStatus ||
                                "We could not find enough external evidence to verify these claims."
                            )}
                        </div>

                    </div>
                `;
            }


            // =================================================
            // CLAIMS
            // =================================================

            let claimsHtml = "";


            if (claims.length > 0) {

                claimsHtml = claims.map(
                    function (claim, index) {


                        // -------------------------------------
                        // VERIFICATION OBJECT
                        // -------------------------------------

                        const verification =
                            claim.verification ||
                            {};


                        // -------------------------------------
                        // VERIFICATION STATUS
                        // -------------------------------------

                        const claimVerificationStatus =
                            verification.status ||
                            claim.verification_result ||
                            "UNVERIFIED";


                        // -------------------------------------
                        // VERIFICATION REASON
                        // -------------------------------------

                        const verificationReason =
                            verification.reason ||
                            claim.verification_reason ||
                            verification.explanation ||
                            "No detailed explanation was provided for this claim.";


                        // -------------------------------------
                        // SUPPORTING SOURCES
                        // -------------------------------------

                        let supportingSources =
                            verification.supporting_sources ||
                            [];


                        // -------------------------------------
                        // FALLBACK SOURCES
                        // -------------------------------------

                        if (
                            !Array.isArray(supportingSources) ||
                            supportingSources.length === 0
                        ) {

                            if (
                                Array.isArray(
                                    claim.evidence_sources
                                )
                            ) {

                                supportingSources =
                                    claim.evidence_sources
                                        .map(function (item) {

                                            if (
                                                typeof item === "string"
                                            ) {
                                                return item;
                                            }

                                            return item?.source || "";
                                        })
                                        .filter(function (item) {
                                            return item.trim() !== "";
                                        });
                            }
                        }


                        // -------------------------------------
                        // SUPPORTED DETAILS
                        // -------------------------------------

                        const supportedDetails =
                            verification.supported_details ||
                            [];


                        // -------------------------------------
                        // UNSUPPORTED DETAILS
                        // -------------------------------------

                        const unsupportedDetails =
                            verification.unsupported_details ||
                            [];


                        // -------------------------------------
                        // ENTITIES
                        // -------------------------------------

                        const entities =
                            Array.isArray(
                                claim.entities
                            )
                                ? claim.entities.join(", ")
                                : claim.entities ||
                                  "None";


                        // -------------------------------------
                        // SOURCE ATTRIBUTION
                        // -------------------------------------

                        const source =
                            claim.source_attribution ||
                            "No source attribution";


                        // -------------------------------------
                        // VERIFICATION REQUIRED
                        // -------------------------------------

                        const verificationRequired =
                            claim.verification_required ??
                            true;


                        // -------------------------------------
                        // CLAIM TEXT
                        // -------------------------------------

                        const claimText =
                            claim.claim ||
                            "No claim text available.";


                        // -------------------------------------
                        // CLAIM TYPE
                        // -------------------------------------

                        const claimType =
                            claim.type ||
                            "Fact";


                        // -------------------------------------
                        // EVIDENCE COUNT
                        // -------------------------------------

                        const evidenceCount =
                            claim.evidence_count ??
                            0;


                        // -------------------------------------
                        // STATUS LABEL
                        // -------------------------------------

                        const statusLabel =
                            formatLabel(
                                claimVerificationStatus
                            );


                        // -------------------------------------
                        // EXPLANATION TITLE
                        // -------------------------------------

                        let explanationTitle =
                            "Why this result?";

                        if (
                            claimVerificationStatus ===
                            "PARTIALLY_SUPPORTED"
                        ) {
                            explanationTitle =
                                "Why is this claim only partially supported?";
                        }

                        else if (
                            claimVerificationStatus ===
                            "SUPPORTED"
                        ) {
                            explanationTitle =
                                "Why is this claim supported?";
                        }

                        else if (
                            claimVerificationStatus ===
                            "UNSUPPORTED"
                        ) {
                            explanationTitle =
                                "Why is this claim unsupported?";
                        }

                        else if (
                            claimVerificationStatus ===
                            "CONTRADICTED"
                        ) {
                            explanationTitle =
                                "Why is this claim contradicted?";
                        }

                        else if (
                            claimVerificationStatus ===
                            "UNVERIFIED"
                        ) {
                            explanationTitle =
                                "Why could this claim not be verified?";
                        }


                        // -------------------------------------
                        // DETAIL LISTS
                        // -------------------------------------

                        const supportedDetailsHtml =
                            formatDetailList(
                                "Details supported by the evidence:",
                                supportedDetails
                            );


                        const unsupportedDetailsHtml =
                            formatDetailList(
                                "Details not confirmed by the evidence:",
                                unsupportedDetails
                            );


                        // -------------------------------------
                        // SOURCES HTML
                        // -------------------------------------

                        const sourcesHtml =
                            formatSources(
                                supportingSources
                            );


                        // -------------------------------------
                        // CLAIM CARD
                        // -------------------------------------

                        return `
                            <div class="claim-card">

                                <div class="claim-number">
                                    Claim ${index + 1}
                                </div>


                                <p class="claim-text">
                                    ${escapeHtml(
                                        cleanText(
                                            claimText
                                        )
                                    )}
                                </p>


                                <div class="claim-details">

                                    <span>
                                        <strong>
                                            Type:
                                        </strong>

                                        ${escapeHtml(
                                            formatLabel(
                                                claimType
                                            )
                                        )}
                                    </span>


                                    <span>
                                        <strong>
                                            Entities:
                                        </strong>

                                        ${escapeHtml(
                                            cleanText(
                                                entities
                                            )
                                        )}
                                    </span>


                                    <span>
                                        <strong>
                                            Source:
                                        </strong>

                                        ${escapeHtml(
                                            cleanText(
                                                source
                                            )
                                        )}
                                    </span>


                                    <span>
                                        <strong>
                                            Verification Required:
                                        </strong>

                                        ${escapeHtml(
                                            String(
                                                verificationRequired
                                            )
                                        )}
                                    </span>


                                    <span>
                                        <strong>
                                            External Evidence:
                                        </strong>

                                        ${escapeHtml(
                                            String(
                                                evidenceCount
                                            )
                                        )}
                                    </span>


                                    <span>
                                        <strong>
                                            Verification Result:
                                        </strong>

                                        ${escapeHtml(
                                            statusLabel
                                        )}
                                    </span>

                                </div>


                                <!-- CLAIM EXPLANATION -->

                                <div class="claim-explanation">

                                    <div class="claim-explanation-title">
                                        ${escapeHtml(
                                            explanationTitle
                                        )}
                                    </div>


                                    <p>
                                        ${escapeHtml(
                                            cleanText(
                                                verificationReason
                                            )
                                        )}
                                    </p>


                                    ${supportedDetailsHtml}

                                    ${unsupportedDetailsHtml}

                                </div>


                                <!-- SUPPORTING SOURCES -->

                                ${sourcesHtml}

                            </div>
                        `;
                    }
                ).join("");

            } else {

                claimsHtml = `
                    <p>
                        No factual claims were extracted.
                    </p>
                `;
            }


            // =================================================
            // EXPLANATION
            // =================================================

            const explanation =
                data.explanation ||
                "No explanation available.";


            // =================================================
            // DISPLAY EVERYTHING
            // =================================================

            resultDiv.innerHTML = `

                <div class="audit-results">


                    <h2>
                        Audit Results
                    </h2>


                    <!-- TRUST SCORE -->

                    ${scoreSection}


                    <!-- HEADLINE ANALYSIS -->

                    <div class="result-card headline-analysis">

                        <h2>
                            Headline Analysis
                        </h2>


                        <!-- HEADLINE -->

                        <div class="headline-text">

                            <span class="section-label">
                                Headline
                            </span>

                            <p class="headline-value">
                                ${escapeHtml(
                                    cleanText(
                                        headline
                                    )
                                )}
                            </p>

                        </div>


                        <!-- CHECKS -->

                        <div class="headline-checks">


                            <!-- BIAS -->

                            <div class="headline-check">

                                <div class="check-header">

                                    <strong>
                                        Bias
                                    </strong>

                                    <span class="headline-status">
                                        ${escapeHtml(
                                            biasStatus
                                        )}
                                    </span>

                                </div>

                                <p>
                                    ${escapeHtml(
                                        biasReason
                                    )}
                                </p>

                            </div>


                            <!-- MISLEADING -->

                            <div class="headline-check">

                                <div class="check-header">

                                    <strong>
                                        Misleading
                                    </strong>

                                    <span class="headline-status">
                                        ${escapeHtml(
                                            misleadingStatus
                                        )}
                                    </span>

                                </div>

                                <p>
                                    ${escapeHtml(
                                        misleadingReason
                                    )}
                                </p>

                            </div>


                            <!-- SENSATIONALISM -->

                            <div class="headline-check">

                                <div class="check-header">

                                    <strong>
                                        Sensationalism
                                    </strong>

                                    <span class="headline-status">
                                        ${escapeHtml(
                                            sensationalismStatus
                                        )}
                                    </span>

                                </div>

                                <p>
                                    ${escapeHtml(
                                        sensationalismReason
                                    )}
                                </p>

                            </div>


                        </div>

                    </div>


                    <!-- EXTRACTED CLAIMS -->

                    <div class="result-card">

                        <h2>
                            Extracted Claims
                        </h2>

                        <div class="claims">
                            ${claimsHtml}
                        </div>

                    </div>


                    <!-- EXPLANATION -->

                    <div class="result-card">

                        <h2>
                            Explanation
                        </h2>

                        <div class="explanation-content">
                            ${formatExplanation(
                                explanation
                            )}
                        </div>

                    </div>


                </div>

            `;
        }


        // =================================================
        // ERROR
        // =================================================

        catch (error) {

            console.error(
                "Audit error:",
                error
            );

            resultDiv.innerHTML = `
                <p>
                    Error:
                    ${escapeHtml(
                        error.message
                    )}
                </p>
            `;
        }
    }


    // ====================================================
    // SEARCH BUTTON
    // ====================================================

    searchButton.addEventListener(
        "click",
        runAudit
    );


    // ====================================================
    // ENTER KEY
    // ====================================================

    articleUrl.addEventListener(
        "keydown",
        function (event) {

            if (event.key === "Enter") {

                event.preventDefault();

                runAudit();
            }
        }
    );

});