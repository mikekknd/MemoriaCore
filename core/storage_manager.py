# 【環境假設】：Python 3.12, numpy 庫可用。使用內建 sqlite3。支援 Schema Evolution。
from core.runtime_paths import runtime_file
from core.storage import (
    DEFAULT_SYSTEM_PROMPT,
    GLOBAL_TOPIC_CHARACTER_ID,
    MAINTENANCE_DROP_TABLE_ALLOWLIST,
    SHARED_MEMORY_CHARACTER_ID,
    SHARED_MEMORY_USER_ID,
    ConversationRepositoryMixin,
    CoreMemoryRepositoryMixin,
    MemoryBlockRepositoryMixin,
    MemoryInspectRepositoryMixin,
    MessageStatsRepositoryMixin,
    PersonaSnapshotRepositoryMixin,
    ProfileRepositoryMixin,
    StorageCommonMixin,
    TopicCacheRepositoryMixin,
    UserRepositoryMixin,
)


class StorageManager(
    StorageCommonMixin,
    MemoryBlockRepositoryMixin,
    CoreMemoryRepositoryMixin,
    ProfileRepositoryMixin,
    TopicCacheRepositoryMixin,
    MemoryInspectRepositoryMixin,
    UserRepositoryMixin,
    ConversationRepositoryMixin,
    MessageStatsRepositoryMixin,
    PersonaSnapshotRepositoryMixin,
):
    def __init__(
        self,
        prefs_file=None,
        history_file=None,
        persona_snapshot_db_path=None,
    ):
        self.prefs_file = prefs_file or runtime_file("user_prefs.json")
        self.history_file = history_file or runtime_file("chat_history.json")
        self.persona_snapshot_db_path = persona_snapshot_db_path or runtime_file("persona_snapshots.db")


__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "GLOBAL_TOPIC_CHARACTER_ID",
    "MAINTENANCE_DROP_TABLE_ALLOWLIST",
    "SHARED_MEMORY_CHARACTER_ID",
    "SHARED_MEMORY_USER_ID",
    "StorageManager",
]
