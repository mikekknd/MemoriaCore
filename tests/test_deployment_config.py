"""deployment_config 單元測試 — 驗證 SU ID 讀取邏輯與 cache 行為。

覆盖：
1. get_su_user_id() cache 行為
2. invalidate_su_id_cache() 清除後重新讀取
3. resolve_context() 使用動態 SU ID（env > prefs.json）
4. SU_ID 為空時 resolve_context fallback 到 public/public
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGetSuUserIdCache:
    """get_su_user_id() 的 cache 行為測試"""

    def test_cached_value_returned_on_subsequent_calls(self):
        """驗證 module-level cache：第二次 call 不再 I/O"""
        from core import deployment_config as dc
        # 清除任何殘留 cache
        dc._cached_su_id = None

        first = dc.get_su_user_id()
        second = dc.get_su_user_id()

        assert first == second, "快取穩定後兩次 call 應回傳相同值"

    def test_invalidate_clears_cache(self):
        """invalidate_su_id_cache() 後，下次 get_su_user_id() 會重新讀取"""
        from core import deployment_config as dc
        dc._cached_su_id = None

        first = dc.get_su_user_id()
        dc.invalidate_su_id_cache()
        second = dc.get_su_user_id()

        # invalidate 不改值，只清 cache；重新讀取後應該還是同一個值
        assert first == second

    def test_empty_su_id_returns_empty_string(self):
        """無 env、prefs.json 也沒有 su_user_id → 回傳空字串"""
        from core import deployment_config as dc

        # 模擬兩者都無的環境
        old_env = os.environ.get("SU_USER_ID")
        if "SU_USER_ID" in os.environ:
            del os.environ["SU_USER_ID"]

        dc._cached_su_id = None

        # 蓋掉 prefs.json 讀取函式
        original = dc._load_su_user_id_from_prefs
        dc._load_su_user_id_from_prefs = lambda: ""

        result = dc.get_su_user_id()

        # 還原
        dc._load_su_user_id_from_prefs = original
        if old_env is not None:
            os.environ["SU_USER_ID"] = old_env

        assert result == "", "無任何來源時應回傳空字串"


class TestResolveContext:
    """resolve_context() 使用動態 SU ID 的驗證"""

    def teardown_method(self):
        from core import deployment_config as dc
        dc._cached_su_id = None

    def test_su_user_matches_private_visibility(self):
        """user_id == SU_USER_ID 時，回傳 ('private', 'private')"""
        from core import deployment_config as dc

        dc._cached_su_id = None
        old_env = os.environ.get("SU_USER_ID")
        os.environ["SU_USER_ID"] = "test-su-uid-12345"

        face, vis = dc.resolve_context("test-su-uid-12345", "telegram")

        if old_env is not None:
            os.environ["SU_USER_ID"] = old_env
        else:
            os.environ.pop("SU_USER_ID", None)

        assert face == "private"
        assert vis == "private"

    def test_non_su_user_gets_public_visibility(self):
        """user_id 不等於 SU_USER_ID 時，回傳 ('public', 'public')"""
        from core import deployment_config as dc

        dc._cached_su_id = None
        old_env = os.environ.get("SU_USER_ID")
        os.environ["SU_USER_ID"] = "su-only"

        face, vis = dc.resolve_context("other-user", "telegram")

        if old_env is not None:
            os.environ["SU_USER_ID"] = old_env
        else:
            os.environ.pop("SU_USER_ID", None)

        assert face == "public"
        assert vis == "public"

    def test_public_channel_always_public_visibility(self):
        """livestream / discord_public 頻道不論 SU 都走 public/public"""
        from core import deployment_config as dc

        dc._cached_su_id = None
        old_env = os.environ.get("SU_USER_ID")
        os.environ["SU_USER_ID"] = "su-id"

        for channel in ("livestream", "discord_public"):
            face, vis = dc.resolve_context("su-id", channel)
            assert face == "public", f"{channel} 應永遠回傳 public face"
            assert vis == "public", f"{channel} 應永遠回傳 public visibility"

        if old_env is not None:
            os.environ["SU_USER_ID"] = old_env
        else:
            os.environ.pop("SU_USER_ID", None)

    def test_empty_su_id_falls_back_to_public(self):
        """SU_USER_ID 為空時，即使是同樣的 user_id 也走 public/public（因為比對失敗）"""
        from core import deployment_config as dc

        dc._cached_su_id = None
        old_env = os.environ.get("SU_USER_ID")
        os.environ.pop("SU_USER_ID", None)

        # 蓋掉 prefs 讀取
        original = dc._load_su_user_id_from_prefs
        dc._load_su_user_id_from_prefs = lambda: ""

        face, vis = dc.resolve_context("any-user", "telegram")

        dc._load_su_user_id_from_prefs = original
        if old_env is not None:
            os.environ["SU_USER_ID"] = old_env

        assert face == "public"
        assert vis == "public"