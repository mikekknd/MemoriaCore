# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language

Always respond in Traditional Chinese (zh-TW). Generate documents, comments, and explanations in Traditional Chinese unless explicitly asked otherwise.

## Project Overview

MemoriaCore — an AI contextual memory engine combining vector retrieval (BGE-M3 ONNX embeddings) with a personality evolution system. Supports multiple frontends: Streamlit web UI, Telegram bot, Unity WebSocket client.

## Commands

### Development
```bash
# Setup (creates venv_ai_memory, installs deps)
setup.bat

# Start both FastAPI + Streamlit
start.bat

# Or run manually:
uvicorn api.main:app --host 0.0.0.0 --port 8088
streamlit run app.py --server.port 8501
```

### Testing
```bash
pytest tests/                          # All tests
pytest tests/test_memory_recall.py -v  # Single test file
pytest -m "not slow"                   # Skip slow tests
```

### Build (PyInstaller standalone binary)
```bash
build_server.bat
# Output: dist/LLMServer/LLMServer.exe
```

## Architecture

### Directory Structure
```
core/           — Core engine modules (memory, LLM routing, personality, storage, etc.)
tools/          — LLM tool implementations (Tavily search, weather, synthetic data)
ui/             — Streamlit UI pages (chat, settings, routing, etc.)
static/         — Static HTML viewers (db_viewer, log_viewer)
api/            — FastAPI backend (routers, models, dependencies)
tests/          — Pytest test suite
```

### Request Flow
```
Client (Streamlit / Telegram / Unity WebSocket)
  → FastAPI (api/main.py, routers under api/routers/)
    → LLMRouter (core/llm_gateway.py) — routes 9 task types to providers
    → MemorySystem (core/core_memory.py) — dual vector search (dense + sparse)
    → MemoryAnalyzer (core/memory_analyzer.py) — topic shift detection, memory pipeline
    → PersonalityEngine (core/personality_engine.py) — self-observation & reflection
    → PreferenceAggregator (core/preference_aggregator.py) — user preference learning
    → StorageManager (core/storage_manager.py) — SQLite WAL + JSON files
```

### Singletons (api/dependencies.py)
All core components are initialized as singletons in `api/dependencies.py` and injected into routers via FastAPI dependency injection: `memory_sys`, `storage`, `analyzer`, `global_router`, `personality_engine`.

### LLM Routing (core/llm_gateway.py)
9 task routes, each independently configurable to a different provider/model via `user_prefs.json` `routing_config`:
`chat`, `pipeline`, `expand`, `compress`, `distill`, `ep_fuse`, `profile`, `ai_observe`, `ai_reflect`.

Supported providers: Ollama (local), OpenAI, OpenRouter, llama.cpp.

### Storage
- **memory_db_*.db** — Per-model SQLite: `memory_blocks` (vectors + weights), `core_memories` (consolidated insights), `user_profile` (facts with confidence)
- **conversation.db** — Sessions and messages
- **user_prefs.json** — Runtime config (models, thresholds, API keys, routing)
- **ai_personality.md** — Evolving personality profile (read/written by PersonalityEngine)
- **system_prompt.txt** — AI system prompt template

### Key Thresholds (in user_prefs.json)
- `memory_threshold` / `memory_hard_base` — Vector similarity cutoffs for recall
- `shift_threshold` — Topic shift detection sensitivity
- `cluster_threshold` — Memory consolidation clustering
- `reflection_threshold` — Observation count before triggering personality reflection

## Constraints

- Python 3.12, NumPy <2.0.0
- ONNX model required at `StreamingAssets/Models/model_quantized.onnx` (BGE-M3 int8 from HuggingFace)
- SQLite uses WAL mode with async locks for concurrency — respect the locking pattern in core/storage_manager.py
- Windows-oriented development (batch scripts), but core Python code is cross-platform
