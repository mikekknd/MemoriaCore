import json
import re
from pathlib import Path

import pytest

import core.i18n as i18n


def test_normalize_locale_accepts_supported_aliases():
    assert i18n.normalize_locale(None) == "zh-TW"
    assert i18n.normalize_locale("zh") == "zh-TW"
    assert i18n.normalize_locale("zh-Hant") == "zh-TW"
    assert i18n.normalize_locale("en") == "en-US"
    assert i18n.normalize_locale("en_US") == "en-US"


def test_normalize_locale_rejects_unknown_locale():
    with pytest.raises(ValueError):
        i18n.normalize_locale("fr-FR")


def test_translation_falls_back_to_zh_tw_and_formats_params(monkeypatch, tmp_path):
    locale_dir = tmp_path / "locales"
    locale_dir.mkdir()
    (locale_dir / "zh-TW.json").write_text(
        json.dumps({"common.greeting": "你好，{name}。"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (locale_dir / "en-US.json").write_text(json.dumps({}), encoding="utf-8")

    monkeypatch.setattr(i18n, "_LOCALE_DIR", locale_dir)
    i18n.load_catalog.cache_clear()
    try:
        assert i18n.t("common.greeting", "en-US", name="Ada") == "你好，Ada。"
        assert i18n.t("missing.key", "en-US") == "missing.key"
    finally:
        i18n.load_catalog.cache_clear()


def test_static_locale_catalogs_cover_routing_task_labels():
    locale_root = Path("static") / "locales"
    zh = json.loads((locale_root / "zh-TW.json").read_text(encoding="utf-8"))
    en = json.loads((locale_root / "en-US.json").read_text(encoding="utf-8"))

    assert zh
    assert en

    task_keys = [
        "chat", "expand", "pipeline", "compress", "distill", "ep_fuse",
        "profile", "persona_sync", "persona_seed", "background_gather",
        "character_gen", "router", "group_router", "translate", "browser",
    ]
    for catalog in (zh, en):
        for task_key in task_keys:
            assert f"routing.tasks.{task_key}.desc" in catalog
            assert f"routing.tasks.{task_key}.help" in catalog


# 動態組合的 i18n key（無法靠 regex 從原始碼抓 literal），需在此明確列舉。
_KNOWN_DYNAMIC_KEYS: set[str] = {
    # routing_config.js / ui/routing.py 動態用 PROVIDER_LABEL_KEYS 對映
    "routing.provider.ollama",
    "routing.provider.llamacpp",
    "routing.provider.openai",
    "routing.provider.openrouter",
    # ui/settings.py 動態 f"settings.ui_locale.{loc}"
    "settings.ui_locale.zh-TW",
    "settings.ui_locale.en-US",
}

_TASK_KEYS_FOR_LINT = (
    "chat", "expand", "pipeline", "compress", "distill", "ep_fuse",
    "profile", "persona_sync", "persona_seed", "background_gather",
    "character_gen", "router", "group_router", "translate", "browser",
)


def _gather_referenced_keys() -> set[str]:
    """從 HTML / JS / Python 抓出所有 literal i18n key，並補上已知動態 key。"""
    keys: set[str] = set()

    # HTML data-i18n* 屬性
    attr_patterns = [
        re.compile(r'data-i18n="([^"]+)"'),
        re.compile(r'data-i18n-title="([^"]+)"'),
        re.compile(r'data-i18n-placeholder="([^"]+)"'),
    ]
    static_root = Path("static")
    html_files = list(static_root.glob("*.html"))
    for html in html_files:
        text = html.read_text(encoding="utf-8")
        for pat in attr_patterns:
            keys.update(pat.findall(text))

    # JS / inline JS 內的 MCI18N.t('xx.yy', ...) literal 呼叫
    js_pat = re.compile(r"""MCI18N\.t\(['"]([\w.\-]+\.[\w.\-]+)['"]""")
    js_files = html_files + list((static_root / "shared").glob("*.js"))
    for f in js_files:
        keys.update(js_pat.findall(f.read_text(encoding="utf-8")))

    # Python: t('xx.yy', ...) literal（排除 f-string、變數）
    py_pat = re.compile(r"""\bt\(['"]([\w.\-]+\.[\w.\-]+)['"]""")
    for py in (Path("ui") / "routing.py", Path("ui") / "settings.py"):
        keys.update(py_pat.findall(py.read_text(encoding="utf-8")))

    keys.update(_KNOWN_DYNAMIC_KEYS)
    for tk in _TASK_KEYS_FOR_LINT:
        keys.add(f"routing.tasks.{tk}.desc")
        keys.add(f"routing.tasks.{tk}.help")

    return keys


def test_locale_catalogs_cover_referenced_keys():
    """所有被原始碼引用的 i18n key 必須同時存在於 zh-TW 與 en-US catalog。"""
    locale_root = Path("static") / "locales"
    zh = json.loads((locale_root / "zh-TW.json").read_text(encoding="utf-8"))
    en = json.loads((locale_root / "en-US.json").read_text(encoding="utf-8"))

    referenced = _gather_referenced_keys()
    missing_zh = sorted(k for k in referenced if k not in zh)
    missing_en = sorted(k for k in referenced if k not in en)

    assert not missing_zh, f"zh-TW catalog missing keys: {missing_zh}"
    assert not missing_en, f"en-US catalog missing keys: {missing_en}"
