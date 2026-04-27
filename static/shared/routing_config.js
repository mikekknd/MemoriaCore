const PROVIDERS = ['Ollama (本地)', 'llama.cpp (本地)', 'OpenAI (雲端)', 'OpenRouter (雲端)'];

const TASK_INFOS = {
  chat:              { desc:'即時對話 (帶影子標籤)',      help:'處理玩家對話，同時伴隨生成實體標籤。建議高參數量模型。' },
  expand:            { desc:'User意圖擴充 (秒速提取)',    help:'在提問瞬間提取使用者話語中的高密度名詞。' },
  pipeline:          { desc:'一體化記憶管線',            help:'在背景一次性完成長文切分、摘要與圖譜修復。' },
  compress:          { desc:'對話壓縮 (編年史化)',        help:'將過長的歷史對話壓縮為高密度編年史摘要。' },
  distill:           { desc:'核心認知提煉 (Insight)',     help:'從情境記憶提煉使用者的深層特徵與長期價值觀。' },
  ep_fuse:           { desc:'情境概覽縫合',              help:'將多段相關的情境記憶合併為一個高密度的綜合概覽。' },
  profile:           { desc:'使用者畫像更新',            help:'從對話中萃取使用者的個人特徵與偏好。' },
  persona_sync:      { desc:'PersonaProbe 人格反思',     help:'定時批次分析對話片段並更新 AI 人格演化。建議使用能力較強的模型以確保分析品質。' },
  background_gather: { desc:'背景話題摘要',              help:'在背景將 Tavily 搜尋下來的資料摘要成主動話題。建議不需太強的模型。' },
  character_gen:     { desc:'角色設定生成',              help:'根據簡短描述利用 AI 擴充出完整的角色系統提示詞與心理指標。' },
  router:            { desc:'意圖路由預處理',            help:'工具意圖偵測：判斷是否需要呼叫外部工具並產生過渡語音。建議使用輕量快速模型以降低延遲。' },
  translate:         { desc:'TTS 語音翻譯',              help:'將角色回覆翻譯為 TTS 目標語言（如日文），獨立於主對話執行。預設跟隨 chat 設定，可指定較輕量的翻譯專用模型。' },
  browser:           { desc:'Browser Agent 瀏覽器代理',  help:'多輪瀏覽器自動化 Subagent，逐步執行網頁操作任務。建議使用支援 Function Calling 的模型。' },
};
