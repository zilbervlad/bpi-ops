// Doughy global launcher behavior
// Read-only browser-side helpers only.
// No AI calls. No write actions. No database access.

document.addEventListener("DOMContentLoaded", function () {
        const button = document.getElementById("doughyMiniButton");
        const panel = document.getElementById("doughyMiniPanel");
        const close = document.getElementById("doughyMiniClose");

        if (!button || !panel || !close) return;

        button.addEventListener("click", function () {
            panel.classList.toggle("open");
            panel.setAttribute("aria-hidden", panel.classList.contains("open") ? "false" : "true");
        });

        close.addEventListener("click", function () {
            panel.classList.remove("open");
            panel.setAttribute("aria-hidden", "true");
        });


        document.addEventListener("click", async function (event) {
            const copyButton = event.target.closest("[data-doughy-copy-draft]");
            if (!copyButton) return;

            const draftBox = document.getElementById("doughyDraftText");
            if (!draftBox) return;

            const draftText = draftBox.innerText.trim();

            try {
                await navigator.clipboard.writeText(draftText);
                copyButton.textContent = "Copied";
                setTimeout(function () {
                    copyButton.textContent = "Copy draft";
                }, 1400);
            } catch (error) {
                copyButton.textContent = "Copy failed";
                setTimeout(function () {
                    copyButton.textContent = "Copy draft";
                }, 1400);
            }
        });

        document.querySelectorAll("[data-doughy-soon]").forEach(function (quickButton) {
            quickButton.addEventListener("click", function () {
                const soonBox = document.getElementById("doughyComingSoon");
                if (!soonBox) return;

                const prompt = quickButton.getAttribute("data-doughy-soon") || "this";

                if (prompt === "Summarize this page") {
                    const snapshot = buildDoughyVisibleSnapshot();

                    soonBox.innerHTML = `
                        <strong>Read-only page snapshot</strong><br>
                        Doughy can see this page has
                        <strong>${snapshot.statCards}</strong> stat/card areas,
                        <strong>${snapshot.tables}</strong> table(s),
                        and <strong>${snapshot.buttons}</strong> visible action button(s).<br>
                        ${snapshot.heading ? `View: <strong>${snapshot.heading}</strong><br>` : ""}
                        <span class="doughy-context-muted">AI summary is still off. This is only visible page structure.</span>
                    `;
                    soonBox.classList.add("open");
                    return;
                }

                if (prompt === "What needs attention today?") {
                    const attention = buildDoughyAttentionSnapshot();

                    if (attention.items.length === 0) {
                        soonBox.innerHTML = `
                            <strong>Read-only attention scan</strong><br>
                            I scanned <strong>${attention.scanned}</strong> visible page block(s), but I do not see obvious warning/open/failed items.<br>
                            <span class="doughy-context-muted">This is only scanning visible page text. AI and database checks are still off.</span>
                        `;
                    } else {
                        soonBox.innerHTML = `
                            <strong>Read-only attention scan</strong><br>
                            I found <strong>${attention.items.length}</strong> visible item(s) that may need attention:<br>
                            ${attention.items.map(item => `• ${escapeDoughyHtml(item)}`).join("<br>")}
                            <br><span class="doughy-context-muted">This is only scanning visible page text. AI and database checks are still off.</span>
                        `;
                    }

                    soonBox.classList.add("open");
                    return;
                }

                if (prompt === "Draft a follow-up message") {
                    const attention = buildDoughyAttentionSnapshot();
                    const contextText = (document.getElementById("doughyContextBody") || {}).textContent || "";
                    const storeMatch = contextText.match(/Store:\s*([^\n]+)/i);
                    const pageMatch = contextText.match(/Page:\s*([^\n]+)/i);

                    const store = storeMatch ? storeMatch[1].trim() : "this store/page";
                    const page = pageMatch ? pageMatch[1].trim() : "BPI Ops";

                    if (attention.items.length === 0) {
                        soonBox.innerHTML = `
                            <strong>Follow-up draft</strong><br>
                            <div class="doughy-draft-box" id="doughyDraftText">
                                Quick follow-up on ${escapeDoughyHtml(page)} for ${escapeDoughyHtml(store)} — I do not see any obvious visible warning/open/failed items right now. Please review and confirm everything is updated.
                            </div>
                            <button type="button" class="doughy-copy-button" data-doughy-copy-draft>Copy draft</button>
                            <span class="doughy-context-muted">Draft is based only on visible page text. AI and send actions are still off.</span>
                        `;
                    } else {
                        soonBox.innerHTML = `
                            <strong>Follow-up draft</strong><br>
                            <div class="doughy-draft-box" id="doughyDraftText">
                                Quick follow-up on ${escapeDoughyHtml(page)} for ${escapeDoughyHtml(store)}. Please review the items below:<br>
                                ${attention.items.slice(0, 4).map(item => `• ${escapeDoughyHtml(item)}`).join("<br>")}<br>
                                Please update once complete.
                            </div>
                            <button type="button" class="doughy-copy-button" data-doughy-copy-draft>Copy draft</button>
                            <span class="doughy-context-muted">Draft is based only on visible page text. AI and send actions are still off.</span>
                        `;
                    }

                    soonBox.classList.add("open");
                    return;
                }

                soonBox.textContent = "Coming soon: Doughy will answer “" + prompt + "” using read-only BPI Ops data.";
                soonBox.classList.add("open");
            });
        });
    });


    function buildDoughyVisibleSnapshot() {
        const headingEl = document.querySelector("main h1, main h2, .page-title, .dashboard-title, h1, h2");
        const heading = headingEl ? headingEl.textContent.trim().replace(/\s+/g, " ") : "";

        const visible = function (el) {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== "none" &&
                style.visibility !== "hidden" &&
                rect.width > 0 &&
                rect.height > 0;
        };

        const statCards = Array.from(document.querySelectorAll(
            ".card, .stat-card, .metric-card, .dashboard-card, .summary-card, .tile, .kpi-card"
        )).filter(visible).length;

        const tables = Array.from(document.querySelectorAll("table")).filter(visible).length;

        const buttons = Array.from(document.querySelectorAll("button, .btn, a.btn"))
            .filter(visible)
            .filter(function (el) {
                return !el.closest(".doughy-mini-panel") && !el.closest(".doughy-mini-launcher");
            }).length;

        return {
            heading: heading,
            statCards: statCards,
            tables: tables,
            buttons: buttons
        };
    }


    function escapeDoughyHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function buildDoughyAttentionSnapshot() {
        const keywords = [
            "warning",
            "danger",
            "urgent",
            "overdue",
            "failed",
            "fail",
            "missing",
            "incomplete",
            "exception",
            "shortage",
            "late",
            "not completed",
            "needs attention",
            "not done",
            "pending",
            "unassigned",
            "unverified",
            "cash difference",
            "variance",
            "below goal",
            "low integrity",
            "not verified",
            "past due"
        ];

        const ignoredContainers = [
            ".doughy-mini-panel",
            ".doughy-mini-launcher",
            ".sidebar",
            ".topbar",
            ".navbar",
            ".nav",
            ".quick-actions",
            ".admin-actions",
            ".action-buttons",
            ".filters",
            ".filter-bar",
            ".pagination",
            ".dropdown-menu",
            ".menu",
            "header",
            "footer",
            "nav"
        ];

        const visible = function (el) {
            if (!el) return false;

            const isIgnored = ignoredContainers.some(function (selector) {
                return el.closest(selector);
            });

            if (isIgnored) return false;

            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();

            return style.display !== "none" &&
                style.visibility !== "hidden" &&
                rect.width > 0 &&
                rect.height > 0;
        };

        const cleanText = function (value) {
            return String(value || "").trim().replace(/\s+/g, " ");
        };

        const mostlyLinksOrButtons = function (el) {
            const text = cleanText(el.textContent);
            if (!text) return true;

            const linkButtonText = Array.from(el.querySelectorAll("a, button"))
                .map(child => cleanText(child.textContent))
                .filter(Boolean)
                .join(" ");

            if (!linkButtonText) return false;

            return linkButtonText.length >= text.length * 0.65;
        };

        const appRoot = document.querySelector(
            "main, .app-main, .app-content, .page-content, .content, .content-wrap, .dashboard-content, .shell-content, .main-content"
        ) || document.body;

        const candidates = Array.from(appRoot.querySelectorAll(
            [
                ".card",
                ".tile",
                ".panel",
                ".section",
                ".block",
                ".module",
                ".dashboard-card",
                ".summary-card",
                ".stat-card",
                ".metric-card",
                ".heatmap-card",
                ".exception-card",
                ".checklist-card",
                ".maintenance-card",
                ".store-card",
                ".checklist-section",
                ".checklist-item",
                ".daily-card",
                ".ops-card",
                ".glass-card",
                "[class*='card']",
                "[class*='panel']",
                "[class*='section']",
                "[class*='checklist']",
                "[class*='metric']",
                "[class*='summary']",
                "table tbody tr",
                ".table-row",
                "li"
            ].join(", ")
        )).filter(visible).filter(function (el) {
            return !mostlyLinksOrButtons(el);
        });

        const items = [];

        candidates.forEach(function (el) {
            const text = cleanText(el.textContent);
            if (!text || text.length < 4) return;
            if (text.length > 650) return;

            const lower = text.toLowerCase();
            const classHint = (el.className || "").toString().toLowerCase();

            const hasKeyword = keywords.some(function (keyword) {
                return lower.includes(keyword);
            });

            const hasBadClassHint =
                classHint.includes("warning") ||
                classHint.includes("danger") ||
                classHint.includes("error") ||
                classHint.includes("alert") ||
                classHint.includes("failed") ||
                classHint.includes("critical") ||
                classHint.includes("bad");

            const percentMatches = lower.match(/\b(\d{1,3})\s*%/g) || [];
            const hasLowPercent = percentMatches.some(function (match) {
                const num = parseInt(match.replace(/[^0-9]/g, ""), 10);
                return !Number.isNaN(num) && num < 80;
            });

            const hasMoneyConcern =
                /\$\s*\d+/.test(lower) &&
                (
                    lower.includes("short") ||
                    lower.includes("difference") ||
                    lower.includes("variance") ||
                    lower.includes("cash")
                );

            const hasOpenOpsConcern =
                /\b[1-9]\d*\b/.test(lower) &&
                (
                    lower.includes("open ticket") ||
                    lower.includes("open maintenance") ||
                    lower.includes("pending") ||
                    lower.includes("incomplete") ||
                    lower.includes("missing") ||
                    lower.includes("unverified")
                );

            const hasChecklistProgressConcern =
                (
                    lower.includes("completion") ||
                    lower.includes("integrity") ||
                    lower.includes("walk integrity") ||
                    lower.includes("before open") ||
                    lower.includes("checklist")
                ) &&
                (
                    lower.includes("0.0%") ||
                    lower.includes("0%") ||
                    /\b0\s*\/\s*\d+\b/.test(lower)
                );

            const hasVeryLowChecklistPercent =
                (
                    lower.includes("completion") ||
                    lower.includes("integrity") ||
                    lower.includes("checklist")
                ) &&
                percentMatches.some(function (match) {
                    const num = parseInt(match.replace(/[^0-9]/g, ""), 10);
                    return !Number.isNaN(num) && num < 50;
                });

            const pageText = (
                (document.getElementById("doughyContextBody") || {}).textContent || ""
            ).toLowerCase();

            const isChecklistPage = pageText.includes("daily checklist") || window.location.pathname.includes("checklist");
            const isMaintenancePage = pageText.includes("maintenance") || window.location.pathname.includes("maintenance");
            const isSvrPage = pageText.includes("svr") || window.location.pathname.includes("svr");
            const isCashPage = pageText.includes("cash") || window.location.pathname.includes("cash");
            const isDashboardPage = pageText.includes("dashboard") || window.location.pathname === "/" || window.location.pathname.includes("dashboard");

            const hasMaintenanceConcern =
                isMaintenancePage &&
                (
                    lower.includes("open") ||
                    lower.includes("in progress") ||
                    lower.includes("submitted") ||
                    lower.includes("unassigned") ||
                    lower.includes("overdue") ||
                    lower.includes("not verified")
                );

            const hasSvrConcern =
                isSvrPage &&
                (
                    lower.includes("fail") ||
                    lower.includes("missed") ||
                    lower.includes("needs follow") ||
                    lower.includes("action") ||
                    lower.includes("issue") ||
                    lower.includes("not complete")
                );

            const hasCashConcern =
                isCashPage &&
                (
                    lower.includes("short") ||
                    lower.includes("over") ||
                    lower.includes("variance") ||
                    lower.includes("difference") ||
                    lower.includes("review")
                );

            const hasDashboardConcern =
                isDashboardPage &&
                (
                    hasLowPercent ||
                    lower.includes("exception") ||
                    lower.includes("warning") ||
                    lower.includes("open") ||
                    lower.includes("failed") ||
                    lower.includes("needs attention")
                );

            if (
                hasKeyword ||
                hasBadClassHint ||
                hasLowPercent ||
                hasMoneyConcern ||
                hasOpenOpsConcern ||
                hasChecklistProgressConcern ||
                hasVeryLowChecklistPercent ||
                hasMaintenanceConcern ||
                hasSvrConcern ||
                hasCashConcern ||
                hasDashboardConcern
            ) {
                let cleaned = text;

                cleaned = cleaned
                    .replace(/\s*→\s*/g, " → ")
                    .replace(/\s+/g, " ")
                    .trim();

                if (cleaned.length > 150) {
                    cleaned = cleaned.slice(0, 147) + "...";
                }

                if (!items.includes(cleaned)) {
                    items.push(cleaned);
                }
            }
        });

        return {
            items: items.slice(0, 8),
            scanned: candidates.length
        };
    }

    async function loadDoughyContext() {
        const body = document.getElementById("doughyContextBody");
        if (!body) return;

        try {
            const currentPath = window.location.pathname || "/";
            const contextCard = document.getElementById("doughyContextCard");
            const endpoint = contextCard ? contextCard.dataset.endpoint || "" : "";
            const pageLabel = contextCard ? contextCard.dataset.pageLabel || "" : "";

            const headingEl = document.querySelector("main h1, main h2, .page-title, .dashboard-title, h1, h2");
            const visibleHeading = headingEl ? headingEl.textContent.trim().replace(/\s+/g, " ") : "";
            const browserTitle = document.title ? document.title.trim().replace(/\s+/g, " ") : "";

            const params = new URLSearchParams({
                path: currentPath,
                endpoint: endpoint,
                page_label: pageLabel,
                visible_heading: visibleHeading,
                browser_title: browserTitle
            });

            const response = await fetch(`/doughy/context?${params.toString()}`, {
                headers: {
                    "Accept": "application/json"
                },
                credentials: "same-origin"
            });

            if (!response.ok) {
                body.textContent = "Context unavailable.";
                return;
            }

            const data = await response.json();

            const page = data.page || "unknown";
            const role = data.role || "unknown";
            const store = data.store || "not set";
            const company = data.company_id || "not set";
            const resourceId = data.resource_id || "none";
            const view = data.visible_heading || data.browser_title || "";

            body.innerHTML = `
                Page: <strong>${page}</strong><br>
                ${view ? `View: <strong>${view}</strong><br>` : ""}
                Role: <strong>${role}</strong><br>
                Store: <strong>${store}</strong><br>
                Company: <strong>${company}</strong><br>
                Page ID: <strong>${resourceId}</strong>
            `;
        } catch (error) {
            body.textContent = "Context unavailable.";
        }
    }

    document.addEventListener("DOMContentLoaded", loadDoughyContext);
