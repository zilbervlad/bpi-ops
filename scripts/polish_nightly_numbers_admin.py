from pathlib import Path

path = Path("app/templates/nightly_numbers_admin.html")
text = path.read_text()
marker = "/* NIGHTLY ADMIN COMPACT POLISH */"

if marker in text:
    print("Nightly Numbers admin polish already applied.")
    raise SystemExit

needle = "{% block content %}"
if needle not in text:
    raise SystemExit("Could not find content block in nightly_numbers_admin.html")

css = r'''
<style>
    /* NIGHTLY ADMIN COMPACT POLISH */
    .nightly-admin-page {
        width: min(1380px, calc(100vw - 28px));
        margin: 0 auto;
        gap: 14px !important;
    }

    .nightly-admin-shell {
        gap: 14px !important;
    }

    .nightly-admin-command {
        padding: 16px 18px !important;
        border-radius: 18px !important;
        min-height: 0 !important;
    }

    .nightly-admin-command h2 {
        font-size: 25px !important;
        line-height: 1.05 !important;
        margin: 2px 0 4px !important;
        letter-spacing: -0.035em !important;
    }

    .nightly-admin-command .command-subtitle {
        font-size: 13px !important;
        margin: 0 !important;
        line-height: 1.35 !important;
    }

    .nightly-admin-panel {
        padding: 16px !important;
        border-radius: 18px !important;
    }

    .nightly-admin-panel .panel-head {
        margin-bottom: 12px !important;
        align-items: center !important;
    }

    .nightly-admin-panel h3 {
        font-size: 16px !important;
        margin-bottom: 3px !important;
    }

    .nightly-admin-muted {
        font-size: 13px !important;
        line-height: 1.35 !important;
    }

    .nightly-admin-toolbar {
        position: static !important;
        padding: 10px 12px !important;
        margin-bottom: 10px !important;
        border-radius: 14px !important;
    }

    .nightly-admin-toolbar-grid {
        grid-template-columns: minmax(220px, 1fr) 190px auto !important;
        gap: 10px !important;
    }

    .nightly-admin-toolbar label,
    .nightly-inline-field label {
        font-size: 10px !important;
        letter-spacing: .08em !important;
    }

    .nightly-admin-toolbar input,
    .nightly-admin-toolbar select,
    .nightly-inline-field input,
    .nightly-inline-field select {
        height: 36px !important;
        min-height: 36px !important;
        border-radius: 10px !important;
        font-size: 13px !important;
        padding: 0 10px !important;
    }

    .nightly-inline-note {
        margin-top: 6px !important;
        font-size: 12px !important;
    }

    .nightly-admin-stat,
    .nightly-admin-chip,
    .nightly-setting-status {
        min-height: 30px !important;
        padding: 7px 10px !important;
        border-radius: 10px !important;
        font-size: 12px !important;
    }

    .nightly-settings-list {
        gap: 7px !important;
    }

    .nightly-setting-card {
        padding: 9px 12px !important;
        border-radius: 13px !important;
    }

    .nightly-setting-head {
        margin-bottom: 6px !important;
        min-height: 26px !important;
    }

    .nightly-setting-title {
        font-size: 9px !important;
        margin-bottom: 1px !important;
    }

    .nightly-setting-subtitle {
        font-size: 13px !important;
        line-height: 1.2 !important;
        font-weight: 800 !important;
    }

    .nightly-setting-meta {
        gap: 5px !important;
    }

    .nightly-setting-meta .table-badge {
        min-height: 24px !important;
        padding: 0 8px !important;
        font-size: 10px !important;
        border-radius: 999px !important;
    }

    .nightly-setting-body {
        grid-template-columns: minmax(170px, .9fr) minmax(280px, 2fr) minmax(150px, .7fr) auto auto !important;
        gap: 8px !important;
        align-items: end !important;
    }

    .nightly-inline-field {
        gap: 3px !important;
    }

    .nightly-setting-flags,
    .nightly-setting-actions {
        min-height: 36px !important;
        padding-top: 0 !important;
        align-items: center !important;
    }

    .nightly-setting-flags {
        gap: 10px !important;
        flex-wrap: nowrap !important;
    }

    .nightly-setting-flags .checkbox-inline {
        font-size: 12px !important;
        white-space: nowrap !important;
    }

    .nightly-setting-status {
        min-width: 66px !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
    }

    @media (max-width: 1100px) {
        .nightly-setting-body {
            grid-template-columns: 1fr 1.6fr 1fr !important;
        }

        .nightly-setting-flags,
        .nightly-setting-actions {
            grid-column: auto !important;
        }
    }

    @media (max-width: 760px) {
        .nightly-admin-page {
            width: calc(100vw - 16px) !important;
        }

        .nightly-admin-toolbar-grid,
        .nightly-setting-body {
            grid-template-columns: 1fr !important;
        }

        .nightly-admin-toolbar-stats,
        .nightly-setting-actions {
            justify-content: flex-start !important;
        }

        .nightly-setting-flags {
            flex-wrap: wrap !important;
        }
    }
</style>
'''

text = text.replace(needle, needle + "\n" + css, 1)
path.write_text(text)
print(f"Polished {path}")
