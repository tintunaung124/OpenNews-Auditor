document.addEventListener("DOMContentLoaded", function () {

    const articleUrl = document.getElementById("article-url");
    const searchButton = document.getElementById("search-button");
    const resultDiv = document.getElementById("result");

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
    // FORMAT STATUS LABEL

    function formatLabel(value) {
        if (!value) {
            return "Not analyzed";}
        let label = cleanText(value);
        label = label
            .replace(/[_-]+/g, " ")
            .toLowerCase()
            .replace(/\b\w/g, function (letter) {
                return letter.toUpperCase();
            });

        return label;
    }
    // GET TRUST SCORE

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

    // ESCAPE HTML

    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // FORMAT SOURCE LIST

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

    // FORMAT DETAIL LIST

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


    // FORMAT EXPLANATION

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

    // SEARCH / AUDIT FUNCTION
    async function runAudit() {

        const urlInput = articleUrl.value.trim();
        // CHECK URL

        if (!urlInput) {

            resultDiv.innerHTML = `
                <p>
                    Please enter a URL.
                </p>
            `;

            return;
        }
        // LOADING
        resultDiv.innerHTML = `
            <p>
                Analyzing article...
            </p>
        `;


        try {
            // SEND URL TO BACKEND
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
            // READ RESPONSE
            const data = await response.json();

            console.log(
                "Backend response:",
                data
            );
            // BACKEND ERROR
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
            // TRUST SCORE
            const score = getTrustScore(data);
            // VERIFICATION STATUS
            const verificationStatus =
                data.verification_status ||
                null;
            // CLAIMS
            const claims =
                data.extracted_claims ||
                data.claims ||
                [];
            // HEADLINE ANALYSIS
            const headlineAnalysis =
                data.headline_analysis ||
                data.headlineAnalysis ||
                {};
            // HEADLINE
            const headline =
                headlineAnalysis.headline ||
                data.headline ||
                data.title ||
                "No headline available.";

            // BIAS
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

            // MISLEADING
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

            // SENSATIONALISM
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

            // SCORE SECTION
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
            // CLAIMS

            let claimsHtml = "";


            if (claims.length > 0) {

                claimsHtml = claims.map(
                    function (claim, index) {
                        // VERIFICATION OBJECT
                        const verification =
                            claim.verification ||
                            {};
                        // VERIFICATION STATUS
                        const claimVerificationStatus =
                            verification.status ||
                            claim.verification_result ||
                            "UNVERIFIED";

                        // VERIFICATION REASON
                        const verificationReason =
                            verification.reason ||
                            claim.verification_reason ||
                            verification.explanation ||
                            "No detailed explanation was provided for this claim.";

                        // SUPPORTING SOURCES
                        let supportingSources =
                            verification.supporting_sources ||
                            [];

                        // FALLBACK SOURCES
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
                        // SUPPORTED DETAILS
                        const supportedDetails =
                            verification.supported_details ||
                            [];
                        // UNSUPPORTED DETAILS
                        const unsupportedDetails =
                            verification.unsupported_details ||
                            [];

                        // ENTITIES
                        const entities =
                            Array.isArray(
                                claim.entities
                            )
                                ? claim.entities.join(", ")
                                : claim.entities ||
                                  "None";

                        // SOURCE ATTRIBUTION
                        const source =
                            claim.source_attribution ||
                            "No source attribution";

                        // VERIFICATION REQUIRED
                        const verificationRequired =
                            claim.verification_required ??
                            true;

                        // CLAIM TEXT
                        const claimText =
                            claim.claim ||
                            "No claim text available.";

                        // CLAIM TYPE
                        const claimType =
                            claim.type ||
                            "Fact";

                        // EVIDENCE COUNT
                        const evidenceCount =
                            claim.evidence_count ??
                            0;

                        // STATUS LABEL
                        const statusLabel =
                            formatLabel(
                                claimVerificationStatus
                            );

                        // EXPLANATION TITLE
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

                        // DETAIL LISTS

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

                        // SOURCES HTML
                        const sourcesHtml =
                            formatSources(
                                supportingSources
                            );

                        // CLAIM CARD
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
            // EXPLANATION

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

        // ERROR
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

    function getClaimStatusV2(claim) {
        return String(
            claim?.verification?.status ||
            claim?.verification_result ||
            "UNVERIFIED"
        ).toUpperCase();
    }

    function formatStatusLabelV2(status) {
        return formatLabel(status || "NOT_ANALYZED");
    }

    function statusClassV2(status) {
        return status.toLowerCase().replace(/_/g, "-");
    }

    function renderSourceDetailsV2(sources) {
        if (!Array.isArray(sources) || sources.length === 0) {
            return `<p class="source-empty">No sources returned</p>`;
        }

        return `
            <div class="source-details">
                ${sources.map(function (source) {
                    if (typeof source === "string") {
                        return `<div class="source-item"><strong>${escapeHtml(source)}</strong></div>`;
                    }

                    const title = source.title || "Untitled source";
                    const publisher = source.source || "Publisher not provided";
                    const published = source.published || "Date not provided";
                    const evidenceType = source.evidence_type || "Evidence";
                    const link = source.url || "";
                    const linkHtml = link
                        ? `<a href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">Open source</a>`
                        : "";

                    return `
                        <div class="source-item">
                            <div class="source-item-heading">
                                <strong>${escapeHtml(title)}</strong>
                                ${linkHtml}
                            </div>
                            <div class="source-item-meta">
                                <span>${escapeHtml(publisher)}</span>
                                <span>${escapeHtml(published)}</span>
                                <span>${escapeHtml(formatLabel(evidenceType))}</span>
                            </div>
                        </div>
                    `;
                }).join("")}
            </div>
        `;
    }

    function renderClaimV2(claim, index) {
        const status = getClaimStatusV2(claim);
        const verification = claim.verification || {};
        const sources = Array.isArray(claim.evidence_sources)
            ? claim.evidence_sources
            : [];
        const evidenceCount = sources.length;
        const reason = cleanText(
            verification.reason ||
            claim.verification_reason ||
            "No detailed explanation was provided for this claim."
        );

        return `
            <article class="claim-card claim-${statusClassV2(status)}">
                <div class="claim-header">
                    <span class="claim-number">Claim ${index + 1}</span>
                    <span class="verdict verdict-${statusClassV2(status)}">${escapeHtml(formatStatusLabelV2(status))}</span>
                </div>
                <p class="claim-text">${escapeHtml(cleanText(claim.claim || "No claim text available."))}</p>
                <div class="claim-summary">
                    <span>${escapeHtml(String(evidenceCount))} source${evidenceCount === 1 ? "" : "s"}</span>
                    <span>${escapeHtml(formatLabel(claim.type || "Fact"))}</span>
                </div>
                <div class="claim-explanation">
                    <h4>Why this result</h4>
                    <p>${escapeHtml(reason)}</p>
                    ${formatDetailList("Details supported by the evidence:", verification.supported_details || [])}
                    ${formatDetailList("Details not confirmed by the evidence:", verification.unsupported_details || [])}
                </div>
                <div class="claim-sources">
                    <h4>Evidence</h4>
                    ${renderSourceDetailsV2(sources)}
                </div>
            </article>
        `;
    }

    function renderHeadlineCheckV2(label, check) {
        const status = check?.status || check?.label || "NOT_ANALYZED";
        const reason = cleanText(
            check?.reason ||
            check?.explanation ||
            "No analysis available."
        );

        return `
            <div class="headline-check">
                <div class="check-header">
                    <strong>${escapeHtml(label)}</strong>
                    <span class="headline-status">${escapeHtml(formatStatusLabelV2(status))}</span>
                </div>
                <p>${escapeHtml(reason)}</p>
            </div>
        `;
    }

    function renderSummaryV2(score, claims, verificationStatus, explanation) {
        const counts = {
            SUPPORTED: 0,
            PARTIALLY_SUPPORTED: 0,
            UNSUPPORTED: 0,
            CONTRADICTED: 0,
            UNVERIFIED: 0
        };

        claims.forEach(function (claim) {
            const status = getClaimStatusV2(claim);
            counts[status] = (counts[status] || 0) + 1;
        });

        const verifiedCount =
            counts.SUPPORTED +
            counts.PARTIALLY_SUPPORTED +
            counts.UNSUPPORTED +
            counts.CONTRADICTED;
        const claimsWithSources = claims.filter(function (claim) {
            return Array.isArray(claim.evidence_sources) && claim.evidence_sources.length > 0;
        }).length;
        const scoreHtml = score !== null
            ? `<div class="summary-score-value">${escapeHtml(String(score))}<span>/100</span></div><p class="summary-score-label">Trust score</p>`
            : `<p class="summary-status">Insufficient evidence for an overall trust score</p><p class="summary-score-label">${escapeHtml(verificationStatus || "Audit completed")}</p>`;
        const sourceMetric = claimsWithSources > 0
            ? `<div><strong>${claimsWithSources}</strong><span>claims with sources</span></div>`
            : "";

        return `
            <section class="result-card summary-card" aria-labelledby="summary-title">
                <div class="summary-main">
                    <div>
                        <span class="section-label">Overall assessment</span>
                        <h2 id="summary-title">Audit results</h2>
                        ${scoreHtml}
                    </div>
                    <p class="summary-explanation">${escapeHtml(cleanText(explanation || "No overall explanation available."))}</p>
                </div>
                <div class="summary-metrics" aria-label="Claim verdict counts">
                    <div><strong>${counts.SUPPORTED}</strong><span>supported</span></div>
                    <div><strong>${counts.PARTIALLY_SUPPORTED}</strong><span>partially supported</span></div>
                    <div><strong>${counts.UNSUPPORTED}</strong><span>unsupported</span></div>
                    <div><strong>${counts.UNVERIFIED}</strong><span>unverified</span></div>
                    <div><strong>${verifiedCount}</strong><span>verified claims</span></div>
                    ${sourceMetric}
                </div>
            </section>
        `;
    }

    function renderErrorV2(message) {
        resultDiv.innerHTML = `
            <div class="state-card error-state" role="alert">
                <span class="section-label">Audit error</span>
                <h2>We could not complete the audit</h2>
                <p>${escapeHtml(message)}</p>
                <button type="button" class="retry-button" data-action="retry">Try again</button>
            </div>
        `;
        articleUrl.focus();
    }

    async function runAuditV2() {
        const urlInput = articleUrl.value.trim();

        if (!urlInput) {
            renderErrorV2("Enter a complete article URL, then try again.");
            return;
        }

        resultDiv.innerHTML = `
            <div class="state-card loading-state" role="status" aria-live="polite" aria-busy="true">
                <span class="loading-mark" aria-hidden="true"></span>
                <div>
                    <h2>Analyzing article</h2>
                    <p>Extracting claims and checking independent evidence. This may take a few minutes.</p>
                </div>
            </div>
        `;

        try {
            const response = await fetch("/audit", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: urlInput })
            });
            const data = await response.json();

            if (!response.ok || data.error || data.detail) {
                renderErrorV2(
                    data.error ||
                    data.detail ||
                    "The audit service returned an unexpected error. Check the URL and try again."
                );
                return;
            }

            const score = getTrustScore(data);
            const claims = data.extracted_claims || data.claims || [];
            const headlineAnalysis = data.headline_analysis || data.headlineAnalysis || {};
            const headline = headlineAnalysis.headline || data.headline || data.title || "No headline available.";
            const explanation = data.explanation || "No overall explanation available.";
            const claimHtml = claims.length > 0
                ? claims.map(renderClaimV2).join("")
                : `<p class="empty-state">No factual claims were extracted.</p>`;

            resultDiv.innerHTML = `
                <div class="audit-results">
                    ${renderSummaryV2(score, claims, data.verification_status, explanation)}
                    <section class="result-card headline-analysis" aria-labelledby="headline-analysis-title">
                        <span class="section-label">Headline review</span>
                        <h2 id="headline-analysis-title">Headline analysis</h2>
                        <p class="headline-value">${escapeHtml(cleanText(headline))}</p>
                        <div class="headline-checks">
                            ${renderHeadlineCheckV2("Bias", headlineAnalysis.bias)}
                            ${renderHeadlineCheckV2("Misleading", headlineAnalysis.misleading)}
                            ${renderHeadlineCheckV2("Sensationalism", headlineAnalysis.sensationalism)}
                        </div>
                    </section>
                    <section class="result-card claims-section" aria-labelledby="claims-title">
                        <div class="section-heading">
                            <div>
                                <span class="section-label">Claim review</span>
                                <h2 id="claims-title">Extracted claims</h2>
                            </div>
                            <span class="section-count">${claims.length}</span>
                        </div>
                        <div class="claims">${claimHtml}</div>
                    </section>
                </div>
            `;
        } catch (error) {
            console.error("Audit error:", error);
            renderErrorV2(
                error?.message
                    ? `The audit request failed: ${error.message}`
                    : "The audit service is unavailable. Check your connection and try again."
            );
        }
    }

    // SEARCH BUTTON
    searchButton.addEventListener(
        "click",
        runAuditV2
    );

    resultDiv.addEventListener("click", function (event) {
        if (event.target.matches('[data-action="retry"]')) {
            runAuditV2();
        }
    });

    // ENTER KEY
    articleUrl.addEventListener(
        "keydown",
        function (event) {

            if (event.key === "Enter") {

                event.preventDefault();

                runAuditV2();
            }
        }
    );

});
