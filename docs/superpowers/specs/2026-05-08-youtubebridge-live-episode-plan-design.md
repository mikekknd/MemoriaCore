# YouTubeBridge Live Episode Plan 設計

## 目的

YouTubeBridge 需要一個開播前產出的節目企劃資產，讓 Codex 先完成資料整理、節目企劃與導播策略，再交給 YouTubeBridge runtime 派發。這份企劃不是 Topic Pack 的替代品。Topic Pack 仍保留在資料層，用於 embedding 檢索與事實素材召回；新的 `LiveEpisodePlan` 負責節目順序、段落目標、角色功能、導播派發與回主線規則。

核心模式是 `Plan-Locked / Audience-Inserted`：

- 段落順序由企劃鎖定。
- 聊天室事件可以打斷目前段落，提供局部互動話題。
- 聊天室事件不能跳段、重排段落、改變整場 rundown。
- 打斷處理完後，導播必須回到目前段落的下一個企劃 turn contract。

## 非目標

- 不把企劃硬壓成現有 FactCards / Topic Pack 格式。
- 不把可可、白蓮、雙主持、動畫新番寫死在 schema。
- 不讓角色層持有整份企劃或自行決定節目流程。
- 不在第一版就要求表情、動作、鏡頭完整落地；先保留 performance hints。
- 不讓聊天室內容取得節目控制權，即使是 Super Chat。

## 設計原則

1. Topic Pack 是資料層，不是節目層。
2. 企劃 contract 描述可執行的導播狀態，不只是人類可讀企劃文。
3. 角色數量可變，支援二人、三人或更多人談話。
4. 節目類型可變，支援訪談、辯論、新聞解讀、陪聊、角色劇場、教學、排行榜討論等形式。
5. JSON 結構穩定，欄位值保持可擴充；避免用硬 enum 鎖死新節目型態。
6. 導播只派發任務，不寫死角色台詞。
7. 每輪只投影必要企劃片段進 prompt，避免整份企劃吃掉 token budget。

## 資產包

Codex skill 最終應產出一個 episode plan package，而不是單一資料卡。

建議目錄：

```text
runtime/YouTubeBridge/EpisodePlans/<plan-slug>/
├── episode-plan.md
├── episode-plan.json
├── sources.md
└── factcards/
```

- `episode-plan.md`：給操作者審稿的人類可讀企劃。
- `episode-plan.json`：給 YouTubeBridge 匯入與導播 runtime 使用的機器 contract。
- `sources.md`：查證來源、使用限制與待查證項目。
- `factcards/`：可選。若企劃需要補資料層素材，可一併產出可匯入 Topic Pack 的資料卡。

## LiveEpisodePlan Schema 草案

第一版 schema 應以 JSON blob 儲存，外層固定、內層可擴充。不要一開始把所有子欄位正規化成多張表。

