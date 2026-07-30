
document.addEventListener("DOMContentLoaded", function () {
    const nav = document.querySelector("#sidebar .nav");
    if (!nav) return;

    const sections = Array.from(
        nav.querySelectorAll(":scope > .nav-section")
    );

    const storageKey = "bpiSidebarOpenSection";

    function directLinks(section) {
        return Array.from(section.children).filter(function (child) {
            return child.tagName === "A";
        });
    }

    function labelFor(section) {
        return Array.from(section.children).find(function (child) {
            return child.classList &&
                child.classList.contains("nav-section-label");
        });
    }

    function sectionKey(section) {
        const label = labelFor(section);

        return label
            ? label.textContent.trim().toLowerCase().replace(/\s+/g, "-")
            : "";
    }

    function closeCollapsibleSections(exceptSection) {
        sections.forEach(function (section) {
            if (
                section !== exceptSection &&
                section.classList.contains("is-collapsible")
            ) {
                section.classList.remove("is-open");

                const label = labelFor(section);
                if (label) {
                    label.setAttribute("aria-expanded", "false");
                }
            }
        });
    }

    function setOpen(section, shouldOpen, remember) {
        if (!section.classList.contains("is-collapsible")) return;

        if (shouldOpen) {
            closeCollapsibleSections(section);
        }

        section.classList.toggle("is-open", shouldOpen);

        const label = labelFor(section);
        if (label) {
            label.setAttribute(
                "aria-expanded",
                shouldOpen ? "true" : "false"
            );
        }

        if (remember) {
            if (shouldOpen) {
                localStorage.setItem(storageKey, sectionKey(section));
            } else {
                localStorage.removeItem(storageKey);
            }
        }
    }

    sections.forEach(function (section, index) {
        const links = directLinks(section);
        const label = labelFor(section);

        if (section.classList.contains("nav-logout-section")) {
            section.classList.add("is-single-link");
            return;
        }

        /*
         * Command stays permanently open.
         */
        if (index === 0) {
            section.classList.add("is-fixed-open", "is-open");

            if (label) {
                label.setAttribute("aria-expanded", "true");
            }

            return;
        }

        /*
         * Sections with one link do not need an accordion heading.
         */
        if (links.length <= 1) {
            section.classList.add("is-single-link");
            return;
        }

        section.classList.add("is-collapsible");

        if (!label) return;

        label.setAttribute("role", "button");
        label.setAttribute("tabindex", "0");
        label.setAttribute("aria-expanded", "false");

        function toggleSection() {
            const opening = !section.classList.contains("is-open");
            setOpen(section, opening, true);
        }

        label.addEventListener("click", toggleSection);

        label.addEventListener("keydown", function (event) {
            if (event.key !== "Enter" && event.key !== " ") return;

            event.preventDefault();
            toggleSection();
        });
    });

    /*
     * The current page always wins over saved preference.
     */
    const activeSection = sections.find(function (section) {
        return Boolean(section.querySelector(":scope > a.active"));
    });

    if (
        activeSection &&
        activeSection.classList.contains("is-collapsible")
    ) {
        setOpen(activeSection, true, false);
        return;
    }

    const savedKey = localStorage.getItem(storageKey);

    if (savedKey) {
        const savedSection = sections.find(function (section) {
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
