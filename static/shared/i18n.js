(function(global) {
  const DEFAULT_LOCALE = 'zh-TW';
  const SUPPORTED_LOCALES = ['zh-TW', 'en-US'];
  const ALIASES = {
    'zh': 'zh-TW',
    'zh-tw': 'zh-TW',
    'zh-hant': 'zh-TW',
    'en': 'en-US',
    'en-us': 'en-US',
  };

  let locale = DEFAULT_LOCALE;
  let readyPromise = null;
  const catalogs = {};

  // 行為注意：前端採「寬鬆模式」，未知 locale 直接 fallback 到 DEFAULT_LOCALE，
  // 不會丟錯。後端 core/i18n.py::normalize_locale 採「嚴格模式」會 raise ValueError，
  // 用於 API validator 攔下非法輸入。兩者刻意不一致：寫入經由 backend validator 把關，
  // 顯示層則保持 graceful fallback。
  function normalizeLocale(value) {
    if (!value) return DEFAULT_LOCALE;
    const raw = String(value).trim();
    if (SUPPORTED_LOCALES.includes(raw)) return raw;
    return ALIASES[raw.toLowerCase().replace(/_/g, '-')] || DEFAULT_LOCALE;
  }

  async function loadCatalog(localeId) {
    if (catalogs[localeId]) return catalogs[localeId];
    const response = await fetch(`/static/locales/${localeId}.json`);
    if (!response.ok) throw new Error(`Failed to load locale catalog: ${localeId}`);
    catalogs[localeId] = await response.json();
    return catalogs[localeId];
  }

  async function init(options = {}) {
    if (readyPromise) return readyPromise;
    readyPromise = (async () => {
      let configuredLocale = options.locale;
      if (!configuredLocale && options.fetchConfig !== false) {
        try {
          const response = await fetch(`${API}/system/config`);
          if (response.ok) {
            const config = await response.json();
            configuredLocale = config.ui_locale;
          }
        } catch {}
        if (!configuredLocale) {
          try {
            const response = await fetch(`${API}/system/ui-locale`);
            if (response.ok) {
              const config = await response.json();
              configuredLocale = config.ui_locale;
            }
          } catch {}
        }
      }
      locale = normalizeLocale(configuredLocale || DEFAULT_LOCALE);
      await loadCatalog(DEFAULT_LOCALE);
      if (locale !== DEFAULT_LOCALE) {
        await loadCatalog(locale);
      }
      document.documentElement.lang = locale === DEFAULT_LOCALE ? 'zh-Hant' : 'en';
      return locale;
    })();
    return readyPromise;
  }

  function format(template, params = {}) {
    return String(template).replace(/\{([A-Za-z0-9_]+)\}/g, (match, key) => (
      Object.prototype.hasOwnProperty.call(params, key) ? params[key] : match
    ));
  }

  function t(key, params = {}, fallback = null) {
    const activeCatalog = catalogs[locale] || {};
    const defaultCatalog = catalogs[DEFAULT_LOCALE] || {};
    const template = activeCatalog[key] ?? defaultCatalog[key] ?? fallback ?? key;
    return format(template, params);
  }

  function apply(root = document) {
    root.querySelectorAll('[data-i18n]').forEach(el => {
      el.textContent = t(el.dataset.i18n);
    });
    root.querySelectorAll('[data-i18n-title]').forEach(el => {
      el.title = t(el.dataset.i18nTitle);
    });
    root.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      el.placeholder = t(el.dataset.i18nPlaceholder);
    });
  }

  global.MCI18N = {
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    init,
    t,
    apply,
    get locale() {
      return locale;
    },
  };
})(window);
