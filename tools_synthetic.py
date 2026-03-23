# 【環境假設】：Python 3.12, Streamlit 1.30+
import streamlit as st
import json
import re
from datetime import datetime, date, time

DEFAULT_TOPICS = [
    "晚餐去吃了一家很棒的豚骨拉麵，湯頭非常濃郁",
    "分享剛看完的一部賽璐璐畫風動畫，色彩通透感極佳",
    "明天預計要繼續開發 Python 畫面即時翻譯軟體",
    "跟傲嬌女僕深月鬥嘴的日常對話",
    "討論燕雲十六聲裡伊刀（刀哥）的武學動作設計",
    "研究 Qwen3-TTS 的語氣與情緒控制參數",
    "回顧 NDS 經典遊戲 Love Plus 的遊玩回憶",
    "討論最近加密貨幣 ETF 的市場趨勢",
    "研究如何優化自動化化遊戲腳本的圖像辨識準確率",
    "規劃製作一首帶有傳統國風的 MIDI 戰鬥音樂"
]

def generate_synthetic_data(topic, turns, memory_sys, analyzer, router, sim_timestamp=None):
    prompt = f"""請模擬一段關於「{topic}」的深度自然對話，包含 User 和 Assistant 的來回討論。
【長度與深度要求】：約 {turns} 回合，最大不超過 20 回合。包含提問、解答、延伸討論。
【強制輸出格式】：嚴禁任何開場白、結語或解釋。請直接依照提供的 JSON Schema 結構輸出。
"""
    
    # 【核心修正】：導入 JSON Schema，強勢鎖定輸出格式，防止括號遺漏
    synthetic_schema = {
        "type": "object",
        "properties": {
            "conversation": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string", "enum": ["user", "assistant"]},
                        "content": {"type": "string"}
                    },
                    "required": ["role", "content"]
                }
            }
        },
        "required": ["conversation"]
    }

    try:
        api_messages = [{"role": "user", "content": prompt}]
        # 【核心修正】：傳入 response_format 參數
        raw_text = router.generate("chat", api_messages, temperature=0.6, response_format=synthetic_schema)
        
        _start = raw_text.find('{')
        if _start == -1:
            return False, "解析失敗，未偵測到合法的 JSON 結構。", raw_text

        try:
            parsed_obj, _ = json.JSONDecoder().raw_decode(raw_text, _start)
            parsed_array = parsed_obj.get("conversation", [])
        except Exception as e:
            return False, f"JSON 反序列化失敗: {e}", raw_text

        messages = []
        for item in parsed_array:
            if isinstance(item, dict):
                r = item.get("role", "").lower()
                c = item.get("content", "").strip()
                if r in ["user", "assistant"] and c:
                    messages.append({"role": r, "content": c})
        
        if not messages:
            return False, "解析失敗，JSON 陣列中未包含有效的 user 或 assistant 對話。", raw_text

        last_block = memory_sys.memory_blocks[-1] if memory_sys.memory_blocks else None
        pipeline_res = analyzer.process_memory_pipeline(messages, last_block, router, memory_sys.embed_model, task_key="pipeline")
        if "error" in pipeline_res:
            return False, pipeline_res["error"], raw_text
            
        new_mems = pipeline_res.get("new_memories", [])
        if not new_mems:
            return False, "管線未產出任何記憶區塊。", raw_text
            
        overview_texts = []
        for mem in new_mems:
            entities_str = ", ".join(mem.get("entities", []))
            summary_str = mem.get("summary", "無摘要")
            indices = mem.get("message_indices", [])
            overview = f"[核心實體]: {entities_str}\n[情境摘要]: {summary_str}"
            
            prefs = mem.get("potential_preferences", [])
            raw_dialogues = [messages[idx] for idx in indices if 0 <= idx < len(messages)]
            if raw_dialogues:
                memory_sys.add_memory_block(overview, raw_dialogues, router=router, sim_timestamp=sim_timestamp, potential_preferences=prefs)
                overview_texts.append(overview)
                
        if not overview_texts:
            return False, "記憶區塊生成成功，但因語意分群未能吸附任何對話而遭捨棄。", raw_text

        # 【使用者畫像提取】：與真實對話管線行為一致，同步提取側寫事實
        try:
            current_profile = memory_sys.storage.load_all_profiles(memory_sys.db_path) if memory_sys.db_path else []
            profile_facts = analyzer.extract_user_facts(messages, current_profile, router, task_key="profile")
            if profile_facts:
                memory_sys.apply_profile_facts(profile_facts, memory_sys.embed_model)
        except Exception:
            pass

        return True, "\n\n".join(overview_texts), messages
    except Exception as e:
        return False, f"系統錯誤: {e}", ""

def render_synthetic_data_ui(memory_sys, analyzer, router):
    st.header("🧪 開發者測試模式")
    with st.expander("🏭 模擬資料生成器", expanded=False):
        test_topic_selection = st.selectbox("選擇預設主題", ["(自訂主題)"] + DEFAULT_TOPICS)
        custom_test_topic = st.text_input("或自訂輸入主題：", value="" if test_topic_selection != "(自訂主題)" else "例如：探討 Unity DOTS 效能陷阱")
        
        final_topic = custom_test_topic if custom_test_topic and custom_test_topic != "例如：探討 Unity DOTS 效能陷阱" else test_topic_selection
        test_turns = st.slider("預期回合數", 3, 15, 8)

        use_custom_time = st.checkbox("設定自訂對話時間（不勾選則使用目前時間）")
        sim_timestamp = None
        if use_custom_time:
            col_d, col_t = st.columns(2)
            with col_d:
                sim_date = st.date_input("日期", value=date.today())
            with col_t:
                sim_time_val = st.time_input("時間", value=time(12, 0))
            sim_timestamp = datetime.combine(sim_date, sim_time_val).isoformat()
            st.caption(f"模擬時間：{sim_timestamp}")

        if st.button("🚀 生成並注入此筆記憶", use_container_width=True):
            if final_topic == "(自訂主題)" or not final_topic.strip():
                st.error("請選擇或輸入有效的主題！")
            else:
                with st.spinner("路由系統正在分配模型生成對話與記憶概覽..."):
                    success, result_overview, result_data = generate_synthetic_data(
                        final_topic, test_turns, memory_sys, analyzer, router, sim_timestamp=sim_timestamp
                    )
                    
                if success:
                    st.success("✅ 測試資料注入成功！")
                    with st.container(border=True):
                        st.markdown("**[生成的概覽]**")
                        st.info(result_overview)
                else:
                    st.error(f"❌ 注入失敗: {result_overview}")
                    st.markdown("**🔍 檢視模型原始輸出**")
                    st.code(result_data, language="json")