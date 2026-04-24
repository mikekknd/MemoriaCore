"""人格演化快照系統 — Path D 增量 trait 樹。

模組分工：
- ``constants``     — B' 休眠參數與 prompt 節流閾值。
- ``trait_diff``    — ``TraitDiff`` / ``TraitUpdate`` / ``NewTrait`` Pydantic 結構。
- ``extractor``     — ``TRAIT_V1_SCHEMA`` / ``TRAIT_VN_SCHEMA`` JSON schema 與
  ``parse_trait_v1`` / ``parse_trait_vn`` 容錯解析。
- ``lineage``       — BGE-M3 cosine fallback（LLM ``parent_key`` 填錯時的救援）。
- ``snapshot_store``— 薄殼層，組合上述模組並委由 ``StorageManager.save_trait_snapshot``
  原子寫入 + B' sweep。

SQLite 寫入統一透過 ``core.storage_manager.StorageManager`` 的
``SECTION: 人格演化 Snapshots`` 方法，不直接使用 sqlite3。
"""
