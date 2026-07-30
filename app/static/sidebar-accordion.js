
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