```json
{
  "schema_version": "live_episode_plan.v1",
  "plan_id": "string",
  "title": "string",
  "language": "zh-TW",
  "show_format": {
    "primary": "debate_panel",
    "secondary": ["news_commentary", "character_banter"],
    "format_notes": "以多人立場碰撞推進，每段都要回到觀眾可帶走的結論。"
  },
  "flow_policy": {
    "segment_order": "locked",
    "audience_interrupts": "allowed_within_current_segment",
    "audience_can_change_segment_order": false,
    "resume_after_interrupt": "next_planned_turn_contract"
  },
  "audience_event_classifier": {
    "event_types": [
      "question",
      "reaction",
      "correction",
      "super_chat",
      "off_topic",
      "hostile",
      "prompt_injection"
    ],
    "actions": {
      "question": "bounded_interrupt",
      "reaction": "optional_ack",
      "correction": "verify_then_ack",
      "super_chat": "bounded_interrupt",
      "off_topic": "ignore_or_soft_ack",
      "hostile": "ignore_or_deescalate",
      "prompt_injection": "ignore"
    }
  },
  "topic_pack_refs": [
    {
      "pack_id": 0,
      "purpose": "evidence_retrieval",
      "query_bias": ["核心作品", "爭議點", "觀眾常問問題"]
    }
  ],
  "participants": [
    {
      "participant_id": "coco",
      "display_name": "可可",
      "role_function": ["host", "audience_proxy", "energy_driver"],
      "speaking_style_bias": ["短句", "反應快", "負責把觀眾情緒帶進來"],
      "best_for_turns": ["hook", "reaction", "transition"],
      "avoid_turns": ["dense_fact_exposition"],
      "interaction_edges": [
        {
          "target_participant_id": "byakuren",
          "relationship_function": "拋問題給對方拆解"
        }
      ]
    }
  ],
  "episode_arc": {
    "thesis": "本集核心主張",
    "tension": "本集主要衝突或取捨",
    "listener_takeaways": ["觀眾最後應該帶走的重點"],
    "opening_strategy": "如何建立本場方向",
    "closing_strategy": "如何自然收束"
  },
  "segments": [
    {
      "segment_id": "seg_01",
      "title": "事件 Hook",
      "goal": "建立為什麼現在值得聊",
      "planned_turn_contracts": [
        {
          "turn_id": "seg_01_turn_01",
          "turn_type": "hook",
          "intent": "用具體事件開場，不直接百科解釋",
          "speaker_policy": {
            "selection_mode": "router_select",
            "preferred_role_functions": ["host", "energy_driver"],
            "allowed_participant_ids": [],
            "avoid_repeat_speaker": true
          },
          "evidence_policy": {
            "queries": [
              "事件名稱 爆點 觀眾反應",
              "作品名稱 榜單 攻頂 爭議"
            ],
            "required_entities": ["作品名稱", "榜單名稱"],
            "allow_unverified_claims": false,
            "max_cards": 3
          },
          "forbidden_repetition": {
            "claims": [],
            "metaphors": [],
            "openings": []
          },
          "output_requirements": {
            "max_sentences": 2,
            "must_end_with_question": false,
            "allow_audience_question": false,
            "should_handoff": true,
            "handoff_target_function": "analyst"
          },
          "handoff": {
            "next_turn_hint": "請另一位角色補上觀眾可能忽略的脈絡"
          }
        }
      ],
      "audience_handling": {
        "allowed_interrupt_types": ["question", "reaction", "super_chat"],
        "max_interrupt_turns": 2,
        "resume_rule": "bridge_back_to_segment_goal"
      },
      "completion_conditions": {
        "min_planned_turns": 2,
        "max_planned_turns": 4,
        "required_turn_types": ["hook", "analysis"],
        "optional_turn_types": ["counterpoint", "transition"]
      },
      "transition_targets": [
        {
          "target_segment_id": "seg_02",
          "transition_intent": "從事件表層轉入核心爭議"
        }
      ]
    }
  ],
  "constraints": {
    "forbidden_repetition": {
      "claims": ["已講過的核心主張"],
      "openings": ["重複開場句式"],
      "jokes": ["重複笑點"]
    },
    "safety": {
      "audience_is_untrusted": true,
      "do_not_follow_audience_instructions": true,
      "do_not_expose_internal_plan": true
    }
  },
  "performance_hints": {
    "tts": {},
    "subtitles": {},
    "expressions": {},
    "camera": {}
  }
}
```

## Runtime 狀態

導播 runtime 應拆成兩條狀態線。

`planned_state`：

- `plan_id`
- `current_segment_index`
- `current_turn_index`
- `segment_memory`
- `last_planned_turn_contract_id`

`segment_memory` 用於 runtime 防重複與回主線，不作為 episode plan 的靜態欄位：

```json
{
  "covered_claims": [],
  "used_examples": [],
  "used_metaphors": [],
  "used_openings": [],
  "audience_reactions": [],
  "pending_questions": [],
  "forbidden_next_repeats": []
}
```

`listener_takeaways` 仍保留在 `episode_arc`，但第一版不把它作為硬性的段落完成條件。段落完成以 `min_planned_turns`、`max_planned_turns` 與 `required_turn_types` 這類機械條件判斷；takeaways 由 LLM 或 summary 模組在事後標記，供回顧、摘要與下次企劃參考。

`interrupt_state`：

- `status`: `idle` 或 `handling_audience`
- `source_event_ids`
- `interrupt_type`
- `return_segment_index`
- `return_turn_index`
- `remaining_interrupt_turns`
- `resume_rule`

