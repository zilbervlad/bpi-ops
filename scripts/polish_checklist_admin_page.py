from pathlib import Path

path = Path("app/templates/checklist_admin.html")
text = path.read_text(encoding="utf-8")
marker = "BPI_CHECKLIST_ADMIN_COMPACT_V1"

if marker in text:
    print("Checklist admin polish already applied.")
    raise SystemExit(0)

css = r'''

/* BPI_CHECKLIST_ADMIN_COMPACT_V1 */
.checklist-admin-page {
    width: min(1380px, calc(100% - 24px));
    margin: 0 auto 32px;
    gap: 16px !important;
}

.checklist-admin-shell {
    gap: 16px !important;
}

.checklist-admin-actions {
    width: min(1380px, calc(100% - 24px));
    margin: 0 auto 14px !important;
    gap: 8px !important;
}

.checklist-admin-actions > a {
    min-height: 40px;
    padding: 0 14px !important;
    border-radius: 12px !important;
    display: inline-flex;
    align-items: center;
}

.checklist-admin-panel {
    padding: 18px !important;
    border-radius: 18px !important;
    box-shadow: 0 12px 30px rgba(2, 8, 23, .16) !important;
}

.checklist-admin-panel .panel-head {
    margin-bottom: 14px !important;
    gap: 12px !important;
}

.checklist-admin-panel h3 {
    font-size: 1.08rem !important;
    letter-spacing: -.025em;
}

.checklist-admin-muted {
    font-size: .88rem !important;
    line-height: 1.4 !important;
    max-width: 980px;
}

.checklist-admin-grid,
.checklist-admin-grid-3 {
    gap: 10px !important;
}

.checklist-admin-note-card {
    padding: 12px 14px !important;
    border-radius: 13px !important;
    min-height: 0 !important;
}

.checklist-admin-note-card strong {
    font-size: .94rem;
}

.checklist-admin-helper {
    margin-top: 4px !important;
    font-size: .78rem !important;
    line-height: 1.35 !important;
}

.checklist-admin-note-card input[type="checkbox"] {
    width: 15px;
    height: 15px;
    margin-right: 5px;
}

.checklist-admin-panel input,
.checklist-admin-panel select,
.checklist-admin-panel textarea {
    min-height: 40px !important;
    border-radius: 11px !important;
}

.checklist-admin-panel button,
.checklist-admin-panel .btn,
.checklist-admin-panel .btn-primary,
.checklist-admin-panel .btn-secondary {
    min-height: 40px !important;
    padding: 0 15px !important;
    border-radius: 11px !important;
    font-weight: 850 !important;
}

.checklist-admin-command {
    padding: 16px 18px !important;
    gap: 12px !important;
    border-radius: 18px !important;
}

.checklist-admin-toolbar {
    padding: 12px !important;
    border-radius: 14px !important;
    margin-bottom: 12px !important;
}

.checklist-task-card {
    padding: 10px 12px !important;
    border-radius: 13px !important;
}

.checklist-section-divider {
    margin: 0 !important;
}

@media (max-width: 900px) {
    .checklist-admin-page,
    .checklist-admin-actions {
        width: min(100% - 14px, 1380px);
    }

    .checklist-admin-panel {
        padding: 14px !important;
    }
}
'''

# Add the overrides at the end of the main page style block.
needle = "</style>\n\n<div class=\"admin-page checklist-admin-page\">"
if needle not in text:
    raise SystemExit("Could not locate checklist admin page style block.")

text = text.replace(needle, css + "\n</style>\n\n<div class=\"admin-page checklist-admin-page\">", 1)
path.write_text(text, encoding="utf-8")
print("Polished app/templates/checklist_admin.html")
