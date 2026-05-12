# YouTubeBridgeV2 — CLAUDE.md

## 先讀

本子專案目前仍在架構與文件規劃階段。其他 agent session 接手前，先讀：

1. `YouTubeBridgeV2/README.md`
2. `YouTubeBridgeV2/docs/architecture-index.md`
3. `YouTubeBridgeV2/docs/documentation-guidelines.md`
4. 任務相關的 `YouTubeBridgeV2/docs/modules/<module-name>.md`

若任務涉及 public function/class/endpoint/event payload，另讀 `YouTubeBridgeV2/docs/api-reference-index.md`。

## 專案定位

YouTubeBridgeV2 是全新的 YouTube live runtime 子專案，不是在舊 `YouTubeBridge/` 上補丁。舊 `YouTubeBridge/` 可作為 reference，但不可直接把舊 runtime 流程、Legacy no-plan director、舊 Topic Pack prompt injection 或 root-level facade/mixin 相容包袱搬進 V2。

## 工作規則

- 新增或修改架構前，先更新對應 module design。
- 實作 public entrypoint 後，必須同步更新 API reference index 或說明不需要更新的原因。
- 若程式或設計改變 phase lifecycle、Legacy 邊界、模組依賴、adapter side effects、UI/API 入口，必須同步更新 V2 文件。
- 實作模組前必須先依 module design 寫 implementation plan，並以 Red-Green-Refactor 展開；新增 public behavior 時先建立 red test，再寫最小實作。
- V2 文件使用繁體中文；symbol、type、endpoint、event type 保持原文。
- 本階段不要在沒有 module design 與 implementation plan 的情況下直接新增 runtime code。
