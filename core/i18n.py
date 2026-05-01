"""i18n helper — UI 語系正規化與 catalog 查詢。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_LOCALE = "zh-TW"
SUPPORTED_LOCALES = ("zh-TW", "en-US")

_ALIASES = {
    "zh": "zh-TW",
    "zh-tw": "zh-TW",
    "zh-hant": "zh-TW",
    "zh_hant": "zh-TW",
    "zh_tw": "zh-TW",
    "en": "en-US",
    "en-us": "en-US",
    "en_us": "en-US",
}

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCALE_DIR = _PROJECT_ROOT / "static" / "locales"


def normalize_locale(locale: str | None) -> str:
    """將支援的 locale alias 正規化為 canonical locale id。

    行為注意：本函式對未知 locale 採「嚴格模式」直接 raise ValueError，
    用於 API validator 將非法輸入轉為 422；前端 ``static/shared/i18n.js``
    的同名實作採「寬鬆模式」silent fallback 到 ``DEFAULT_LOCALE``，
    用於避免顯示層因壞資料而炸掉。兩者刻意不一致。
    """
    if locale is None or str(locale).strip() == "":
        return DEFAULT_LOCALE

    raw = str(locale).strip()
    if raw in SUPPORTED_LOCALES:
        return raw

    normalized = _ALIASES.get(raw.lower())
    if normalized:
        return normalized

    supported = ", ".join(SUPPORTED_LOCALES)
    raise ValueError(f"unsupported locale '{raw}'. Supported locales: {supported}")


@lru_cache(maxsize=len(SUPPORTED_LOCALES))
def load_catalog(locale: str | None = DEFAULT_LOCALE) -> dict[str, str]:
    """從 static/locales 載入 locale catalog。"""
    locale_id = normalize_locale(locale)
    path = _LOCALE_DIR / f"{locale_id}.json"
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"locale catalog must be a JSON object: {path}")
    return {str(k): str(v) for k, v in data.items()}


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def t(key: str, locale: str | None = DEFAULT_LOCALE, params: dict[str, Any] | None = None, **kwargs: Any) -> str:
    """翻譯扁平 catalog key，fallback 順序為 zh-TW，再 fallback 到 key 本身。"""
    locale_id = normalize_locale(locale)
    template = load_catalog(locale_id).get(key)
    if template is None and locale_id != DEFAULT_LOCALE:
        template = load_catalog(DEFAULT_LOCALE).get(key)
    if template is None:
        template = key

    values = _SafeFormatDict()
    if params:
        values.update(params)
    values.update(kwargs)
    return template.format_map(values)
