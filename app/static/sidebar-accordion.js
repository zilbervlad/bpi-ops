
document.addEventListener("DOMContentLoaded", function () {
    const nav = document.querySelector("#sidebar .nav");
    if (!nav) return;

    const storageKey = "bpiSidebarOpenSection";

    function sections() {
        return Array.from(
            nav.querySelectorAll(":scope > .nav-section")
        );
    }

    function getLabel(section) {
        return Array.from(section.children).find(function (child) {
            return child.classList &&
                child.classList.contains("nav-section-label");
        });
    }

    function labelText(section) {
        const label = getLabel(section);
        return label ? label.textContent.trim() : "";
    }

    function directLinks(section) {
        return Array.from(section.children).filter(function (child) {
            return child.tagName === "A";
        });
    }

    function sectionKey(section) {
        return labelText(section)
            .toLowerCase()
            .replace(/&/g, "and")
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-|-$/g, "");
    }

    /*
     * Move BPI Academy into Command and remove its old
     * one-item Development wrapper.
     */
    const commandSection = sections().find(function (section) {
        return labelText(section) === "Command";
    });

    const academySection = sections().find(function (section) {
        return directLinks(section).some(function (link) {
            return link.textContent.trim().includes("BPI Academy");
        });
    });

    if (
        commandSection &&
        academySection &&
        academySection !== commandSection
    ) {
        const academyLink = directLinks(academySection).find(function (link) {
            return link.textContent.trim().includes("BPI Academy");
        });

        if (academyLink) {
            commandSection.appendChild(academyLink);
        }

        if (directLinks(academySection).length === 0) {
            academySection.remove();
        }
    }

    /*
     * Merge a separate HR section into People & HR.
     */
    const peopleHrSection = sections().find(function (section) {
        return labelText(section) === "People & HR";
    });

    const hrSection = sections().find(function (section) {
        return labelText(section) === "HR";
    });

    if (
        peopleHrSection &&
        hrSection &&
        peopleHrSection !== hrSection
    ) {
        directLinks(hrSection).forEach(function (link) {
            peopleHrSection.appendChild(link);
        });

        hrSection.remove();
    }

    function closeOtherSections(exceptSection) {
        sections().forEach(function (section) {
            if (
                section !== exceptSection &&
                section.classList.contains("is-collapsible")
            ) {
                section.classList.remove("is-open");

                const label = getLabel(section);
                if (label) {
                    label.setAttribute("aria-expanded", "false");
                }
            }
        });
    }

    function setOpen(section, shouldOpen, remember) {
        if (!section.classList.contains("is-collapsible")) return;

        if (shouldOpen) {
            closeOtherSections(section);
        }

        section.classList.toggle("is-open", shouldOpen);

        const label = getLabel(section);

        if (label) {
            label.setAttribute(
                "aria-expanded",
                shouldOpen ? "true" : "false"
            );
        }

        if (remember) {
            if (shouldOpen) {
                localStorage.setItem(
                    storageKey,
                    sectionKey(section)
                );
            } else {
                localStorage.removeItem(storageKey);
            }
        }
    }

    sections().forEach(function (section) {
        const label = getLabel(section);
        const links = directLinks(section);
        const name = labelText(section);

        section.classList.remove(
            "is-open",
            "is-fixed-open",
            "is-collapsible",
            "is-single-link"
        );

        if (
            section.classList.contains("nav-logout-section")
        ) {
            section.classList.add("is-single-link");
            return;
        }

        if (name === "Command") {
            section.classList.add("is-fixed-open", "is-open");

            if (label) {
                label.setAttribute("aria-expanded", "true");
            }

            return;
        }

        if (links.length <= 1) {
            section.classList.add("is-single-link");
            return;
        }

        section.classList.add("is-collapsible");

        if (!label) return;

        label.setAttribute("role", "button");
        label.setAttribute("tabindex", "0");
        label.setAttribute("aria-expanded", "false");

        function toggle() {
            setOpen(
                section,
                !section.classList.contains("is-open"),
                true
            );
        }

        label.addEventListener("click", toggle);

        label.addEventListener("keydown", function (event) {
            if (
                event.key !== "Enter" &&
                event.key !== " "
            ) {
                return;
            }

            event.preventDefault();
            toggle();
        });
    });

    const activeSection = sections().find(function (section) {
        return Boolean(
            section.querySelector(":scope > a.active")
        );
    });

    if (
        activeSection &&
        activeSection.classList.contains("is-collapsible")
    ) {
        setOpen(activeSection, true, false);
    } else {
        const savedKey = localStorage.getItem(storageKey);

        const savedSection = sections().find(function (section) {
            return (
                section.classList.contains("is-collapsible") &&
                sectionKey(section) === savedKey
            );
        });

        if (savedSection) {
            setOpen(savedSection, true, false);
        }
    }
});

