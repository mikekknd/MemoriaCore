# 【環境假設】：Python 3.12。專職對話狀態容器，解耦 UI 與業務邏輯。
class ConversationSession:
    def __init__(self, storage):
        self.storage = storage
        self.messages = []
        self.last_entities = []
        self.sync_with_storage()

    def sync_with_storage(self):
        """與硬碟存儲同步，防範其他 UI 模組 (如 DB Manager) 直接清空檔案"""
        loaded_history = self.storage.load_history()
        if not loaded_history:
            self.messages = []
            self.last_entities = []
        elif not self.messages:
            self.messages = loaded_history

    def get_messages(self):
        return self.messages

    def get_last_entities(self):
        return self.last_entities

    def add_user_message(self, content):
        self.messages.append({"role": "user", "content": content})
        self.storage.save_history(self.messages)

    def add_assistant_message(self, content, debug_info, extracted_entities):
        self.messages.append({
            "role": "assistant",
            "content": content,
            "debug_info": debug_info
        })
        self.last_entities = extracted_entities
        self.storage.save_history(self.messages)

    def get_pipeline_context(self):
        """回傳等待送入記憶管線的歷史對話 (排除最新一句 User 提問)"""
        return [{"role": m["role"], "content": m["content"]} for m in self.messages[:-1]]

    def bridge_context(self):
        """橋接邏輯：切斷舊脈絡，但保留 AI 上一句與 User 最新一句作為話題橋樑"""
        bridged = []
        if len(self.messages) >= 2:
            bridged.append(self.messages[-2])
        if len(self.messages) >= 1:
            bridged.append(self.messages[-1])
            
        self.messages = bridged
        self.storage.save_history(self.messages)
        # 注意：不清除 last_entities，維持影子實體繼承
        
    def clear_context(self):
        self.messages = []
        self.last_entities = []
        self.storage.save_history(self.messages)