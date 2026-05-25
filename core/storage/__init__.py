"""StorageManager focused repository mixins。"""
from __future__ import annotations

from core.storage.common import StorageCommonMixin
from core.storage.conversation import ConversationRepositoryMixin
from core.storage.core_memory import CoreMemoryRepositoryMixin
from core.storage.inspect import MemoryInspectRepositoryMixin
from core.storage.memory_blocks import MemoryBlockRepositoryMixin
from core.storage.message_stats import MessageStatsRepositoryMixin
from core.storage.persona_snapshots import PersonaSnapshotRepositoryMixin
from core.storage.profiles import ProfileRepositoryMixin
from core.storage.topic_cache import TopicCacheRepositoryMixin
from core.storage.users import UserRepositoryMixin
from core.storage.constants import (
    DEFAULT_SYSTEM_PROMPT,
    GLOBAL_TOPIC_CHARACTER_ID,
    MAINTENANCE_DROP_TABLE_ALLOWLIST,
    SHARED_MEMORY_CHARACTER_ID,
    SHARED_MEMORY_USER_ID,
)

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "GLOBAL_TOPIC_CHARACTER_ID",
    "MAINTENANCE_DROP_TABLE_ALLOWLIST",
    "SHARED_MEMORY_CHARACTER_ID",
    "SHARED_MEMORY_USER_ID",
    "StorageCommonMixin",
    "MemoryBlockRepositoryMixin",
    "CoreMemoryRepositoryMixin",
    "ProfileRepositoryMixin",
    "TopicCacheRepositoryMixin",
    "MemoryInspectRepositoryMixin",
    "UserRepositoryMixin",
    "ConversationRepositoryMixin",
    "MessageStatsRepositoryMixin",
    "PersonaSnapshotRepositoryMixin",
]
