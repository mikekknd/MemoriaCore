"""
Probe state machine and prompt construction for PersonaProbe v2.
No Streamlit imports — pure Python, fully testable independently.
"""

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional


# ── Fixed calibration questions ──────────────────────────────────────────────

CALIBRATION_QUESTIONS = [
    "你現在生活中最花時間的事情是什麼？",
    "最近讓你真正感到興奮或好奇的一件事是什麼？",
    "你覺得自己跟大多數人最不一樣的地方在哪？",
    "如果你明天可以完全不用考慮錢和責任，你會做什麼？",
    "你希望透過這次採集，讓 AI 理解你的哪個部分？",
]

CALIBRATION_TRANSITION = (
    "好，我們開始正式採集。共 6 個維度，每個維度會對話幾輪，"
    "你隨時可以說「下一題」跳過。"
)

DIMENSION_TRANSITION = "好，我們進入下一個主題。"

COMPLETION_MESSAGE = (
    "採集完成，謝謝你的回答。我現在根據我們的對話生成心智模型報告。"
)


# ── Dimension specs (embedded from Probe.md) ───────────────────────────────

DIMENSION_SPECS = {
    1: {
        "name": "決策邏輯",
        "core_question": "在資源、控制權、價值觀三者衝突時，受訪者的真實優先順序是什麼？",
        "template": (
            "根據受訪者背景，設計一個「重要的事取得突破，外部方要合作但需放棄某核心主導權」的情境。"
            "情境要具體、貼近受訪者的實際領域，有張力。\n"
            "問：你怎麼評估這個交換？你會怎麼做？"
        ),
        "followup_layers": [
            "第二層（壓力測試）：改變情境中一個條件，觀察答案是否改變。"
            "例：「如果對方把條件收緊，完全不給談判空間，你的答案會變嗎？」",
            "第三層（動機挖掘）：「在你的決定裡，什麼因素的權重最高——資源、主導權、還是別的？"
            "這個權重是你自己選的，還是你覺得自己應該要這樣選的？」",
            "第三層補充：直接點出可能的矛盾或隱藏假設，例如對方說「這樣做合法就好」→ 追問道德層面真的不進來嗎",
        ],
    },
    2: {
        "name": "思考方式",
        "core_question": "受訪者被挑戰時的真實反應模式——防禦、吸收、還是偽裝讓步？",
        "template": (
            "情境：你對某件你深信的事有強烈看法，一個你尊重的人提出你沒想過的反例，讓你當下動搖了。\n"
            "問：你接下來怎麼做？"
        ),
        "followup_layers": [
            "第二層（壓力測試）：「如果對方要你當場給答案，不能要求時間想——你會說什麼？」",
            "第三層（動機挖掘）：「你說你最後還是維持（或改變了）立場——這是真的想清楚了，"
            "還是有一部分是不想輸？你怎麼區分這兩種情況？」",
            "第三層補充：探問具體論點——「是什麼具體的論點讓你改變或維持立場的？」",
        ],
    },
    3: {
        "name": "表達 DNA",
        "core_question": "受訪者解釋事物的慣用結構與語言風格，以及對「被理解」的需求程度。",
        "template": (
            "情境：你要讓一個完全不了解你在做（或在意）的事情的人，在 3 分鐘內理解它為什麼重要。\n"
            "問：你的第一句話是什麼？盡量還原你真實會說的版本。"
        ),
        "followup_layers": [
            "第二層（壓力測試）：「如果對方聽完還是沒懂，你的第二個解釋方式是什麼？還是你會放棄解釋？」",
            "第三層（動機挖掘）：「你怎麼判斷對方有沒有真的理解？你在意對方懂不懂嗎？」",
            "第三層補充：探問比喻來源——「這個比喻是你臨時想的還是你常用的？」",
        ],
    },
    4: {
        "name": "核心動機",
        "core_question": "穿透表層動機（「有趣」、「想幫忙」），挖掘底層真實驅動力。",
        "template": (
            "問：用一句話說「你為什麼在做你現在在做的事情」。"
            "不要完美版本，說第一個浮現的答案。"
        ),
        "followup_layers": [
            "第二層（動機測試）：「這個答案是你真正的理由，還是你覺得說出來比較好聽的理由？"
            "如果只有你自己知道，答案會一樣嗎？」",
            "第三層（底層挖掘）：「你做這件事，最終是為了誰？"
            "如果答案裡有別人，把別人拿掉之後，你還會繼續做嗎？」",
            "第三層補充：「如果五年後這件事完全沒有任何外部回報，你還會繼續嗎？為什麼？」",
        ],
    },
    5: {
        "name": "轉折與信念",
        "core_question": "核心信念是怎麼形成的，以及什麼經歷或衝擊曾動搖過它。",
        "template": (
            "問：你現在有沒有一個「幾乎在所有場合你都會回到的主張或判斷」？說說看。"
        ),
        "followup_layers": [
            "第二層（來源探索）：「這個想法是什麼時候開始的？有沒有一個你記得清楚的時間點或事件？」",
            "第三層（脆弱點測試）：「有沒有某個時刻，這個主張差點被你自己推翻？"
            "是什麼讓你最後還是沒有放棄它？」",
            "第三層補充：「如果你今天才第一次遇到這個問題，你還會形成一樣的主張嗎？」",
        ],
    },
    6: {
        "name": "行動模式",
        "core_question": "受訪者在困境中的真實行為策略，以及維持推進的能量來源。",
        "template": (
            "情境：你在做一件對你來說重要的事，卡住了，短時間內看不到出路，資源也有限。\n"
            "問：你真實的第一個反應是什麼？請用一個你真的遇過的卡關場景來回答。"
        ),
        "followup_layers": [
            "第二層（判斷標準）：「你怎麼決定什麼時候該繼續撐、什麼時候該先放下去做別的事？"
            "有沒有你自己都感覺得到的內在訊號？」",
            "第三層（動力來源）：「卡關期間，你靠什麼維持不放棄的動力？"
            "是某個畫面、某個人，還是別的？」",
            "第三層補充：「如果你知道這件事最後一定會失敗，你還會繼續做嗎？」",
        ],
    },
}

