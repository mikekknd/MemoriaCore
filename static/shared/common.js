const API = '/api/v1';
const CSRF_KEY = 'mc_csrf';

function escapeHtml(s) {
  if (!s) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function toast(msg, type = 'info') {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

function apiErrorMessage(data, fallback = '操作失敗') {
  if (!data) return fallback;
  const detail = data.detail ?? data.error?.message ?? data.message;
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === 'string') return item;
      if (!item || typeof item !== 'object') return '';
      if (item.msg) return String(item.msg).replace(/^Value error,\s*/, '');
      if (item.message) return String(item.message);
      return JSON.stringify(item);
    }).filter(Boolean);
    return messages.join('；') || fallback;
  }
  if (typeof detail === 'object') {
    return detail.message || JSON.stringify(detail);
  }
  return String(detail);
}

const originalFetch = window.fetch.bind(window);
window.fetch = async function(url, opts = {}) {
  opts.credentials = opts.credentials || 'same-origin';
  const method = (opts.method || 'GET').toUpperCase();
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = sessionStorage.getItem(CSRF_KEY);
    opts.headers = opts.headers || {};
    if (csrf && !opts.headers['X-CSRF-Token']) {
      opts.headers['X-CSRF-Token'] = csrf;
    }
  }
  const response = await originalFetch(url, opts);
  if (response.status === 401 && !String(url).includes('/auth/login')) {
    if (window.top) {
      window.top.location.href = '/static/login.html';
    } else {
      window.location.href = '/static/login.html';
    }
  }
  return response;
};

function storeCsrf(token) {
  if (token) sessionStorage.setItem(CSRF_KEY, token);
}

function clearCsrf() {
  sessionStorage.removeItem(CSRF_KEY);
}
