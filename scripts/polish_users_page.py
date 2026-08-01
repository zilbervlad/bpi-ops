from pathlib import Path

path = Path("app/templates/users.html")
text = path.read_text(encoding="utf-8")
marker = "    /* Users directory polish — compact desktop layout */"

if marker in text:
    print("Users page polish already applied.")
    raise SystemExit(0)

css = r'''

    /* Users directory polish — compact desktop layout */
    .premium-panel:has(.user-card-list) {
        max-width: 1380px;
        margin-left: auto !important;
        margin-right: auto !important;
        border-radius: 18px !important;
        box-shadow: 0 10px 28px rgba(15, 23, 42, 0.06) !important;
    }

    .premium-panel:has(.user-card-list) > .panel-head {
        padding: 15px 18px 9px !important;
    }

    .user-search-bar {
        grid-template-columns: minmax(300px, 1fr) auto !important;
        padding: 10px 18px 8px !important;
        gap: 8px !important;
    }

    .user-search-input,
    #userSearchClear,
    #userFilterClear {
        height: 38px !important;
        min-height: 38px !important;
        border-radius: 11px !important;
    }

    .user-filter-panel {
        grid-template-columns: minmax(0, 1fr) auto !important;
        align-items: center !important;
        gap: 10px !important;
        margin: 0 18px 10px !important;
        padding: 9px 10px !important;
        border-radius: 13px !important;
    }

    .user-filter-chip {
        padding: 7px 10px !important;
        font-size: 12px !important;
    }

    .user-filter-select {
        min-width: 132px !important;
        height: 36px !important;
        border-radius: 11px !important;
        font-size: 12px !important;
    }

    .user-directory-header,
    .user-row-grid {
        grid-template-columns: minmax(360px, 1fr) 145px 92px 104px 76px !important;
        gap: 10px !important;
    }

    .user-directory-header {
        padding: 8px 18px !important;
    }

    .user-card-summary {
        min-height: 50px !important;
        padding: 7px 18px !important;
    }

    .user-card:nth-child(even) {
        background: #fbfdff !important;
    }

    .user-card:hover {
        background: #f3f7fb !important;
    }

    .user-name-main {
        font-size: 13px !important;
        line-height: 1.1 !important;
    }

    .user-name-sub {
        gap: 6px !important;
        font-size: 10.5px !important;
        margin-top: 2px !important;
    }

    .user-row-role {
        gap: 4px !important;
        flex-wrap: nowrap !important;
        overflow: hidden;
    }

    .user-row-role .summary-pill,
    .user-row-status .status-badge,
    .user-expand-chip {
        height: 25px !important;
        min-height: 25px !important;
        padding: 0 8px !important;
        font-size: 11px !important;
    }

    .user-row-role .subtle-pill,
    .protected-pill {
        font-size: 10px !important;
    }

    .user-expand-chip {
        min-width: 64px !important;
        border-radius: 10px !important;
        letter-spacing: 0 !important;
    }

    .user-card[data-status="inactive"],
    .user-card.is-inactive {
        opacity: 0.72;
        background: #f8fafc !important;
    }

    @media (min-width: 981px) {
        .user-filter-controls {
            flex-wrap: nowrap !important;
        }
    }
'''

idx = text.find("</style>")
if idx == -1:
    raise SystemExit("Could not find closing </style> in users.html")

text = text[:idx] + css + "\n" + text[idx:]
path.write_text(text, encoding="utf-8")
print("Applied compact users directory polish to app/templates/users.html")
