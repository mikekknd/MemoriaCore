# YouTubeBridge Studio 第一階段接線 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 `/studio/` 的測試、角色、系統設定從本頁草稿改為後端持久化自動儲存。

**Architecture:** 新增 Studio 專用設定 API 聚合既有 Connector、Memoria Auth、persona overlay、TTS profile 與新的 `studio_settings` JSON section table。前端以獨立 helper 讀寫 `/studio/settings` 與既有 persona/TTS endpoints，不接直播啟動與 runtime 注入。

**Tech Stack:** FastAPI route modules、Pydantic v2 models、BridgeStorage SQLite repository mixins、vanilla HTML/CSS/JS。

---

## Tasks

- [ ] 新增 `StudioTestSettings`、`StudioDisplaySettings`、`StudioLiveDefaults`、`StudioSettingsPatch` model。
- [ ] 新增 `studio_settings` table 與 `BridgeStorage` repository methods。
- [ ] 新增 `GET /studio/settings` 與 `PATCH /studio/settings` route，聚合公開設定並保留 key/password 遮罩。
- [ ] 更新 Studio 前端，初始化讀後端設定，欄位變更後 500ms debounce autosave。
- [ ] 角色設定 autosave 寫既有 persona overlay / TTS profile endpoints，且固定 `mode="replace"`。
- [ ] 更新測試並跑指定回歸與 Browser QA。

## Boundaries

- 不接 `/sessions/current/start`。
- 不啟動或停止 runtime。
- 不讓自動留言真的注入 pending queue。
- 不接 Summary 實際生成。
- 不把 legacy Topic Pack 或 autonomous director UI 放回 Studio。
- 不改 legacy `/ui/` 行為。
