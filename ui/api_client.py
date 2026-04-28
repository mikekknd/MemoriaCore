"""Streamlit 專用 FastAPI client。

每個 Streamlit browser session 都有自己的 ``st.session_state.api_session``。
這個模組提供 requests-like 函式，但不 monkeypatch 全域 requests 模組。
"""
from __future__ import annotations

import requests as _requests
import streamlit as st

ConnectionError = _requests.ConnectionError
Timeout = _requests.Timeout
Session = _requests.Session


def _session() -> _requests.Session:
    if "api_session" not in st.session_state:
        st.session_state.api_session = _requests.Session()
    return st.session_state.api_session


def _headers(extra=None, include_csrf: bool = False) -> dict:
    headers = dict(extra or {})
    csrf = st.session_state.get("api_csrf_token", "")
    if include_csrf and csrf and "X-CSRF-Token" not in headers:
        headers["X-CSRF-Token"] = csrf
    return headers


def get(url, **kwargs):
    return _session().get(url, **kwargs)


def post(url, **kwargs):
    kwargs["headers"] = _headers(kwargs.get("headers"), include_csrf=True)
    return _session().post(url, **kwargs)


def put(url, **kwargs):
    kwargs["headers"] = _headers(kwargs.get("headers"), include_csrf=True)
    return _session().put(url, **kwargs)


def delete(url, **kwargs):
    kwargs["headers"] = _headers(kwargs.get("headers"), include_csrf=True)
    return _session().delete(url, **kwargs)
