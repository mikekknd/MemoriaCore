"""YouTubeBridge SQLite schema 初始化與 migration。"""
from __future__ import annotations

import sqlite3


def init_bridge_db(conn: sqlite3.Connection) -> None:
    """建立 YouTubeBridge runtime DB schema。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS connectors (
            connector_id TEXT PRIMARY KEY,
            display_name TEXT DEFAULT '',
            api_key TEXT DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memoria_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            base_url TEXT DEFAULT 'http://localhost:8088/api/v1',
            username TEXT DEFAULT '',
            password TEXT DEFAULT '',
            admin_bypass INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS studio_settings (
            section TEXT PRIMARY KEY,
            payload_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS live_episode_plans (
            plan_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            title TEXT NOT NULL,
            language TEXT DEFAULT 'zh-TW',
            show_format_json TEXT DEFAULT '{}',
            plan_json TEXT NOT NULL,
            source_path TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS live_sessions (
            session_id TEXT PRIMARY KEY,
            connector_id TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            video_id TEXT DEFAULT '',
            live_chat_id TEXT DEFAULT '',
            target_memoria_session_id TEXT DEFAULT '',
            episode_plan_id TEXT DEFAULT '',
            character_ids_json TEXT DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'stopped',
            auto_connect INTEGER NOT NULL DEFAULT 1,
            auto_inject INTEGER NOT NULL DEFAULT 0,
            inject_interval_seconds INTEGER NOT NULL DEFAULT 30,
            inject_min_interval_seconds INTEGER NOT NULL DEFAULT 10,
            inject_min_interval_ratio REAL NOT NULL DEFAULT 0.32,
            min_pending_events INTEGER NOT NULL DEFAULT 1,
            max_pending_events INTEGER NOT NULL DEFAULT 12,
            dynamic_inject_enabled INTEGER NOT NULL DEFAULT 1,
            max_context_messages INTEGER NOT NULL DEFAULT 50,
            max_context_chars INTEGER NOT NULL DEFAULT 8000,
            retention_days INTEGER NOT NULL DEFAULT 30,
            planned_duration_minutes INTEGER NOT NULL DEFAULT 30,
            auto_finalize_on_duration INTEGER NOT NULL DEFAULT 1,
            auto_delete_after_processed INTEGER NOT NULL DEFAULT 1,
            director_guidance TEXT DEFAULT '',
            host_interaction_rules TEXT DEFAULT '',
            program_segment_plan TEXT DEFAULT '',
            program_segment_turns INTEGER NOT NULL DEFAULT 3,
            auto_test_events_enabled INTEGER NOT NULL DEFAULT 0,
            test_event_min_seconds INTEGER NOT NULL DEFAULT 20,
            test_event_max_seconds INTEGER NOT NULL DEFAULT 45,
            test_event_count_per_tick INTEGER NOT NULL DEFAULT 3,
            test_event_use_llm INTEGER NOT NULL DEFAULT 1,
            test_super_chat_count_per_tick INTEGER NOT NULL DEFAULT 0,
            test_malicious_sc_enabled INTEGER NOT NULL DEFAULT 0,
            test_sc_burst_mode INTEGER NOT NULL DEFAULT 0,
            sc_interrupt_cooldown_seconds INTEGER NOT NULL DEFAULT 30,
            max_sc_per_batch INTEGER NOT NULL DEFAULT 5,
            director_anchor_every_turns INTEGER NOT NULL DEFAULT 2,
            director_dialogue_expansion_enabled INTEGER NOT NULL DEFAULT 1,
            director_group_turn_limit INTEGER NOT NULL DEFAULT 3,
            director_audience_interrupt_cooldown_seconds INTEGER NOT NULL DEFAULT 30,
            director_max_audience_batches_per_planned_turn INTEGER NOT NULL DEFAULT 1,
            director_max_chat_batches_before_anchor INTEGER NOT NULL DEFAULT 2,
            director_offtopic_policy TEXT DEFAULT 'defer',
            director_sc_burst_policy TEXT DEFAULT 'summarize_batch',
            research_enabled INTEGER NOT NULL DEFAULT 0,
            research_cooldown_seconds INTEGER NOT NULL DEFAULT 300,
            research_max_per_session INTEGER NOT NULL DEFAULT 12,
            auto_sc_thanks_on_finalize INTEGER NOT NULL DEFAULT 1,
            presentation_enabled INTEGER NOT NULL DEFAULT 0,
            tts_enabled INTEGER NOT NULL DEFAULT 0,
            tts_provider TEXT DEFAULT 'gpt_sovits',
            presentation_ack_timeout_seconds INTEGER NOT NULL DEFAULT 120,
            prefetch_wait_timeout_seconds REAL NOT NULL DEFAULT 10.0,
            post_plan_free_talk_enabled INTEGER NOT NULL DEFAULT 0,
            post_plan_free_talk_minutes INTEGER NOT NULL DEFAULT 20,
            post_plan_free_talk_tick_interval_seconds INTEGER NOT NULL DEFAULT 30,
            post_plan_free_talk_idle_turns_min INTEGER NOT NULL DEFAULT 6,
            post_plan_free_talk_idle_turns_max INTEGER NOT NULL DEFAULT 6,
            post_plan_free_talk_audience_turns_min INTEGER NOT NULL DEFAULT 3,
            post_plan_free_talk_audience_turns_max INTEGER NOT NULL DEFAULT 3,
            post_plan_free_talk_topic_pack_ids_json TEXT DEFAULT '[]',
            free_talk_closing_target_batches INTEGER NOT NULL DEFAULT 10,
            free_talk_closing_min_batch_size INTEGER NOT NULL DEFAULT 5,
            free_talk_closing_max_batch_size INTEGER NOT NULL DEFAULT 30,
            free_talk_closing_time_limit_seconds INTEGER NOT NULL DEFAULT 300,
            started_at TEXT DEFAULT '',
            finalized_at TEXT DEFAULT '',
            summary_status TEXT NOT NULL DEFAULT 'pending',
            summary_id INTEGER,
            summary_error TEXT DEFAULT '',
            summary_updated_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS live_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bridge_session_id TEXT NOT NULL,
            connector_id TEXT NOT NULL,
            video_id TEXT DEFAULT '',
            live_chat_id TEXT DEFAULT '',
            youtube_message_id TEXT NOT NULL,
            message_type TEXT NOT NULL DEFAULT '',
            author_channel_id TEXT DEFAULT '',
            author_display_name TEXT DEFAULT '',
            author_profile_image_url TEXT DEFAULT '',
            message_text TEXT DEFAULT '',
            published_at TEXT DEFAULT '',
            received_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            amount_display_string TEXT DEFAULT '',
            currency TEXT DEFAULT '',
            amount_micros INTEGER NOT NULL DEFAULT 0,
            sc_tier INTEGER NOT NULL DEFAULT 0,
            priority_class TEXT DEFAULT 'normal',
            safety_label TEXT DEFAULT 'unclassified',
            safety_status TEXT DEFAULT 'pending',
            safe_message_text TEXT DEFAULT '',
            safety_summary TEXT DEFAULT '',
            safety_reason TEXT DEFAULT '',
            safety_confidence REAL NOT NULL DEFAULT 0,
            safety_checked_at TEXT DEFAULT '',
            handled_in_closing_at TEXT DEFAULT '',
            injected_at TEXT DEFAULT '',
            injection_count INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT DEFAULT '{}',
            UNIQUE(bridge_session_id, youtube_message_id)
        );

        CREATE TABLE IF NOT EXISTS youtube_live_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            connector_id TEXT NOT NULL,
            video_id TEXT DEFAULT '',
            live_chat_id TEXT DEFAULT '',
            character_ids_json TEXT DEFAULT '[]',
            title TEXT DEFAULT '',
            summary_text TEXT NOT NULL,
            topic_tags_json TEXT DEFAULT '[]',
            key_points_json TEXT DEFAULT '[]',
            qa_pairs_json TEXT DEFAULT '[]',
            audience_mood TEXT DEFAULT '',
            memory_text TEXT DEFAULT '',
            event_count INTEGER NOT NULL DEFAULT 0,
            source_started_at TEXT DEFAULT '',
            source_ended_at TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'completed',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS topic_packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS topic_pack_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pack_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            source_url TEXT DEFAULT '',
            source_type TEXT DEFAULT 'manual',
            tags_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS topic_pack_entry_embeddings (
            entry_id INTEGER PRIMARY KEY,
            pack_id INTEGER NOT NULL,
            embedding_model TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            embedding_blob BLOB NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS topic_pack_entry_usages (
            session_id TEXT NOT NULL,
            entry_id INTEGER NOT NULL,
            pack_id INTEGER NOT NULL,
            query_text TEXT DEFAULT '',
            similarity REAL DEFAULT 0,
            usage_source TEXT DEFAULT 'external_context',
            interaction_id TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS live_session_topic_packs (
            session_id TEXT NOT NULL,
            pack_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(session_id, pack_id)
        );

        CREATE TABLE IF NOT EXISTS live_persona_overlays (
            character_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            mode TEXT NOT NULL DEFAULT 'replace',
            system_prompt TEXT DEFAULT '',
            self_address TEXT DEFAULT '',
            addressing_json TEXT DEFAULT '{}',
            opening_intro TEXT DEFAULT '',
            reply_rules TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            chat_background_color TEXT DEFAULT '',
            chat_accent_color TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS topic_graph_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pack_id INTEGER NOT NULL,
            entry_id INTEGER,
            node_key TEXT NOT NULL,
            node_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            source_name TEXT DEFAULT '',
            source_heading TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(pack_id, node_key)
        );

        CREATE TABLE IF NOT EXISTS topic_graph_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pack_id INTEGER NOT NULL,
            source_node_id INTEGER NOT NULL,
            target_node_id INTEGER NOT NULL,
            edge_type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            evidence TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(pack_id, source_node_id, target_node_id, edge_type)
        );

        CREATE TABLE IF NOT EXISTS topic_graph_retrieval_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            pack_id INTEGER NOT NULL,
            source TEXT DEFAULT '',
            query_text TEXT DEFAULT '',
            entry_node_ids_json TEXT DEFAULT '[]',
            expanded_node_ids_json TEXT DEFAULT '[]',
            selected_node_ids_json TEXT DEFAULT '[]',
            rejected_nodes_json TEXT DEFAULT '[]',
            context_text_preview TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS research_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            query TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            result_entry_id INTEGER,
            created_at TEXT NOT NULL,
            metadata_json TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS live_interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT UNIQUE NOT NULL,
            session_id TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'youtube_injection',
            priority INTEGER NOT NULL DEFAULT 100,
            status TEXT NOT NULL DEFAULT 'queued',
            reason TEXT DEFAULT '',
            event_ids_json TEXT DEFAULT '[]',
            memoria_session_id TEXT DEFAULT '',
            character_ids_json TEXT DEFAULT '[]',
            content TEXT DEFAULT '',
            reply_text TEXT DEFAULT '',
            closure_text TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            started_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            interrupted_at TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS live_presentation_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT UNIQUE NOT NULL,
            session_id TEXT NOT NULL,
            interaction_job_id TEXT DEFAULT '',
            message_id TEXT DEFAULT '',
            character_id TEXT DEFAULT '',
            character_name TEXT DEFAULT '',
            sequence_index INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'queued',
            text TEXT DEFAULT '',
            audio_path TEXT DEFAULT '',
            audio_format TEXT DEFAULT 'wav',
            error TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            presented_at TEXT DEFAULT '',
            acked_at TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS live_tts_profiles (
            character_id TEXT PRIMARY KEY,
            ref_audio_path TEXT NOT NULL DEFAULT '',
            prompt_text TEXT DEFAULT '',
            text_lang TEXT NOT NULL DEFAULT 'zh',
            prompt_lang TEXT NOT NULL DEFAULT 'zh',
            speed_factor REAL NOT NULL DEFAULT 1.0,
            media_type TEXT NOT NULL DEFAULT 'wav',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS live_director_state (
            session_id TEXT PRIMARY KEY,
            director_enabled INTEGER NOT NULL DEFAULT 0,
            idle_seconds INTEGER NOT NULL DEFAULT 60,
            last_director_action_at TEXT DEFAULT '',
            current_topic TEXT DEFAULT '',
            consecutive_ai_turns INTEGER NOT NULL DEFAULT 0,
            last_seen_event_id INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'stopped',
            updated_at TEXT NOT NULL,
            metadata_json TEXT DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_live_events_session_time
            ON live_events(bridge_session_id, id);
        CREATE INDEX IF NOT EXISTS idx_live_events_author
            ON live_events(author_channel_id);
        CREATE INDEX IF NOT EXISTS idx_live_events_priority
            ON live_events(bridge_session_id, priority_class, sc_tier, id);
        CREATE INDEX IF NOT EXISTS idx_live_sessions_summary
            ON live_sessions(summary_status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_live_summaries_session
            ON youtube_live_summaries(session_id, id);
        CREATE INDEX IF NOT EXISTS idx_live_summaries_video
            ON youtube_live_summaries(connector_id, video_id);
        CREATE INDEX IF NOT EXISTS idx_live_interactions_session
            ON live_interactions(session_id, id);
        CREATE INDEX IF NOT EXISTS idx_live_interactions_status
            ON live_interactions(session_id, status, priority, id);
        CREATE INDEX IF NOT EXISTS idx_live_presentation_items_session
            ON live_presentation_items(session_id, id);
        CREATE INDEX IF NOT EXISTS idx_live_presentation_items_status
            ON live_presentation_items(session_id, status, id);
        CREATE INDEX IF NOT EXISTS idx_topic_pack_entries_pack
            ON topic_pack_entries(pack_id, id);
        CREATE INDEX IF NOT EXISTS idx_topic_pack_embeddings_pack
            ON topic_pack_entry_embeddings(pack_id, entry_id);
        CREATE INDEX IF NOT EXISTS idx_topic_pack_usages_session
            ON topic_pack_entry_usages(session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_topic_pack_usages_entry
            ON topic_pack_entry_usages(session_id, entry_id);
        CREATE INDEX IF NOT EXISTS idx_topic_graph_nodes_pack
            ON topic_graph_nodes(pack_id, node_type, entry_id);
        CREATE INDEX IF NOT EXISTS idx_topic_graph_edges_pack
            ON topic_graph_edges(pack_id, source_node_id, edge_type);
        CREATE INDEX IF NOT EXISTS idx_topic_graph_traces_session
            ON topic_graph_retrieval_traces(session_id, id);
        CREATE INDEX IF NOT EXISTS idx_research_requests_session
            ON research_requests(session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_live_persona_overlays_enabled
            ON live_persona_overlays(enabled, updated_at);
        """
    )
    ensure_live_session_columns(conn)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_live_sessions_episode_plan
            ON live_sessions(episode_plan_id)
        """
    )
    ensure_live_event_columns(conn)
    ensure_live_persona_overlay_columns(conn)
    conn.commit()


def ensure_live_session_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(live_sessions)").fetchall()}
    columns = {
        "auto_inject": "auto_inject INTEGER NOT NULL DEFAULT 0",
        "episode_plan_id": "episode_plan_id TEXT DEFAULT ''",
        "inject_interval_seconds": "inject_interval_seconds INTEGER NOT NULL DEFAULT 30",
        "inject_min_interval_seconds": "inject_min_interval_seconds INTEGER NOT NULL DEFAULT 10",
        "inject_min_interval_ratio": "inject_min_interval_ratio REAL NOT NULL DEFAULT 0.32",
        "min_pending_events": "min_pending_events INTEGER NOT NULL DEFAULT 1",
        "max_pending_events": "max_pending_events INTEGER NOT NULL DEFAULT 12",
        "dynamic_inject_enabled": "dynamic_inject_enabled INTEGER NOT NULL DEFAULT 1",
        "planned_duration_minutes": "planned_duration_minutes INTEGER NOT NULL DEFAULT 30",
        "auto_finalize_on_duration": "auto_finalize_on_duration INTEGER NOT NULL DEFAULT 1",
        "auto_delete_after_processed": "auto_delete_after_processed INTEGER NOT NULL DEFAULT 1",
        "director_guidance": "director_guidance TEXT DEFAULT ''",
        "host_interaction_rules": "host_interaction_rules TEXT DEFAULT ''",
        "program_segment_plan": "program_segment_plan TEXT DEFAULT ''",
        "program_segment_turns": "program_segment_turns INTEGER NOT NULL DEFAULT 3",
        "auto_test_events_enabled": "auto_test_events_enabled INTEGER NOT NULL DEFAULT 0",
        "test_event_min_seconds": "test_event_min_seconds INTEGER NOT NULL DEFAULT 20",
        "test_event_max_seconds": "test_event_max_seconds INTEGER NOT NULL DEFAULT 45",
        "test_event_count_per_tick": "test_event_count_per_tick INTEGER NOT NULL DEFAULT 3",
        "test_event_use_llm": "test_event_use_llm INTEGER NOT NULL DEFAULT 1",
        "test_super_chat_count_per_tick": "test_super_chat_count_per_tick INTEGER NOT NULL DEFAULT 0",
        "test_malicious_sc_enabled": "test_malicious_sc_enabled INTEGER NOT NULL DEFAULT 0",
        "test_sc_burst_mode": "test_sc_burst_mode INTEGER NOT NULL DEFAULT 0",
        "sc_interrupt_cooldown_seconds": "sc_interrupt_cooldown_seconds INTEGER NOT NULL DEFAULT 30",
        "max_sc_per_batch": "max_sc_per_batch INTEGER NOT NULL DEFAULT 5",
        "director_anchor_every_turns": "director_anchor_every_turns INTEGER NOT NULL DEFAULT 2",
        "director_dialogue_expansion_enabled": "director_dialogue_expansion_enabled INTEGER NOT NULL DEFAULT 1",
        "director_group_turn_limit": "director_group_turn_limit INTEGER NOT NULL DEFAULT 3",
        "director_audience_interrupt_cooldown_seconds": "director_audience_interrupt_cooldown_seconds INTEGER NOT NULL DEFAULT 30",
        "director_max_audience_batches_per_planned_turn": "director_max_audience_batches_per_planned_turn INTEGER NOT NULL DEFAULT 1",
        "director_max_chat_batches_before_anchor": "director_max_chat_batches_before_anchor INTEGER NOT NULL DEFAULT 2",
        "director_offtopic_policy": "director_offtopic_policy TEXT DEFAULT 'defer'",
        "director_sc_burst_policy": "director_sc_burst_policy TEXT DEFAULT 'summarize_batch'",
        "research_enabled": "research_enabled INTEGER NOT NULL DEFAULT 0",
        "research_cooldown_seconds": "research_cooldown_seconds INTEGER NOT NULL DEFAULT 300",
        "research_max_per_session": "research_max_per_session INTEGER NOT NULL DEFAULT 12",
        "auto_sc_thanks_on_finalize": "auto_sc_thanks_on_finalize INTEGER NOT NULL DEFAULT 1",
        "presentation_enabled": "presentation_enabled INTEGER NOT NULL DEFAULT 0",
        "tts_enabled": "tts_enabled INTEGER NOT NULL DEFAULT 0",
        "tts_provider": "tts_provider TEXT DEFAULT 'gpt_sovits'",
        "presentation_ack_timeout_seconds": "presentation_ack_timeout_seconds INTEGER NOT NULL DEFAULT 120",
        "prefetch_wait_timeout_seconds": "prefetch_wait_timeout_seconds REAL NOT NULL DEFAULT 10.0",
        "post_plan_free_talk_enabled": "post_plan_free_talk_enabled INTEGER NOT NULL DEFAULT 0",
        "post_plan_free_talk_minutes": "post_plan_free_talk_minutes INTEGER NOT NULL DEFAULT 20",
        "post_plan_free_talk_tick_interval_seconds": "post_plan_free_talk_tick_interval_seconds INTEGER NOT NULL DEFAULT 30",
        "post_plan_free_talk_idle_turns_min": "post_plan_free_talk_idle_turns_min INTEGER NOT NULL DEFAULT 6",
        "post_plan_free_talk_idle_turns_max": "post_plan_free_talk_idle_turns_max INTEGER NOT NULL DEFAULT 6",
        "post_plan_free_talk_audience_turns_min": "post_plan_free_talk_audience_turns_min INTEGER NOT NULL DEFAULT 3",
        "post_plan_free_talk_audience_turns_max": "post_plan_free_talk_audience_turns_max INTEGER NOT NULL DEFAULT 3",
        "post_plan_free_talk_topic_pack_ids_json": "post_plan_free_talk_topic_pack_ids_json TEXT DEFAULT '[]'",
        "free_talk_closing_target_batches": "free_talk_closing_target_batches INTEGER NOT NULL DEFAULT 10",
        "free_talk_closing_min_batch_size": "free_talk_closing_min_batch_size INTEGER NOT NULL DEFAULT 5",
        "free_talk_closing_max_batch_size": "free_talk_closing_max_batch_size INTEGER NOT NULL DEFAULT 30",
        "free_talk_closing_time_limit_seconds": "free_talk_closing_time_limit_seconds INTEGER NOT NULL DEFAULT 300",
        "started_at": "started_at TEXT DEFAULT ''",
        "finalized_at": "finalized_at TEXT DEFAULT ''",
        "summary_status": "summary_status TEXT NOT NULL DEFAULT 'pending'",
        "summary_id": "summary_id INTEGER",
        "summary_error": "summary_error TEXT DEFAULT ''",
        "summary_updated_at": "summary_updated_at TEXT DEFAULT ''",
    }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE live_sessions ADD COLUMN {ddl}")


def ensure_live_event_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(live_events)").fetchall()}
    columns = {
        "amount_micros": "amount_micros INTEGER NOT NULL DEFAULT 0",
        "sc_tier": "sc_tier INTEGER NOT NULL DEFAULT 0",
        "priority_class": "priority_class TEXT DEFAULT 'normal'",
        "safety_label": "safety_label TEXT DEFAULT 'unclassified'",
        "safety_status": "safety_status TEXT DEFAULT 'pending'",
        "safe_message_text": "safe_message_text TEXT DEFAULT ''",
        "safety_summary": "safety_summary TEXT DEFAULT ''",
        "safety_reason": "safety_reason TEXT DEFAULT ''",
        "safety_confidence": "safety_confidence REAL NOT NULL DEFAULT 0",
        "safety_checked_at": "safety_checked_at TEXT DEFAULT ''",
        "handled_in_closing_at": "handled_in_closing_at TEXT DEFAULT ''",
    }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE live_events ADD COLUMN {ddl}")


def ensure_live_persona_overlay_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(live_persona_overlays)").fetchall()}
    columns = {
        "enabled": "enabled INTEGER NOT NULL DEFAULT 0",
        "mode": "mode TEXT NOT NULL DEFAULT 'replace'",
        "system_prompt": "system_prompt TEXT DEFAULT ''",
        "self_address": "self_address TEXT DEFAULT ''",
        "addressing_json": "addressing_json TEXT DEFAULT '{}'",
        "opening_intro": "opening_intro TEXT DEFAULT ''",
        "reply_rules": "reply_rules TEXT DEFAULT ''",
        "avatar_url": "avatar_url TEXT DEFAULT ''",
        "chat_background_color": "chat_background_color TEXT DEFAULT ''",
        "chat_accent_color": "chat_accent_color TEXT DEFAULT ''",
        "created_at": "created_at TEXT DEFAULT ''",
        "updated_at": "updated_at TEXT DEFAULT ''",
    }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE live_persona_overlays ADD COLUMN {ddl}")