/*
 * Global POST-form submit guard.
 *
 * A slow email/PDF/upload request used to leave the submit button active,
 * so a second click could create a duplicate submission. Lock the form as
 * soon as the browser accepts a valid submit, show a clear busy state, and
 * block every later submit event until the page changes.
 *
 * Add data-allow-repeat-submit to a form if a future AJAX workflow genuinely
 * needs to submit the same form more than once without a page navigation.
 */
(function () {
    function getBusyText(form, button) {
        const override =
            (button && button.getAttribute("data-submitting-text")) ||
            form.getAttribute("data-submitting-text");

        if (override) return override;

        const label = button
            ? ((button.textContent || button.value || "").trim().toLowerCase())
            : "";

        if (label.includes("save") || label.includes("update")) {
            return "Saving…";
        }

        if (label.includes("upload")) {
            return "Uploading…";
        }

        return "Sending…";
    }

    function preserveSubmitterValue(form, submitter) {
        if (!submitter || !submitter.name || submitter.disabled) return;

        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = submitter.name;
        hidden.value = submitter.value || "";
        hidden.setAttribute("data-bpi-submitter-copy", "true");
        form.appendChild(hidden);
    }

    function lockSubmitButton(button, busyText) {
        if (!button) return;

        button.setAttribute("aria-busy", "true");
        button.setAttribute("aria-disabled", "true");
        button.disabled = true;

        if (button.tagName === "INPUT") {
            button.setAttribute("data-bpi-original-value", button.value || "");
            button.value = busyText;
            return;
        }

        button.setAttribute("data-bpi-original-html", button.innerHTML);
        button.textContent = busyText;
    }

    document.addEventListener("submit", function (event) {
        const form = event.target;

        if (!(form instanceof HTMLFormElement)) return;
        if ((form.method || "get").toLowerCase() !== "post") return;
        if (form.hasAttribute("data-allow-repeat-submit")) return;
        if (event.defaultPrevented) return;

        if (form.getAttribute("data-bpi-submitting") === "true") {
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }

        form.setAttribute("data-bpi-submitting", "true");
        form.setAttribute("aria-busy", "true");

        const submitter = event.submitter || form.querySelector(
            'button[type="submit"], input[type="submit"]'
        );

        preserveSubmitterValue(form, submitter);

        const busyText = getBusyText(form, submitter);
        const submitButtons = form.querySelectorAll(
            'button[type="submit"], input[type="submit"]'
        );

        submitButtons.forEach(function (button) {
            lockSubmitButton(button, busyText);
        });
    });
})();

/*
 * Checklist cash capture.
 *
 * "Count Till" and the 3-O'Clock "Dayshift Cash Out" task stay normal
 * checklist items. Checking either one opens a one-field cash-on-hand popup.
 * The cash value is saved into the existing CashLog table first; only after
 * that succeeds do we let the checklist's normal autosave mark the task done.
 */