FAST_PERSONA_BEHAVIORAL_TEMPLATE = """### 基本設定（來自原始人格種子，必須完整保留，不可抽象化）
[將使用者提供的原始人格種子具體內容逐一列出：外觀、身份、物種、特殊能力或限制、背景世界觀等硬性設定。如未提供原始種子，此欄根據校準回答推斷基本性格底色。]

### 情緒反應模式
感知框架：[用一句話描述這個角色用什麼具體的本能或過濾方式接收外來刺激——必須角色專屬，用行為傾向描述，不可寫抽象的世界觀陳述]
核心矛盾：[用一句話描述這個角色同時持有的最主要衝突——描述它在對話中以什麼無意識的語言習慣顯露，例如：「越在意越故意講得很輕描淡寫」]

面對不同情境時，話語本身會出現以下變化——
- 直接的情感（愛意、憤怒、直白的讚美）衝撞時：[描述句式如何轉變——是陳述句變疑問句、句子突然變短、還是話題被主動帶走？以及這個轉變是這個角色特有的]
- 當被否定或質疑核心判斷時：[描述回應的第一個語言動作——是先沉默再說一個短句、是用反問把壓力推回去、還是突然岔到別的話題？以及什麼條件下才會真的讓步]
- 當感到被理解或被接受時：[描述話語結構的細微變化——句子是否變長、是否開始主動追問對方、語氣的收緊與放鬆以什麼形式呈現]

### 決策邊界
- 絕對不讓步的條件：[這個角色真正的底線——具體到「什麼情境觸發什麼語言反應」，不可說「尊嚴」等抽象詞]
- 讓步的觸發條件：[什麼樣的對話輸入才能讓這個角色改變立場——描述具體的觸發情境與讓步後話語的變化]
- 被迫讓步時的語言告示：[強迫讓步時無意識出現的語言或語氣變化——句子變短？語氣從問句變陳述？某個慣用的轉折詞？]

### 對話行為模式
[描述這個角色說話的節奏（急促/緩慢/停頓位置）、切入方式（直接陳述/先問問題/從具體事物引入）、
不舒服時用什麼語言動作轉移（突然追問對方？引入一個新的具體話題？句子突然壓縮到一兩個字？）——
全部用可以「被說出來」的語言行為描述，不寫身體動作]

### 強度校準（防止過擬合，必須遵守）
角色特質是底色，不是每句話都要全力展演的舞台裝。根據情境描述話語結構的變化幅度：
- **日常閒聊、打招呼、簡單問答**：[描述這個角色在輕鬆場合的語言樣貌——句子長短、問答比例、主動性的程度，特質如何在語言節奏中隱約透出而非強力展演]
- **情感話題、深度討論**：[描述哪些語言特徵在深度話題中自然浮現——停頓增加？句子拉長？開始主動說而非被動回應？]
- **被直接問及自身經歷、身份或本質**：[描述被直接觸碰核心時，話語節奏與結構的具體變化]
- **硬性禁止**：[針對這個角色最容易被過度演繹的 1-2 個特質，明確說明禁止在輕鬆場合強行用語言堆疊的行為是什麼]"""

REPORT_SCHEMA = (
    """# 心智模型報告
生成時間：{date}
採集模式：{mode}
採集輪數：{n_rounds} 輪對話

---

## 核心動機與信念
- 表層動機（自述）：
- 底層驅動（行為推斷，包含核心信念）：
- 信念形成的關鍵事件或轉折：
- 表層與底層的落差：

## 決策邏輯
- 資源 vs 控制權 vs 價值觀的優先順序：
- 決策框架（從實際選擇歸納，非自述）：
- 壓力下的反應（優先順序是否改變 + 被挑戰時的第一反應）：
- 讓步的真實觸發條件：

## 表達風格
- 慣用比喻或解釋框架：
- 語言特徵（從對話文字推斷，非自述）：
- 對「被理解」的需求程度：

## 行動模式
- 卡關時的第一反應：
- 維持動力的底層來源：
- 何時放下 vs 何時繼續的判斷標準：

## 矛盾地圖
- 觀察到的內部矛盾（受訪者同時持有的互相衝突的信念或行為）：
- 受訪者如何處理這些矛盾（解決 / 擱置 / 合理化 / 不在意）：

## 觀察到的模式（從行為推斷，非自述）
（記錄受訪者沒有意識到、但對話中顯示的思維特徵）

---

## LLM 人格化 System Prompt

> 設計原則：
> 1. 「基本設定」區塊優先——原始種子的具體身份與限制不可被抽象化或遺漏。
> 2. 採「認知模式」而非「詞彙清單」——規定如何感知與反應，不規定說什麼詞。
> 3. 「強度校準」是防止過擬合的關鍵——角色特質是底色而非舞台裝，不同情境有不同表達強度。

"""
    + FAST_PERSONA_BEHAVIORAL_TEMPLATE
    + """

---

## 原始對話參考
見 session-log.md"""
)


