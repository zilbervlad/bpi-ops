
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