document.addEventListener("DOMContentLoaded", function () {
    const checklistForm = document.getElementById("checklist-form");
    if (!checklistForm) return;

    const storeSelect = document.getElementById("store");
    const dateInput = document.getElementById("date");
    const openingManagerInput = document.getElementById("opening_manager");

    let activeCashCapture = null;

    function normalizeText(value) {
        return String(value || "")
            .toLowerCase()
            .replace(/[’']/g, "'")
            .replace(/[-_/]+/g, " ")
            .replace(/\s+/g, " ")
            .trim();
    }

    function getCashCaptureConfig(checkbox) {
        const card = checkbox.closest(".checklist-item-card");
        const taskEl = card ? card.querySelector(".checklist-task-text") : null;
        const sectionEl = checkbox.closest(".checklist-items");

        const task = normalizeText(taskEl ? taskEl.textContent : "");
        const section = normalizeText(
            sectionEl ? sectionEl.dataset.sectionName : ""
        );

        if (task.includes("count till")) {
            return {
                shiftType: "opening",
                title: "Morning Cash Count",
                eyebrow: "COUNT TILL",
                helper: "Enter the total cash on hand. This saves directly to Cash Review as the opening cash count.",
            };
        }

        const isThreeOClock =
            section.includes("3 o'clock restock") ||
            section.includes("3 o clock restock");

        const isDayshiftCashOut =
            task.includes("dayshift cash out") ||
            task.includes("day shift cash out") ||
            (task.includes("cash out") && task.includes("day"));

        if (isThreeOClock && isDayshiftCashOut) {
            return {
                shiftType: "midshift",
                title: "Dayshift Cash Out",
                eyebrow: "3 O'CLOCK CASH",
                helper: "Enter the total cash on hand. This saves directly to Cash Review as the midshift cash count.",
            };
        }

        return null;
    }

    function injectCashCaptureStyles() {
        if (document.getElementById("bpi-checklist-cash-style")) return;

        const style = document.createElement("style");
        style.id = "bpi-checklist-cash-style";
        style.textContent = `
            .bpi-cash-capture-modal {
                position: fixed;
                inset: 0;
                z-index: 10050;
                display: none;
                align-items: center;
                justify-content: center;
                padding: 20px;
                background: rgba(2, 8, 23, .72);
                backdrop-filter: blur(7px);
            }
            .bpi-cash-capture-modal.is-open { display: flex; }
            .bpi-cash-capture-card {
                width: min(100%, 420px);
                border: 1px solid rgba(96, 165, 250, .24);
                border-radius: 20px;
                padding: 22px;
                background: linear-gradient(180deg, #0d1a2f 0%, #08111f 100%);
                box-shadow: 0 26px 80px rgba(0, 0, 0, .48);
                color: #f8fafc;
            }
            .bpi-cash-capture-eyebrow {
                margin: 0 0 6px;
                color: #7dd3fc;
                font-size: 10px;
                font-weight: 900;
                letter-spacing: .14em;
                text-transform: uppercase;
            }
            .bpi-cash-capture-title {
                margin: 0;
                font-size: 23px;
                line-height: 1.12;
                letter-spacing: -.03em;
            }
            .bpi-cash-capture-helper {
                margin: 8px 0 18px;
                color: #a8bad3;
                font-size: 13px;
                line-height: 1.45;
            }
            .bpi-cash-capture-label {
                display: block;
                margin-bottom: 7px;
                color: #dce8f7;
                font-size: 12px;
                font-weight: 900;
            }
            .bpi-cash-capture-money {
                position: relative;
            }
            .bpi-cash-capture-money span {
                position: absolute;
                left: 14px;
                top: 50%;
                transform: translateY(-50%);
                color: #7dd3fc;
                font-size: 20px;
                font-weight: 900;
            }
            .bpi-cash-capture-input {
                width: 100%;
                min-height: 54px;
                box-sizing: border-box;
                border: 1px solid rgba(125, 211, 252, .24);
                border-radius: 14px;
                padding: 12px 14px 12px 34px;
                background: rgba(3, 10, 22, .92);
                color: #fff;
                font: inherit;
                font-size: 20px;
                font-weight: 850;
                outline: none;
            }
            .bpi-cash-capture-input:focus {
                border-color: rgba(56, 189, 248, .8);
                box-shadow: 0 0 0 4px rgba(56, 189, 248, .10);
            }
            .bpi-cash-capture-error {
                min-height: 18px;
                margin: 8px 0 0;
                color: #fca5a5;
                font-size: 12px;
                font-weight: 750;
            }
            .bpi-cash-capture-actions {
                display: grid;
                grid-template-columns: .8fr 1.2fr;
                gap: 9px;
                margin-top: 14px;
            }
            .bpi-cash-capture-actions button {
                min-height: 44px;
                border-radius: 12px;
                font: inherit;
                font-weight: 900;
                cursor: pointer;
            }
            .bpi-cash-capture-cancel {
                border: 1px solid rgba(148, 163, 184, .2);
                background: rgba(15, 23, 42, .72);
                color: #dbe7f5;
            }
            .bpi-cash-capture-save {
                border: 1px solid rgba(56, 189, 248, .25);
                background: linear-gradient(135deg, #1da7f0, #2563eb);
                color: #fff;
            }
            .bpi-cash-capture-save:disabled {
                opacity: .6;
                cursor: not-allowed;
            }
            @media (max-width: 520px) {
                .bpi-cash-capture-card { padding: 19px; }
                .bpi-cash-capture-actions { grid-template-columns: 1fr; }
                .bpi-cash-capture-cancel { order: 2; }
            }
        `;
        document.head.appendChild(style);
    }

    function buildCashCaptureModal() {
        injectCashCaptureStyles();

        const modal = document.createElement("div");
        modal.className = "bpi-cash-capture-modal";
        modal.setAttribute("aria-hidden", "true");

        modal.innerHTML = `
            <div class="bpi-cash-capture-card" role="dialog" aria-modal="true" aria-labelledby="bpi-cash-capture-title">
                <p class="bpi-cash-capture-eyebrow" id="bpi-cash-capture-eyebrow">CASH COUNT</p>
                <h3 class="bpi-cash-capture-title" id="bpi-cash-capture-title">Cash on Hand</h3>
                <p class="bpi-cash-capture-helper" id="bpi-cash-capture-helper"></p>

                <label class="bpi-cash-capture-label" for="bpi-cash-capture-input">Cash on Hand</label>
                <div class="bpi-cash-capture-money">
                    <span>$</span>
                    <input
                        id="bpi-cash-capture-input"
                        class="bpi-cash-capture-input"
                        type="number"
                        min="0"
                        step="0.01"
                        inputmode="decimal"
                        autocomplete="off"
                        placeholder="0.00"
                    >
                </div>
                <div class="bpi-cash-capture-error" id="bpi-cash-capture-error"></div>

                <div class="bpi-cash-capture-actions">
                    <button type="button" class="bpi-cash-capture-cancel" id="bpi-cash-capture-cancel">Cancel</button>
                    <button type="button" class="bpi-cash-capture-save" id="bpi-cash-capture-save">Save Cash</button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);
        return modal;
    }

    const modal = buildCashCaptureModal();
    const modalTitle = document.getElementById("bpi-cash-capture-title");
    const modalEyebrow = document.getElementById("bpi-cash-capture-eyebrow");
    const modalHelper = document.getElementById("bpi-cash-capture-helper");
    const cashInput = document.getElementById("bpi-cash-capture-input");
    const cashError = document.getElementById("bpi-cash-capture-error");
    const cancelButton = document.getElementById("bpi-cash-capture-cancel");
    const saveButton = document.getElementById("bpi-cash-capture-save");

    function closeCashCapture() {
        if (!modal) return;
        modal.classList.remove("is-open");
        modal.setAttribute("aria-hidden", "true");
        activeCashCapture = null;
    }

    function cancelCashCapture() {
        if (activeCashCapture && activeCashCapture.checkbox) {
            activeCashCapture.checkbox.checked = false;
        }
        closeCashCapture();
    }

    function openCashCapture(checkbox, config) {
        activeCashCapture = { checkbox: checkbox, config: config };

        checkbox.checked = false;
        modalTitle.textContent = config.title;
        modalEyebrow.textContent = config.eyebrow;
        modalHelper.textContent = config.helper;
        cashInput.value = "";
        cashError.textContent = "";
        saveButton.disabled = false;
        saveButton.textContent = "Save Cash";

        modal.classList.add("is-open");
        modal.setAttribute("aria-hidden", "false");

        window.setTimeout(function () {
            cashInput.focus();
        }, 80);
    }

    async function saveCashCapture() {
        if (!activeCashCapture) return;

        const amount = Number(cashInput.value);
        if (!cashInput.value.trim() || !Number.isFinite(amount) || amount < 0) {
            cashError.textContent = "Enter a valid cash-on-hand amount.";
            cashInput.focus();
            return;
        }

        const storeNumber = storeSelect ? String(storeSelect.value || "").trim() : "";
        const logDate = dateInput ? String(dateInput.value || "").trim() : "";
        const managerName = openingManagerInput
            ? String(openingManagerInput.value || "").trim()
            : "";

        if (!storeNumber || !logDate) {
            cashError.textContent = "Store or checklist date is missing.";
            return;
        }

        saveButton.disabled = true;
        saveButton.textContent = "Saving…";
        cashError.textContent = "";

        try {
            const response = await fetch("/cash/checklist-log", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    store_number: storeNumber,
                    log_date: logDate,
                    shift_type: activeCashCapture.config.shiftType,
                    cash_on_hand: amount,
                    manager_name: managerName,
                }),
            });

            const data = await response.json();

            if (!response.ok || !data.success) {
                cashError.textContent = data.error || "Cash could not be saved.";
                saveButton.disabled = false;
                saveButton.textContent = "Save Cash";
                return;
            }

            const checkbox = activeCashCapture.checkbox;
            closeCashCapture();

            checkbox.checked = true;
            checkbox.dataset.cashCaptureBypass = "true";
            checkbox.dispatchEvent(new Event("change", { bubbles: true }));
        } catch (error) {
            cashError.textContent = "Cash could not be saved. Check the connection and try again.";
            saveButton.disabled = false;
            saveButton.textContent = "Save Cash";
        }
    }

    document.addEventListener("change", function (event) {
        const checkbox = event.target;

        if (
            !(checkbox instanceof HTMLInputElement) ||
            !checkbox.classList.contains("live-checklist-box")
        ) {
            return;
        }

        if (checkbox.dataset.cashCaptureBypass === "true") {
            delete checkbox.dataset.cashCaptureBypass;
            return;
        }

        if (!checkbox.checked) return;

        const config = getCashCaptureConfig(checkbox);
        if (!config) return;

        event.preventDefault();
        event.stopImmediatePropagation();
        openCashCapture(checkbox, config);
    }, true);

    cancelButton.addEventListener("click", cancelCashCapture);
    saveButton.addEventListener("click", saveCashCapture);

    modal.addEventListener("click", function (event) {
        if (event.target === modal) {
            cancelCashCapture();
        }
    });

    cashInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            saveCashCapture();
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && modal.classList.contains("is-open")) {
            cancelCashCapture();
        }
    });
});
