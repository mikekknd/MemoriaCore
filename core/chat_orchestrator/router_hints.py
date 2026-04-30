"""Router context hints — 給 Router Agent 的位置／預設城市線索。

當 user 訊息語意上需要外部資料但缺少必要參數（最典型是 get_weather 的 city）時，
Router 透過這些線索判斷是否能從上下文補位；缺少可靠值時應改呼叫 direct_chat
（對應 prompt: prompts_default.json -> router_system 的工具參數提取原則）。
"""
import re

from core.prompt_utils import _is_su_private_weather_context

# 常見台灣城市名（中英對照），同行同義詞放一起，順序由長到短避免短前綴誤命中。
_LOCATION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'新北|New Taipei', re.IGNORECASE), "新北"),
    (re.compile(r'台北|臺北|Taipei', re.IGNORECASE), "台北"),
    (re.compile(r'桃園|Taoyuan', re.IGNORECASE), "桃園"),
    (re.compile(r'新竹|Hsinchu', re.IGNORECASE), "新竹"),
    (re.compile(r'苗栗|Miaoli', re.IGNORECASE), "苗栗"),
    (re.compile(r'台中|臺中|Taichung', re.IGNORECASE), "台中"),
    (re.compile(r'彰化|Changhua', re.IGNORECASE), "彰化"),
    (re.compile(r'南投|Nantou', re.IGNORECASE), "南投"),
    (re.compile(r'雲林|Yunlin', re.IGNORECASE), "雲林"),
    (re.compile(r'嘉義|Chiayi', re.IGNORECASE), "嘉義"),
    (re.compile(r'台南|臺南|Tainan', re.IGNORECASE), "台南"),
    (re.compile(r'高雄|Kaohsiung', re.IGNORECASE), "高雄"),
    (re.compile(r'屏東|Pingtung', re.IGNORECASE), "屏東"),
    (re.compile(r'宜蘭|Yilan', re.IGNORECASE), "宜蘭"),
    (re.compile(r'花蓮|Hualien', re.IGNORECASE), "花蓮"),
    (re.compile(r'台東|臺東|Taitung', re.IGNORECASE), "台東"),
    (re.compile(r'澎湖|Penghu', re.IGNORECASE), "澎湖"),
    (re.compile(r'金門|Kinmen', re.IGNORECASE), "金門"),
    (re.compile(r'馬祖|Matsu', re.IGNORECASE), "馬祖"),
    (re.compile(r'基隆|Keelung', re.IGNORECASE), "基隆"),
]


def _extract_locations(messages: list[dict]) -> list[str]:
    """從訊息列表偵測常見地點（中英對照）。命中順序保留先後（去重）。"""
    seen = set()
    out: list[str] = []
    for m in messages:
        content = m.get("content", "") or ""
        if not content:
            continue
        for pattern, label in _LOCATION_PATTERNS:
            if pattern.search(content) and label not in seen:
                seen.add(label)
                out.append(label)
    return out


def build_router_context_hints(
    session_messages: list[dict],
    user_prefs: dict | None = None,
    session_ctx: dict | None = None,
    profile_facts: list[dict] | None = None,
) -> dict:
    """組裝 router 用的上下文線索。

    回傳 dict 形如：
        {
            "user_profile_location": "台中",   # 來自 profile.basic_info.location
            "su_weather_city": "Taipei",       # 僅 SU private face 才注入
            "recent_mentions": "台中, 宜蘭",   # 最近 6 則訊息偵測到的地點（去重）
        }
    任何缺值的 key 不會出現。
    """
    hints: dict[str, str] = {}

    # 1) profile.basic_info 中與位置相關的欄位
    if profile_facts:
        for fact in profile_facts:
            if not isinstance(fact, dict):
                continue
            if fact.get("category") != "basic_info":
                continue
            key = (fact.get("fact_key") or "").lower()
            if key in ("location", "city", "residence", "address"):
                value = (fact.get("fact_value") or "").strip()
                if value:
                    hints["user_profile_location"] = value
                    break

    # 2) SU private face 才暴露 weather_city
    if _is_su_private_weather_context(session_ctx):
        wc = ((user_prefs or {}).get("weather_city") or "").strip()
        if wc:
            hints["su_weather_city"] = wc

    # 3) 最近 6 則訊息中明確提及的城市
    recent_locations = _extract_locations(session_messages[-6:] if session_messages else [])
    if recent_locations:
        hints["recent_mentions"] = ", ".join(recent_locations)

    return hints