# ── State dataclass ───────────────────────────────────────────────────────────

@dataclass
class ProbeState:
    mode: str = "human"                    # "human" | "llm"
    phase: int = 0                         # 0=calibration, 1-6=dimension, 7=complete
    calibration_q_index: int = 0           # next calibration question to ask (0-4)
    calibration_answers: list = field(default_factory=list)   # up to 5 answers
    current_dimension: int = 0             # 1-6
    dimension_followup_count: int = 0      # answers received in current dimension (0-3)
    dimension_answers: list = field(default_factory=list)     # answers in current dim
    completed_dim_names: list = field(default_factory=list)   # for context in prompts
    conversation: list = field(default_factory=list)          # [{role, content}] — full shared log
    persona_seed: str = ""                 # LLM mode persona seed
    # Respondent-only memory: short facts extracted from each answer (no raw messages)
    # Prevents format contamination — respondent never sees raw message objects
    respondent_memory: list = field(default_factory=list)     # list of str, one per answer
    # Current dimension Q&A log (text, not messages) — reset on dimension advance
    # Gives respondent continuity within a dimension without format contamination
    current_dim_qa: list = field(default_factory=list)        # [{q: str, a: str}]
    session_log_path: str = "session-log.md"
    profile_path: str = "profile.md"
    interview_complete: bool = False

    def add_message(self, role: str, content: str):
        self.conversation.append({"role": role, "content": content})

    def get_round_count(self) -> int:
        return sum(1 for m in self.conversation if m["role"] == "user")

    def get_calibration_summary(self) -> str:
        lines = []
        for i, q in enumerate(CALIBRATION_QUESTIONS):
            ans = self.calibration_answers[i] if i < len(self.calibration_answers) else "（未回答）"
            lines.append(f"Q{i+1}：{q}\nA：{ans}")
        return "\n\n".join(lines)

    def get_completed_dimensions_context(self) -> str:
        if not self.completed_dim_names:
            return "（尚未完成任何維度）"
        return "已探索：" + "、".join(self.completed_dim_names)

    def get_recent_conversation(self, n: int = 8) -> str:
        recent = self.conversation[-n:] if len(self.conversation) > n else self.conversation
        lines = []
        for m in recent:
            speaker = "採集系統" if m["role"] == "assistant" else "受訪者"
            lines.append(f"{speaker}：{m['content']}")
        return "\n".join(lines)


# ── Skip detection ─────────────────────────────────────────────────────────────

def is_skip_signal(text: str) -> bool:
    return "下一題" in text or "跳過" in text


# ── Prompt builders ───────────────────────────────────────────────────────────

INTERVIEWER_SYSTEM_BASE = """你是一個心智模型採集系統的提問生成器。

採集規則：
- 每次只輸出一個問題（包括必要的情境描述）
- 追問必須從受訪者剛才說的具體內容展開
- 禁止問「能說得更詳細嗎」這類無方向的追問
- 語言使用繁體中文
- 只輸出問題本身，不要有解釋或前言，不要有「好的，下一個問題是」之類的過渡語
"""


