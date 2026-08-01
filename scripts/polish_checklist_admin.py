from pathlib import Path

path = Path("app/templates/checklist_admin.html")
text = path.read_text(encoding="utf-8")

marker = "/* CHECKLIST_ADMIN_COMPACT_V2 */"
if marker in text:
    print("Checklist Admin polish already applied.")
    raise SystemExit(0)

css = r'''
<style>
/* CHECKLIST_ADMIN_COMPACT_V2 */
.checklist-admin-page {
    max-width: 1380px;
    margin: 0 auto;
    gap: 14px !important;
}

.checklist-admin-shell {
    gap: 14px !important;
}

.checklist-admin-actions {
    gap: 8px !important;
    margin: 0 0 12px !important;
}

.checklist-admin-actions > a {
    min-height: 40px;
    padding: 9px 13px !important;
    border-radius: 12px !important;
}

.doughy-admin-button {
    box-shadow: none !important;
}

.checklist-admin-panel {
    padding: 16px 18px !important;
    border-radius: 18px !important;
}

.checklist-admin-panel .panel-head {
    margin-bottom: 12px !important;
    gap: 12px !important;
    align-items: center !important;
}

.checklist-admin-panel h3 {
    margin-bottom: 3px !important;
    font-size: 17px !important;
    letter-spacing: -0.02em;
}

.checklist-admin-muted {
    max-width: 980px;
    font-size: 13px !important;
    line-height: 1.4 !important;
    color: rgba(255,255,255,.62) !important;
}

.checklist-admin-grid {
    gap: 10px !important;
}

.checklist-admin-grid-3 {
    gap: 10px !important;
}

.checklist-admin-note-card {
    min-height: 72px;
    padding: 12px 14px !important;
    border-radius: 14px !important;
    display: grid;
    grid-template-columns: auto 1fr;
    column-gap: 9px;
    align-content: center;
}

.checklist-admin-note-card > input[type="checkbox"] {
    margin: 2px 0 0;
    align-self: start;
}

.checklist-admin-note-card > strong {
    font-size: 14px;
    line-height: 1.25;
}

.checklist-admin-note-card .checklist-admin-helper {
    grid-column: 2;
}

.checklist-admin-helper,
.checklist-inline-note {
    font-size: 12px !important;
    line-height: 1.35 !important;
    margin-top: 3px !important;
    color: rgba(255,255,255,.55) !important;
}

.checklist-admin-actions button,
.checklist-admin-actions .btn-primary,
.checklist-admin-actions .btn-secondary {
    min-height: 38px !important;
    padding: 8px 14px !important;
    border-radius: 12px !important;
    font-size: 13px !important;
}

.checklist-admin-command {
    padding: 16px 18px !important;
    gap: 12px !important;
}

.checklist-admin-note-card-title {
    font-size: 10px !important;
}

.checklist-admin-note-card-text {
    font-size: 13px !important;
    line-height: 1.4 !important;
}

.checklist-section-divider {
    margin: 8px 0 !important;
}

.checklist-admin-form-stack {
    gap: 10px !important;
}

.checklist-admin-panel input,
.checklist-admin-panel select,
.checklist-admin-panel textarea {
    min-height: 38px;
    border-radius: 11px !important;
}

.checklist-admin-panel label:not(.checklist-admin-note-card) {
    font-size: 12px;
}

.checklist-task-card {
    padding: 10px 12px !important;
    border-radius: 13px !important;
}

.checklist-task-inline-field input,
.checklist-task-inline-field select {
    height: 38px !important;
}

.checklist-admin-toolbar {
    padding: 12px !important;
    border-radius: 14px !important;
    margin-bottom: 12px !important;
}

.checklist-admin-stat,
.checklist-admin-chip {
    padding: 7px 10px !important;
    font-size: 12px !important;
}

@media (max-width: 760px) {
    .checklist-admin-page {
        max-width: none;
    }

    .checklist-admin-panel {
        padding: 14px !important;
    }

    .checklist-admin-grid,
    .checklist-admin-grid-3 {
        grid-template-columns: 1fr !important;
    }

    .checklist-admin-note-card {
        min-height: auto;
    }
}
</style>
'''

needle = '<div class="admin-page checklist-admin-page">'
if needle not in text:
    raise SystemExit("Could not find Checklist Admin page wrapper.")

text = text.replace(needle, css + "\n" + needle, 1)
path.write_text(text, encoding="utf-8")
print(f"Updated {path}")
