"""YouTubeBridge manager 共用常數與 LLM schema。"""
from __future__ import annotations


DEFAULT_INJECT_CONTENT = "請根據已提供的 Topic Pack / fact card / YouTube 直播留言上下文回應。不要自行開啟瀏覽器或搜尋網頁。"
CONTROLLED_CONTEXT_CONTENT = DEFAULT_INJECT_CONTENT
FACT_CARDS_PACK_TITLE = "動畫新番 FactCards"
FACT_CARDS_PACK_DESCRIPTION = "FactCards 資料夾匯入的動畫新番參考資料。"

DIRECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "reason": {"type": "string"},
        "prompt": {"type": "string"},
        "current_topic": {"type": "string"},
    },
}

TEST_COMMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "comments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "author_display_name": {"type": "string"},
                    "message_text": {"type": "string"},
                },
            },
        },
    },
}

SAFETY_CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer"},
                    "label": {"type": "string"},
                    "safe_text": {"type": "string"},
                    "safe_summary": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["event_id", "label", "safe_text"],
            },
        },
    },
}

AUDIENCE_QUERY_CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "is_factual_question": {"type": "boolean"},
        "needs_external_search": {"type": "boolean"},
        "safe_search_allowed": {"type": "boolean"},
        "sanitized_query": {"type": "string"},
        "topic_scope": {"type": "string"},
        "risk_label": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["is_factual_question", "needs_external_search", "safe_search_allowed", "sanitized_query"],
}

SAFETY_CLASSIFIER_BATCH_LIMIT = 20
AUDIENCE_QUERY_FACT_CARD_MIN_SCORE = 0.20
AUDIENCE_QUERY_FACT_CARD_STRONG_SCORE = 0.60
AUDIENCE_QUERY_FACT_CARD_MIN_GAP = 0.08
