from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_db_maintenance_page_is_wired_into_dashboard():
    page_path = ROOT / "static" / "db_maintenance.html"
    assert page_path.exists()

    dashboard = _read("static/dashboard.html")
    assert 'data-tab="maintenance"' in dashboard
    assert 'src="/static/db_maintenance.html' in dashboard
    assert 'data-i18n="dashboard.tab.maintenance"' in dashboard
    assert 'data-i18n-title="dashboard.iframe.maintenance"' in dashboard


def test_db_maintenance_page_uses_admin_maintenance_api_only():
    html = _read("static/db_maintenance.html")

    assert '<script src="/static/shared/common.js' in html
    assert '<script src="/static/shared/i18n.js' in html
    assert "/memory/inspect/scopes" in html
    assert "/memory/inspect/" in html
    assert "/memory/maintenance/blocks/" in html
    assert "/memory/maintenance/core/" in html
    assert "/memory/maintenance/profile" in html
    assert "/memory/maintenance/topics/" in html
    assert "/memory/maintenance/refresh-cache" in html
    assert "/memory/maintenance/drop-table" in html
    assert "DROP COLUMN" not in html.upper()
    assert "ALTER TABLE" not in html.upper()


def test_db_maintenance_i18n_keys_exist():
    zh = _read("static/locales/zh-TW.json")
    en = _read("static/locales/en-US.json")

    for catalog in (zh, en):
        assert '"dashboard.tab.maintenance"' in catalog
        assert '"dashboard.iframe.maintenance"' in catalog
        assert '"db_maintenance.title"' in catalog
        assert '"db_maintenance.drop_table_confirm"' in catalog


def test_delete_button_does_not_require_typing_delete_first():
    html = _read("static/db_maintenance.html")

    assert 'id="deleteUnlock"' not in html
    assert "db_maintenance.delete_unlock" not in html
    assert "unlock_first" not in html
    assert "Type DELETE" not in html
    assert "${unlocked ? '' : 'disabled'}" not in html


def test_row_delete_does_not_show_confirm_dialog():
    html = _read("static/db_maintenance.html")
    zh = _read("static/locales/zh-TW.json")
    en = _read("static/locales/en-US.json")

    assert "db_maintenance.delete_confirm_message" not in html
    assert "const label = deleteLabel(row);" not in html
    assert '"db_maintenance.delete_confirm_message"' not in zh
    assert '"db_maintenance.delete_confirm_message"' not in en


def test_maintenance_mode_label_describes_pause_writes_behavior():
    zh = _read("static/locales/zh-TW.json")
    en = _read("static/locales/en-US.json")

    assert '"db_maintenance.edit_mode": "暫停一般寫入"' in zh
    assert '"db_maintenance.edit_mode": "Pause normal writes"' in en


def test_user_and_character_changes_auto_refresh_runtime_cache():
    html = _read("static/db_maintenance.html")

    assert '<select id="userSelect" onchange="onScopeChange()"></select>' in html
    assert '<select id="characterSelect" onchange="onScopeChange()"></select>' in html
    assert "async function onScopeChange()" in html
    assert "await refreshRuntimeCache({showToast: false});" in html
    assert "await loadRows();" in html
