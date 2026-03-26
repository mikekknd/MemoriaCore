import time
import random
import asyncio
import json
from core.storage_manager import StorageManager
from tools.tavily import search_web
from core.llm_gateway import LLMRouter
from datetime import datetime, timedelta
from core.system_logger import SystemLogger

_next_gather_time = None

def run_background_topic_gather(db_path: str, router: LLMRouter, storage: StorageManager):
    """
    背景搜集引擎邏輯：
    1. 從 DB 讀取使用者的 user_profile（偏好/事實）
    2. 隨機挑選一個興趣或客觀事實
    3. 呼叫 Tavily 進行相關新聞或深入研究
    4. 用 LLM 對結果進行摘要
    5. 存入 topic_cache
    """
    try:
        sm = storage
        profiles = sm.load_all_profiles(db_path)
        
        if not profiles:
            print("[TopicGather] 找不到任何使用者畫像，無法生成話題。")
            return
            
        # 篩選看起來像興趣的，不過如果沒有這種類別，就直接全體隨機
        interests = [p for p in profiles if p.get('category', '') in ['興趣', '偏好', '喜好', 'interest', 'preference']]
        if not interests:
            interests = profiles
            
        chosen_fact = random.choice(interests)
        interest_keyword = chosen_fact.get('fact_value', '')
        if not interest_keyword:
            interest_keyword = chosen_fact.get('fact_key', '')
            
        if not interest_keyword:
            return
            
        print(f"[TopicGather] 開始針對主題 '{interest_keyword}' 進行背景資料搜集...")
        
        # 1. 執行 TAVILY 搜尋
        query = f"{interest_keyword} 相關最新資訊或新聞"
        search_result_json = search_web(query=query, topic="news")
        
        result = json.loads(search_result_json)
        if "error" in result or "message" in result:
            print(f"[TopicGather] 搜尋失敗或無結果: {result}")
            return
            
        raw_content = result.get("search_results", "")
        
        # 2. LLM 摘要並萃取話題
        prompt = f"""請根據以下網路搜尋結果，針對主題「{interest_keyword}」撰寫一段 50 字以內的自然話題引子。
目標：這將用作 AI 助理後續主動物理話題的口袋話題。
要求：語氣要自然生動，彷彿是不經意提起，絕對不能出現「根據搜尋結果」或「這裡有一篇新聞」等生硬字眼。

【搜尋結果】：
{raw_content}
"""
        messages = [{"role": "user", "content": prompt}]
        
        # 假設 router 裡面註冊了對應的通道
        try:
            summary = router.generate("background_gather", messages, temperature=0.7)
        except ValueError:
            # 向後兼容：如果使用者還沒儲存設定，則回退使用 chat 路由
            summary = router.generate("chat", messages, temperature=0.7)
            
        if summary:
            # 3. 存入資料庫
            topic_id = f"topic_{int(time.time())}_{random.randint(100, 999)}"
            sm.insert_topic_cache(db_path, topic_id, interest_keyword, summary.strip())
            print(f"[TopicGather] 成功產生並快取話題: {summary.strip()}")
            
    except Exception as e:
        print(f"[TopicGather] 背景發生錯誤: {e}")

def force_gather_now():
    """強制讓下次迴圈檢查時立即發動蒐集"""
    global _next_gather_time
    _next_gather_time = datetime.now()


async def start_background_gather_loop(db_path: str, router: LLMRouter, storage: StorageManager, default_interval_seconds: int = 14400):
    """
    啟動無限迴圈定時執行搜集。
    第一次啟動時會等待 interval 秒才執行，避免一開機就觸發。
    """
    global _next_gather_time

    # 即時讀取最新設定的頻率 (可能在途中被使用者透過介面更改)
    try:
        prefs = storage.load_prefs()
        interval_seconds = int(prefs.get("bg_gather_interval", default_interval_seconds))
    except Exception:
        interval_seconds = default_interval_seconds

    _next_gather_time = datetime.now() + timedelta(seconds=interval_seconds)
    SystemLogger.log_system_event("BackgroundGather",f"背景話題搜集已掛載，下次預計啟動時間: {_next_gather_time}")

    while True:
        try:
            now = datetime.now()
            if _next_gather_time and now >= _next_gather_time:
                SystemLogger.log_system_event("BackgroundGather","觸發背景話題蒐集任務...")
                await asyncio.to_thread(run_background_topic_gather, db_path, router, storage)

                # 執行完畢後，重新讀取最新頻率，並以「此刻 + N 小時」重新計算下次發動時間
                prefs = storage.load_prefs()
                new_interval = int(prefs.get("bg_gather_interval", default_interval_seconds))
                _next_gather_time = datetime.now() + timedelta(seconds=new_interval)
                SystemLogger.log_system_event("BackgroundGather",f"任務執行完畢，下次預計啟動時間重設為: {_next_gather_time}")
                
        except asyncio.CancelledError:
            SystemLogger.log_system_event("BackgroundGather","收到中斷訊號，退出背景話題搜集。")
            break
        except Exception as e:
            SystemLogger.log_error(f"背景話題蒐集迴圈發生錯誤: {e}")
            
        # 短暫休眠 10 秒，讓系統能快速反應設定變更或 `force_gather_now`
        await asyncio.sleep(10)