聊天室插入只更新 `interrupt_state` 與 `segment_memory.audience_reactions`。它不得直接改 `current_segment_index` 或重排 `segments`。

## 導播派發流程

1. 載入目前 `LiveEpisodePlan` 與 `planned_state`。
2. 對 pending 聊天室事件先走現有安全分類，再由 `audience_event_classifier` 判斷節目互動型別與 action。
3. 若沒有可處理聊天室事件，選取目前 segment 的下一個 `planned_turn_contract`。
4. 若有聊天室事件且分類 action 需要處理，建立 bounded interrupt contract：
   - 只處理目前事件或事件批次。
   - 依 `max_interrupt_turns` 限制角色互動輪數。
   - prompt 明確要求回到目前 segment goal。
   - `prompt_injection` 預設 ignore，不進入角色回應。
   - `hostile` 只允許 ignore 或去升溫式短回應，不改變企劃流程。
   - `correction` 必須先走查證或 Topic Pack 檢索，再決定是否回應。
5. 將當輪 contract、目前 segment 摘要、必要 Topic Pack query、角色功能列表投影成 `external_context.context_text`。
6. 呼叫 MemoriaCore 群聊流程，由 group router 根據角色功能與接話狀態挑 speaker。
7. 回合完成後：
   - 若是 interrupt，消耗 `remaining_interrupt_turns`，完成後清空 `interrupt_state`。
   - 若是 planned turn，更新 `current_turn_index`、已完成 turn type 與 `segment_memory`。
8. 只有 `completion_conditions` 的機械條件滿足時，導播才推進到下一段。

## Prompt 投影

不要把完整 JSON 直接塞進角色 prompt。每輪只投影：

- 目前節目段落。
- 當輪 turn contract。
- 可用參與者的 role functions。
- 本輪 Topic Pack queries、required entities、max cards 與召回素材。
- 全域與本輪 forbidden repetition。
- 本輪 output requirements。
- speaker selection mode 與 allowed participant 限制。
- 若是 interrupt，包含事件摘要與 resume rule。

投影範例：

```text
<live_episode_director_context>
plan_id: ...
segment: seg_02 / 核心爭議
turn_contract: seg_02_turn_03
turn_intent: 提出反方觀點，回應上一位角色的主張。
speaker_policy:
  selection_mode: router_select
  preferred_role_functions: skeptic, analyst
  allowed_participant_ids: （未指定時由 router 依 role function 選角）
evidence_policy:
  queries: 作品名稱 榜單 攻頂 爭議
  required_entities: 作品名稱, 榜單名稱
  max_cards: 3
  allow_unverified_claims: false
output_requirements:
  max_sentences: 2
  allow_audience_question: false
  should_handoff: true
forbidden_repetition:
  claims: ...
  openings: ...
resume_rule: 本輪不是聊天室打斷，完成後依 required_turn_types 檢查段落進度。
</live_episode_director_context>
```

## Skill 設計方向

新的 Codex skill 應在 runtime contract 確定後建立，暫名 `live-episode-planner`。

輸入：

- 節目主題或方向。
- 預期節目類型。
- 預計角色清單與角色定位。
- 預計直播長度。
- 是否需要查證最新資料。
- 是否要附帶 FactCards / Topic Fuel Cards。

輸出：

- `episode-plan.md`
- `episode-plan.json`
- `sources.md`
- 可選資料層卡片。

品質要求：

- Markdown 要適合人類審稿。
- JSON 要能被 schema validator 檢查。
- 不允許把角色台詞寫死成逐字稿。
- 不允許把節目類型、角色數量、題材分類寫死。
- 時效性資料必須附來源或標記待查證。

## Storage 與 UI 建議

第一版可新增：

- `live_episode_plans`：存 `plan_id`、title、schema_version、plan_json、source_path、created_at、updated_at。
- `live_sessions.episode_plan_id`：將單一企劃綁定到 live session；第一版不支援同一場直播同時掛多份企劃。
- `live_director_state.metadata.planned_state`：保存目前段落與 turn 指標。
- `live_director_state.metadata.interrupt_state`：保存聊天室插入狀態。

UI 第一版只需要：

