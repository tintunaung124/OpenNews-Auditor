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

            // Remove bullet points
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

        // Convert:
        // POTENTIAL_BIAS
        // NO_CLEAR_INDICATORS
        // LOW
        //
        // into:
        // Potential Bias
        // No Clear Indicators
        // Low

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
    // SEARCH / AUDIT FUNCTION
    // ====================================================

    async function runAudit() {

        const urlInput = articleUrl.value.trim();


        // =================================================
        // CHECK URL
        // =================================================

        if (!urlInput) {

            resultDiv.innerHTML = `
                <p>Please enter a URL.</p>
            `;

            return;
        }


        // =================================================
        // LOADING
        // =================================================

        resultDiv.innerHTML = `
            <p>Analyzing article...</p>
        `;


        try {

            // =============================================
            // SEND URL TO BACKEND
            // =============================================

            const response = await fetch("/audit", {

                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({
                    url: urlInput
                })

            });


            // =============================================
            // READ RESPONSE
            // =============================================

            const data = await response.json();

            console.log("Backend response:", data);


            // =============================================
            // BACKEND ERROR
            // =============================================

            if (!response.ok || data.error) {

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
                data.verification_status || null;


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

            const biasStatus = formatLabel(
                bias.status ||
                bias.label ||
                "Not analyzed"
            );

            const biasReason = cleanText(
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

            const misleadingStatus = formatLabel(
                misleading.status ||
                misleading.label ||
                "Not analyzed"
            );

            const misleadingReason = cleanText(
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

            const sensationalismStatus = formatLabel(
                sensationalism.status ||
                sensationalism.label ||
                "Not analyzed"
            );

            const sensationalismReason = cleanText(
                sensationalism.reason ||
                sensationalism.explanation ||
                "No sensationalism analysis available."
            );


            // =================================================
            // SCORE SECTION
            // =================================================

            let scoreSection = "";


            if (score !== null) {

                scoreSection = `

                    <div class="score-card">

                        <div class="score-label">
                            Trust Score
                        </div>

                        <div class="score-number">
                            ${score}
                            <span>/ 100</span>
                        </div>

                        <div class="score-bar">

                            <div
                                class="score-fill fill-good"
                                style="width: ${Math.max(
                                    0,
                                    Math.min(100, score)
                                )}%"
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

                        const verificationStatus =
                            claim.verification?.status ||
                            claim.verification_result ||
                            "UNVERIFIED";


                        const entities =
                            Array.isArray(claim.entities)
                                ? claim.entities.join(", ")
                                : claim.entities ||
                                  "None";


                        const source =
                            claim.source_attribution ||
                            "No source attribution";


                        const verificationRequired =
                            claim.verification_required ??
                            true;


                        const claimText =
                            claim.claim ||
                            "No claim text available.";


                        const claimType =
                            claim.type ||
                            "Fact";


                        return `

                            <div class="claim-card">

                                <div class="claim-number">
                                    Claim ${index + 1}
                                </div>

                                <p class="claim-text">
                                    ${escapeHtml(
                                        cleanText(claimText)
                                    )}
                                </p>

                                <div class="claim-details">

                                    <span>
                                        <strong>
                                            Type:
                                        </strong>
                                        ${escapeHtml(
                                            formatLabel(claimType)
                                        )}
                                    </span>

                                    <span>
                                        <strong>
                                            Entities:
                                        </strong>
                                        ${escapeHtml(
                                            cleanText(entities)
                                        )}
                                    </span>

                                    <span>
                                        <strong>
                                            Source:
                                        </strong>
                                        ${escapeHtml(
                                            cleanText(source)
                                        )}
                                    </span>

                                    <span>
                                        <strong>
                                            Verification Required:
                                        </strong>
                                        ${verificationRequired}
                                    </span>

                                    <span>
                                        <strong>
                                            Verification Result:
                                        </strong>

                                        ${escapeHtml(
                                            formatLabel(
                                                verificationStatus
                                            )
                                        )}

                                    </span>

                                </div>

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

                        <h3>
                            Headline Analysis
                        </h3>


                        <!-- HEADLINE -->

                        <div class="headline-text">

                            <span class="section-label">
                                Headline
                            </span>

                            <p class="headline-value">
                                ${escapeHtml(
                                    cleanText(headline)
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

                        <h3>
                            Extracted Claims
                        </h3>

                        <div class="claims">

                            ${claimsHtml}

                        </div>

                    </div>


                    <!-- EXPLANATION -->

                    <div class="result-card">

                        <h3>
                            Explanation
                        </h3>

                        <p>
                            ${escapeHtml(
                                cleanText(
                                    data.explanation ||
                                    "No explanation available."
                                )
                            )}
                        </p>

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
                    ${escapeHtml(error.message)}
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