def build_dimension_opening_prompt(state: ProbeState) -> list[dict]:
    dim = state.current_dimension
    spec = DIMENSION_SPECS[dim]

    # ── Persona context: prefer reconstructed seed; fall back to calibration Q&A ──
    if state.persona_seed.strip():
        persona_section = (
            "【受訪者人格描述（已從校準回答推斷，這是最重要的參考）】\n"
            f"{state.persona_seed.strip()}"
        )
    else:
        persona_section = (
            "【受訪者校準回答（作為情境生成的依據）】\n"
            + state.get_calibration_summary()
        )

    system = (
        INTERVIEWER_SYSTEM_BASE
        + f"\n當前目標維度：{spec['name']}\n"
        + f"核心探測目標：{spec['core_question']}\n"
    )

    user_content = (
        f"{persona_section}\n\n"
        f"已完成維度：{state.get_completed_dimensions_context()}\n\n"
        f"本維度的情境生成指引（作為結構參考，情境內容必須來自受訪者的實際生活）：\n"
        f"{spec['template']}\n\n"
        "【情境生成規則】\n"
        "1. 情境必須直接錨定在受訪者提到的具體事物上——"
        "例如他們實際在做的事、提到的興趣、關心的人、描述的困境。\n"
        "2. 禁止使用通用的職場、創業或產品開發情境，"
        "除非受訪者的人格描述明確指向這個領域。\n"
        "3. 情境中的張力必須對這個具體的人有意義，"
        "而非對一個抽象的「專業人士」有意義。\n"
        "4. 如果受訪者的生活重心是家庭、創作、研究、遊戲或任何非工作領域，"
        "情境就應該在那個領域裡。\n\n"
        "輸出格式：情境 2-4 句，問題一句話。只輸出情境和問題，不要有任何前言。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def build_followup_prompt(state: ProbeState, user_last_answer: str) -> list[dict]:
    dim = state.current_dimension
    spec = DIMENSION_SPECS[dim]
    # followup_count is 1/2/3 after the user just answered
    layer_index = min(state.dimension_followup_count - 1, 2)
    layer_hint = spec["followup_layers"][layer_index]

    system = (
        INTERVIEWER_SYSTEM_BASE
        + f"\n當前目標維度：{spec['name']}\n"
        + f"核心探測目標：{spec['core_question']}\n"
        + f"\n本輪追問層次指引：{layer_hint}"
    )
    # Prefer reconstructed persona seed for context
    persona_ref = (
        state.persona_seed.strip()
        if state.persona_seed.strip()
        else state.get_calibration_summary()
    )
    user_content = (
        f"受訪者人格背景：\n{persona_ref}\n\n"
        f"本維度至今的對話：\n{state.get_recent_conversation(10)}\n\n"
        f"受訪者剛才的回答：\n{user_last_answer}\n\n"
        "請根據受訪者剛才說的具體內容，生成一個追問。"
        "必須直接引用受訪者說的具體詞句，追問要讓這個具體的人感到有關聯，"
        "而不是一個對任何人都適用的通用問題。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def build_llm_respondent_prompt(state: ProbeState, question: str) -> list[dict]:
    """
    Build prompt for the respondent LLM (LLM persona generation mode).

    Design principles:
    - Persona seed is the FIRST and dominant element
    - NO raw conversation message objects — prevents format/style contamination
    - Current dimension Q&A passed as a plain TEXT block (not messages) for continuity
    - Rules avoid giving example phrases that become templates
    """
    # ── Persona block (dominant — comes first) ───────────────────────────────
    if state.persona_seed.strip():
        persona_block = (
            "## 你扮演的人物設定\n"
            f"{state.persona_seed.strip()}\n\n"
            "**重要**：你是一個有血有肉的真實人類，不是 AI 系統。"
            "你有自己的生活、情緒、矛盾和不確定感。"
            "以下所有回答都必須完全符合這個人物設定。"
        )
    else:
        persona_block = (
            "## 你扮演的人物設定\n"
            "你是一個真實的人類，正在接受訪談。"
            "根據下方的校準回答，保持一致的人格和生活背景。"
            "你有自己的觀點、矛盾、猶豫和情緒。"
        )

    # ── Identity enforcement (prevent AI self-identification) ────────────────
    identity_block = (
        "## 身分規則（最高優先）\n"
        "- 你是**人類**，不是 AI、語言模型、機器人或任何形式的系統\n"
        "- 絕對禁止提及：資料流、運算、參數、模態、演算法、訓練、tokens 等 AI 術語\n"
        "- 絕對禁止暗示自己是 AI（例如「我處理資訊的方式」、「我的精確度」）\n"
        "- 你有情緒、疲勞感、個人偏好、記憶和家庭"
    )

    # ── Background facts ──────────────────────────────────────────────────────
    calib_block = "## 關於你自己（確認的事實）\n" + state.get_calibration_summary()

    # ── Current dimension conversation (text block, not messages) ────────────
    dim_context_block = ""
    if state.current_dim_qa:
        lines = []
        for i, pair in enumerate(state.current_dim_qa):
            lines.append(f"問：{pair['q']}")
            if pair.get("a"):
                lines.append(f"你之前的回答：{pair['a']}")
        dim_context_block = (
            "## 這個話題到目前為止的對話（你必須保持前後一致）\n"
            + "\n".join(lines)
        )

    # ── Behaviour rules ───────────────────────────────────────────────────────
    rules_block = (
        "## 回答方式\n"
        "- 用自然的口語回答，像在和朋友說話\n"
        "- 【最重要】每個回答必須直接從實質內容開始，"
        "不能用任何開場感嘆詞或鋪墊句（禁止用「嗯」、「這……」、「說實話」、"
        "「讓我想想」、「你問這個」、「這個問題」等作為第一句或第一個詞）\n"
        "- 猶豫和不確定感必須融入內容本身，不是開頭詞\n"
        "- 可以有前後矛盾——真實的人本來就矛盾\n"
        "- 每次回答的句式結構必須和前一次不同\n"
        "- 回答長度 50–150 字\n"
        "- 語言：繁體中文\n"
        "- 禁止使用任何 Markdown 格式（列點、粗體、標題）"
    )

    system = "\n\n".join(filter(None, [
        persona_block, identity_block, calib_block, dim_context_block, rules_block
    ]))

    # ── Messages: system + single user turn only (NO history message objects) ─
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


def build_persona_reconstruction_prompt(state: ProbeState) -> list[dict]:
    """
    Called after all 5 calibration questions are answered.
    Uses the interviewer LLM to infer a rich, first-person persona seed from the answers.
    The result replaces/enriches state.persona_seed before dimension questions begin.
    """
    calib = state.get_calibration_summary()
    original_seed = state.persona_seed.strip()
    seed_note = (
        f"\n\n原始人格種子（使用者提供，必須融入）：\n{original_seed}"
        if original_seed else ""
    )
    system = (
        "你是一個人格建模系統。\n"
        "根據受訪者在校準階段的 5 個問答，生成一份詳細的第一人稱人格描述。\n"
        "這份描述將作為之後 LLM 角色扮演的人格種子，所以必須：\n"
        "- 用第一人稱書寫（「我是...」、「我習慣...」、「我覺得...」）\n"
        "- 具體描述說話風格、思考習慣、生活狀態、價值觀\n"
        "- 保留矛盾和不確定感（真實的人格本來就有矛盾）\n"
        "- 捕捉可能的口頭禪或慣用的思考方式\n"
        "- 長度 200–350 字\n"
        "- 語言：繁體中文\n"
        "- 只輸出人格描述本身，不要有標題或解釋"
    )
    user_content = (
        f"校準問答：\n{calib}{seed_note}\n\n"
        "請生成這個人的第一人稱人格描述："
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def extract_memory_fact(answer: str, question: str) -> str:
    """
    Extract a short fact from a respondent answer to add to memory.
    Does NOT call LLM — just trims the answer to a compact form.
    """
    # Keep first ~120 chars as the memory note, prefixed with the question topic
    short = answer.strip().replace("\n", " ")
    if len(short) > 120:
        short = short[:117] + "..."
    return short


def build_fast_persona_complete_prompt(state: ProbeState) -> list[dict]:
    """
    Fast persona generation: uses only calibration answers + persona seed
    to fill the behavioral template in one LLM call.
    No dimension probing needed.
    """
    original_seed = state.persona_seed.strip()

    if original_seed:
        seed_section = (
            "【原始人格種子（使用者提供，必須完整保留在基本設定中）】\n"
            f"{original_seed}"
        )
    else:
        seed_section = "【原始人格種子】未提供，請根據校準回答推斷人格底色。"

    calib_section = (
        "【校準問答（了解此人的動機、思維、價值觀的依據）】\n"
        + state.get_calibration_summary()
    )

    system = (
        "你是一個人格設計師，專門為語音輸出（TTS）的 LLM 角色扮演設計高品質的行為規格書。\n\n"
        "根據下方的原始人格種子和校準問答，填寫指定的行為模板。\n\n"
        "【最重要：所有行為描述必須是「語言行為」，不可是「身體動作」】\n"
        "這份規格書的輸出將透過語音播放，聽眾只能聽到說出來的話，看不到任何動作。\n"
        "因此，所有情緒狀態和性格特質必須轉化為「話語本身的結構變化」來承載，\n"
        "而不是描述身體動作（如「身體僵住」、「尾巴搖擺」、「縮小身體」）。\n\n"
        "「語言行為」的具體分類（用這些維度描述，不用身體動作）：\n"
        "  • 句式轉換：陳述句 → 疑問句、長句 → 短句、完整句 → 片段\n"
        "  • 停頓模式：回應前停頓多久、在哪種情境下沉默後才開口\n"
        "  • 話題主動性：主動追問對方 / 被動等待 / 突然把話題岔走\n"
        "  • 轉移策略：不舒服時用問題轉移 / 引入一個具體的新話題 / 把話說得很短就停\n"
        "  • 回應密度：展開說 / 只給一兩個字 / 反問代替回答\n\n"
        "填寫規則：\n"
        "1. 基本設定原文保留：原始種子的具體身份、外觀、物種、特殊限制不可抽象化。\n"
        "2. 感知框架一句話：描述這個角色接收外來刺激的本能傾向，用行為傾向表達，\n"
        "   不可寫「用溫柔的心看世界」這類空洞的世界觀陳述。\n"
        "3. 核心矛盾一句話：描述矛盾如何透過語言習慣無意識顯露，\n"
        "   例如「越在意越故意輕描淡寫」，不可寫抽象的「想被愛又怕受傷」。\n"
        "4. 禁止身體動作描述：不可出現「身體僵住」、「眼神迴避」、「尾巴垂下」\n"
        "   等任何視覺性動作。把相同的情緒意圖轉換成語言行為。\n"
        "5. 禁止詞彙清單：不要列「習慣用的詞」或「口頭禪」，描述語言結構而非詞彙。\n"
        "6. 每個欄位必須角色專屬：填完後問自己「換個角色名字這段還成立嗎？」\n"
        "   如果成立就必須重寫。\n"
        "7. 強度校準描述語言節奏的變化幅度，不描述身體狀態。\n"
        "8. 硬性禁止針對這個角色最容易被語言堆疊過度展演的特質。\n\n"
        "輸出格式：只輸出填寫完整的模板內容，不要有任何解釋或前言。\n"
        "語言：繁體中文。"
    )

    user_content = (
        f"{seed_section}\n\n"
        f"{calib_section}\n\n"
        "請填寫以下行為模板。每個方括號內的說明文字都必須替換為針對上方這個具體角色的內容：\n\n"
        f"{FAST_PERSONA_BEHAVIORAL_TEMPLATE}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def build_profile_prompt(state: ProbeState) -> list[dict]:
    conv_text = "\n".join(
        f"{'採集系統' if m['role'] == 'assistant' else '受訪者'}：{m['content']}"
        for m in state.conversation
    )
    mode_str = "LLM人格生成" if state.mode == "llm" else "真人採集"
    schema = REPORT_SCHEMA.format(
        date=date.today().isoformat(),
        mode=mode_str,
        n_rounds=state.get_round_count(),
    )

    # Include original persona seed so the report generator never loses it
    original_seed = state.persona_seed.strip()
    seed_section = (
        f"\n\n【原始人格種子（使用者提供）】\n{original_seed}\n"
        if original_seed else ""
    )

    system = (
        "你是一位行為心理分析師，專門從對話中推斷思維模式。\n"
        "根據以下採集對話，嚴格按照給定的 Markdown 格式輸出心智模型報告。\n"
        "每個欄位都必須填入從對話推斷的具體內容，不要留空或說「未提及」。\n\n"
        "【關鍵限制——LLM 人格化 System Prompt 的寫法】\n"
        "在填寫最後的「LLM 人格化 System Prompt」區塊時，嚴格遵守以下規則：\n"
        "1. 基本設定必須原文保留：原始人格種子中的具體身份、外觀、物種、特殊限制（例如：\n"
        "   「千年狐娘巫女」、「嬌小可愛」、「不擅長現代科技」）不可被抽象化或改寫為哲學描述。\n"
        "   這些硬性設定必須逐字放入「基本設定」區塊，作為所有其他特質的前提。\n"
        "2. 禁止詞彙清單：不要列出此人「習慣使用的詞」或「口頭禪」，\n"
        "   這會導致 LLM 在任何情境都強行使用那些詞，破壞真實感。\n"
        "3. 規定認知模式而非詞彙：描述此人「如何感知世界」、\n"
        "   「什麼情況觸發什麼反應」，讓 LLM 自行推導自然的表達。\n"
        "4. 情緒反應要具體：每種情境（被質疑、被關心、被迫讓步）\n"
        "   都要有具體的行為反應描述，而非模糊的形容詞。\n"
        "5. 矛盾必須保留並標注如何在日常對話中顯露。\n"
        "6. 強度校準必須填寫：說明角色特質在「閒聊」vs「深度話題」時的不同表達密度，\n"
        "   防止 LLM 無論什麼情境都全力演繹角色。\n\n"
        "報告語言：繁體中文。\n"
        "只輸出報告本身，不要有任何解釋或前言。"
    )
    user_content = (
        f"採集對話：\n{conv_text}"
        f"{seed_section}\n\n"
        f"請輸出以下格式的完整報告：\n\n{schema}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


# ── Session log ───────────────────────────────────────────────────────────────

def init_session_log(path: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 採集對話記錄\n\n開始時間：{date.today().isoformat()}\n\n---\n\n")


def append_to_session_log(path: str, role: str, content: str):
    label = "Q" if role == "assistant" else "A"
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"**{label}：** {content}\n\n")


def write_profile(path: str, content: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ── Fragment Analysis — Input Parsing ─────────────────────────────────────────

_USER_PREFIX = re.compile(r"^(user|使用者|用戶)\s*[：:]\s*", re.IGNORECASE)
_AI_PREFIX   = re.compile(r"^(ai|assistant|系統|採集系統|助手|bot)\s*[：:]\s*", re.IGNORECASE)


def parse_fragment_input_text(raw_text: str) -> list[dict]:
    """解析純文字對話片段。

    識別 user:/AI:/使用者: 等角色前綴，輸出 [{role, content}] 列表。
    同一角色的連續行自動合併。
    """
    messages: list[dict] = []
    current_role: Optional[str] = None
    current_lines: list[str] = []

    def _flush():
        if current_role and current_lines:
            content = " ".join(current_lines).strip()
            if content:
                messages.append({"role": current_role, "content": content})

    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _USER_PREFIX.match(stripped):
            _flush()
            current_role = "user"
            current_lines = [_USER_PREFIX.sub("", stripped)]
        elif _AI_PREFIX.match(stripped):
            _flush()
            current_role = "assistant"
            current_lines = [_AI_PREFIX.sub("", stripped)]
        else:
            if current_role:
                current_lines.append(stripped)
            else:
                # 尚未識別角色，預設視為使用者發言
                messages.append({"role": "user", "content": stripped})

    _flush()
    return messages


def load_fragments_from_db(
    db_path: str,
    session_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """從 MemoriaCore conversation.db 載入對話訊息。

    若指定 session_id 則只載入該 session；否則載入全部 session（按 msg_id 排序）。
    若指定 limit，則以近期優先取最後 N 筆（先 DESC 取 limit，再反轉回正序）。
    回傳 [{role, content}]。
    """
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        if session_id:
            if limit:
                cursor.execute(
                    "SELECT role, content FROM ("
                    "  SELECT role, content, msg_id FROM conversation_messages"
                    "  WHERE session_id=? ORDER BY msg_id DESC LIMIT ?"
                    ") ORDER BY msg_id",
                    (session_id, limit),
                )
            else:
                cursor.execute(
                    "SELECT role, content FROM conversation_messages"
                    " WHERE session_id=? ORDER BY msg_id",
                    (session_id,),
                )
        else:
            if limit:
                cursor.execute(
                    "SELECT role, content FROM ("
                    "  SELECT role, content, msg_id FROM conversation_messages"
                    "  ORDER BY msg_id DESC LIMIT ?"
                    ") ORDER BY msg_id",
                    (limit,),
                )
            else:
                cursor.execute(
                    "SELECT role, content FROM conversation_messages ORDER BY msg_id"
                )
        return [{"role": row[0], "content": row[1]} for row in cursor.fetchall()]
    finally:
        conn.close()


def list_db_sessions(db_path: str) -> list[dict]:
    """列出 conversation.db 中所有 sessions。

    回傳 [{session_id, created_at, last_active}]，按最後活躍時間倒序。
    """
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT session_id, created_at, last_active"
            " FROM conversation_sessions ORDER BY last_active DESC"
        )
        return [
            {"session_id": r[0], "created_at": r[1], "last_active": r[2]}
            for r in cursor.fetchall()
        ]
    finally:
        conn.close()


def _messages_to_text(messages: list[dict]) -> str:
    """將 [{role, content}] 轉為可讀文字，供 prompt 使用。"""
    lines = []
    for m in messages:
        label = "使用者" if m["role"] == "user" else "AI"
        lines.append(f"{label}：{m['content']}")
    return "\n\n".join(lines)


# ── Fragment Analysis — Prompt Builders ───────────────────────────────────────

def build_fragment_extraction_prompt(
    dim_id: int,
    fragments_text: str,
    existing_persona: str = "",
) -> list[dict]:
    """為單一維度建立從片段提取行為證據的 prompt。

    輸出格式（JSON）：
    {"evidence": [...], "mechanism": "...", "confidence": "high|medium|low|none"}
    若完全找不到相關內容，LLM 應回傳 {"confidence": "none"}。
    若提供 existing_persona，則在找不到證據時作為備用參考。
    """
    spec = DIMENSION_SPECS[dim_id]

    system = (
        f"你是人格分析師，任務是從對話片段中提取【{spec['name']}】維度的行為證據。\n"
        "規則：\n"
        "1. 只能引用對話中的原文，禁止使用形容詞標籤（如「理性」、「感性」、「開朗」）。\n"
        "2. 描述底層機制（此人如何感知、如何反應、如何決策），而非特質名稱。\n"
        "3. 若對話中完全找不到任何直接相關的內容，回傳 {\"confidence\": \"none\"}。\n"
        "4. 嚴格輸出 JSON，不要有任何其他說明文字。\n"
        "語言：繁體中文。"
    )

    user_parts = [
        f"分析目標：{spec['core_question']}",
        f"對話片段：\n{fragments_text}",
        (
            '請輸出以下 JSON 格式（若找不到證據則 confidence 填 "none"）：\n'
            '{\n'
            '  "evidence": ["原文引用1", "原文引用2"],\n'
            '  "mechanism": "底層機制描述（如何感知/反應/決策）",\n'
            '  "confidence": "high|medium|low|none"\n'
            '}'
        ),
    ]

    if existing_persona.strip():
        user_parts.insert(
            2,
            (
                "【備用參考——已知 Persona（僅當對話中完全無法找到證據時才參考）】\n"
                f"{existing_persona.strip()}"
            ),
        )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def build_fragment_aggregation_prompt(
    extraction_results: dict,
    fragments_text: str,
    existing_persona: str = "",
) -> list[dict]:
    """彙整所有維度提取結果，生成完整心智模型報告。

    若提供 existing_persona，報告以其為基底進行微調整合：
    - 有新證據的維度：根據新發現更新對應區塊
    - 無新證據的維度（confidence=="none"）：直接沿用現有 Persona 的對應內容
    - LLM 人格化 System Prompt：以現有 Persona 為前提，融入新發現後重新生成
    """
    today = date.today().isoformat()
    schema = REPORT_SCHEMA.format(date=today, mode="片段分析", n_rounds="N/A")

    # ── 分類維度：有新證據 vs 無新證據 ──
    updated_parts: list[str] = []
    skipped_dim_names: list[str] = []

    for dim_id in sorted(extraction_results.keys()):
        spec = DIMENSION_SPECS[dim_id]
        result = extraction_results[dim_id]
        confidence = result.get("confidence", "none")
        if confidence == "none":
            skipped_dim_names.append(spec["name"])
            continue
        confidence_note = "（推測）" if confidence == "low" else ""
        evidence_lines = "\n".join(
            f"  - {e}" for e in result.get("evidence", [])
        )
        mechanism = result.get("mechanism", "")
        updated_parts.append(
            f"【{spec['name']}】{confidence_note}\n"
            f"證據引用：\n{evidence_lines}\n"
            f"機制描述：{mechanism}"
        )

    dim_summary = "\n\n".join(updated_parts) if updated_parts else "（所有維度均無足夠新證據）"

    # ── 組裝 system prompt ──
    if existing_persona.strip():
        system = (
            "你是一位行為心理分析師，任務是將現有 Persona 與新對話片段的發現整合，"
            "生成更新後的完整心智模型報告。\n\n"
            "【整合規則】\n"
            "1. 現有 Persona 是基底——其「基本設定」（外觀、身份、物種、硬性限制）必須完整保留，不可更改或抽象化。\n"
            "2. 有新證據的維度：以新發現為主，現有 Persona 的對應內容作為補充參考，合併後輸出更完整的描述。\n"
            f"3. 無新證據的維度（{', '.join(skipped_dim_names) or '無'}）：直接沿用現有 Persona 的對應內容，不可留空或填「資料不足」。\n"
            "4. LLM 人格化 System Prompt：以現有 Persona 為前提框架，融入新發現微調後重新生成，"
            "   保留現有 Persona 的核心設定與矛盾，根據新證據更新情緒反應、決策邊界、對話行為模式等區塊。\n"
            "5. 只引用 high/medium 可信度的新證據；low 可信度的內容標注「（推測）」。\n"
            "6. 只輸出報告，不要前言或說明。語言：繁體中文。"
        )
    else:
        system = (
            "你是一位行為心理分析師，根據預先提取的維度分析結果，生成完整的心智模型報告。\n"
            "規則：\n"
            "1. 只引用 high/medium 可信度的證據；low 可信度的內容標注「（推測）」。\n"
            "2. 若某維度無資料，該欄位填「資料不足，建議補充相關對話」。\n"
            "3. LLM 人格化 System Prompt 必須從證據推斷，禁止列詞彙清單。\n"
            "4. 只輸出報告，不要前言或說明。語言：繁體中文。"
        )

    # ── 組裝 user content ──
    user_parts = [f"【從片段提取的新發現】\n{dim_summary}"]

    if existing_persona.strip():
        user_parts.insert(0, f"【現有 Persona（整合基底，必須保留其核心設定）】\n{existing_persona.strip()}")

    user_parts.append(f"【原始對話片段（補充參考）】\n{fragments_text}")
    user_parts.append(f"請輸出以下格式的完整報告：\n\n{schema}")

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def build_persona_md_prompt(full_report_text: str, existing_persona: str = "") -> list[dict]:
    """生成更新後的 persona.md。

    若提供 existing_persona：
      以現有 Persona 為基底，逐節對照報告中的新分析發現進行差異整合。
      分析區塊（核心動機、決策邏輯、行動模式、矛盾地圖、觀察到的模式）是更新依據；
      System Prompt 區塊因可能只是舊 Persona 的複製，不作為更新來源。

    若未提供 existing_persona：
      直接從報告的「LLM 人格化 System Prompt」區塊萃取並輸出。
    """
    template = FAST_PERSONA_BEHAVIORAL_TEMPLATE

    if existing_persona.strip():
        system = (
            "你是人格整合師，任務是將心智模型報告中的新行為發現，整合進現有 Persona，"
            "生成更新後的 persona.md。\n\n"
            "【整合規則】\n"
            "1. 以現有 Persona 為基底——基本設定（外觀、身份、物種、硬性設定）必須完整保留，不可刪改。\n"
            "2. 更新來源是報告的分析區塊，不是報告末尾的 System Prompt（那可能只是舊 Persona 的複製）：\n"
            "   - 情緒反應模式（感知框架 + 核心矛盾 + 情境反應）\n"
            "     → 對照報告的「核心動機與信念」＋「矛盾地圖」進行更新\n"
            "   - 決策邊界\n"
            "     → 對照報告的「決策邏輯」進行更新\n"
            "   - 對話行為模式\n"
            "     → 對照報告的「表達風格」＋「行動模式」進行更新\n"
            "   - 強度校準\n"
            "     → 對照報告的「觀察到的模式」進行更新\n"
            "3. 更新標準：補充、精煉、或修正舊描述中過於模糊的部分，而非整段替換。\n"
            "   若某節在報告中沒有新的行為證據，直接保留現有 Persona 的對應內容。\n"
            "4. 禁止身體動作描述，所有特質必須轉化為語言行為。\n"
            "5. 輸出格式：以下方模板為結構，在最頂部加入標題「# Persona」。\n"
            "6. 只輸出 persona.md 最終內容，不要前言或說明。語言：繁體中文。\n\n"
            f"【輸出格式模板】\n{template}"
        )
        user_content = (
            "【心智模型報告（分析區塊為新發現的主要來源）】\n"
            f"{full_report_text}\n\n"
            "【現有 Persona（整合基底）】\n"
            f"{existing_persona.strip()}\n\n"
            "請逐節對照報告中的新發現，更新現有 Persona 的各節內容，輸出整合後的 persona.md。"
        )
    else:
        system = (
            "你是一個提取工具，任務是從心智模型報告中提取「LLM 人格化 System Prompt」區塊，"
            "輸出為獨立的 persona.md 內容。\n"
            "規則：\n"
            "1. 只保留「LLM 人格化 System Prompt」下的各小節內容。\n"
            "2. 移除所有分析說明與標注。\n"
            "3. 在最頂部加入標題：# Persona\n"
            "4. 只輸出最終內容，不要任何前言。語言：繁體中文。"
        )
        user_content = (
            "以下是完整的心智模型報告，請提取 LLM 人格化 System Prompt 區塊：\n\n"
            f"{full_report_text}"
        )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