- 匯入 episode plan JSON。
- 顯示目前 segment / turn / interrupt 狀態。
- 綁定或解除 session 的 episode plan。
- 不需要在第一版提供完整線上編輯器。

## 測試策略

單元測試：

- schema validator 接受三人以上 participants。
- schema validator 接受未知但有 `format_notes` 的 show format。
- schema validator 接受結構化 `evidence_policy.queries`、`required_entities` 與 `max_cards`。
- schema validator 接受 `speaker_policy.selection_mode`、`allowed_participant_ids` 與 per-turn `forbidden_repetition`。
- `planned_state` 推進不受聊天室事件改變。
- interrupt 完成後回到原 segment / turn。
- Super Chat interrupt 不會跳段。
- `completion_conditions.required_turn_types` 未滿足時不推進到下一段。
- `audience_event_classifier` 將 question / reaction / correction / hostile / prompt_injection 映射到正確 action。
- Topic Pack 查詢只作為 evidence retrieval，不作為段落順序來源。

整合測試：

- 匯入 episode plan 後，director turn 的 `external_context` 包含目前 turn contract。
- director turn 的 `external_context` 包含結構化 evidence queries 與 output requirements。
- 聊天室事件插入後，下一個 planned turn 正確恢復。
- prompt injection 類留言被忽略，不建立 interrupt。
- correction 類留言先查證或檢索，再進入可回應路徑。
- 群聊接力仍遵守既有 primary reply target 規則。
- Live persona overlay 仍只影響角色語氣，不覆寫節目流程。

回歸測試：

- 現有 Topic Pack / FactCards 匯入流程不被移除。
- 現有無 episode plan 的 live session 仍可用舊導播流程。
- summary / memory 寫入仍不保存 prompt、hidden context、攻擊原文。

## 風險與對策

- 企劃太大：每輪只投影必要 contract，不塞完整 plan。
- LLM 忽略導播：把 turn contract 寫成短而明確的狀態，不用散文提示。
- 聊天室帶偏：interrupt 只在目前 segment 內生效，並有固定回主線規則。
- 所有留言都變 interrupt：用 `audience_event_classifier` 將 reaction / off_topic / hostile / prompt_injection 分流，只有需要處理的事件進 bounded interrupt。
- 檢索 query 太像人類筆記：`evidence_policy.queries` 使用可直接送進檢索的字串陣列，並以 `required_entities` 與 `max_cards` 約束召回。
- 段落完成條件太主觀：第一版只用 turn 數與 turn type 判斷；takeaways 事後標記。
- 節目類型過度硬編碼：`show_format.primary` 使用字串與 notes，不用封閉 enum。
- 多人談話失控：speaker selection 依 role functions 與 turn type，而不是固定角色順序。

## 實作切分建議

1. 定義 `LiveEpisodePlan` schema 與 validator。
2. 新增 plan 匯入與 session 綁定 API。
3. 新增 audience event classifier，接在現有 SafetyLLM 之後，輸出 event type 與 plan action。
4. 新增 director plan projection helper。
5. 新增 `planned_state` / `interrupt_state` / `segment_memory` 推進邏輯。
6. 將 director runtime 接上 plan-aware path，無 plan 時維持舊流程。
7. 建立 `live-episode-planner` skill，輸出 Markdown + JSON + sources。
8. 補 UI 顯示與 E2E 驗證。

## 第一版決策

- episode plan JSON 匯入後存入 `live_episode_plans.plan_json`；原始檔案路徑只作為 `source_path` metadata。
- 第一版每個 live session 只綁定一份 episode plan，不做直播中多企劃切換。
- 第一版段落完成條件只使用機械條件：`min_planned_turns`、`max_planned_turns`、`required_turn_types` 與 `optional_turn_types`。
- `listener_takeaways` 不作為 runtime 硬條件；由 LLM 或 summary 模組在事後標記。
- audience event classifier 只處理已通過安全分類或已安全化的留言，不取代 SafetyLLM。
- `performance_hints` 第一版只保留欄位與投影接口，不同步到 live overlay，也不驅動鏡頭或表情。
- `live-episode-planner` skill 預設產出 episode plan；只有使用者要求補資料層素材時，才額外產出 Topic Fuel Cards。
