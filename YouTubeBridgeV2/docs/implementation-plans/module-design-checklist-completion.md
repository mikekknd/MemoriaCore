# Module Design Checklist Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking. When writing each module's implementation plan, follow `superpowers:writing-plans`.

**Goal:** Complete all remaining YouTubeBridgeV2 module design documents and their implementation plan documents in the order defined by `docs/architecture-index.md`.

**Architecture:** This is a docs-only planning workflow. Each module is handled as one independent documentation unit: first finish `docs/modules/<module>.md`, then create `docs/implementation-plans/<module>.md`, then sync API and architecture indexes only when the module introduces public contracts or checklist status changes.

**Tech Stack:** Markdown, Mermaid where useful, V2 docs contract, Red-Green-Refactor planning.

---

## Scope

This plan completes documentation plans only. It does not create runtime Python code, tests, API routes, UI files, storage schemas, or adapter implementations.

Existing completed documents:

- `YouTubeBridgeV2/docs/modules/runtime-phase.md`
- `YouTubeBridgeV2/docs/implementation-plans/runtime-phase.md`
- `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
- `YouTubeBridgeV2/docs/implementation-plans/runtime-application-service.md`
- `YouTubeBridgeV2/docs/modules/live-episode-plan.md`
- `YouTubeBridgeV2/docs/implementation-plans/live-episode-plan.md`
- `YouTubeBridgeV2/docs/modules/aftertalk.md`
- `YouTubeBridgeV2/docs/implementation-plans/aftertalk.md`
- `YouTubeBridgeV2/docs/modules/memoria-adapter.md`
- `YouTubeBridgeV2/docs/implementation-plans/memoria-adapter.md`
- `YouTubeBridgeV2/docs/modules/closing.md`
- `YouTubeBridgeV2/docs/implementation-plans/closing.md`
- `YouTubeBridgeV2/docs/modules/storage.md`
- `YouTubeBridgeV2/docs/implementation-plans/storage.md`
- `YouTubeBridgeV2/docs/modules/server-api-surface.md`
- `YouTubeBridgeV2/docs/implementation-plans/server-api-surface.md`
- `YouTubeBridgeV2/docs/modules/access-control-security.md`
- `YouTubeBridgeV2/docs/implementation-plans/access-control-security.md`
- `YouTubeBridgeV2/docs/modules/observability.md`
- `YouTubeBridgeV2/docs/implementation-plans/observability.md`
- `YouTubeBridgeV2/docs/modules/operator-console-ui.md`
- `YouTubeBridgeV2/docs/implementation-plans/operator-console-ui.md`
- `YouTubeBridgeV2/docs/modules/chat-display-ui.md`
- `YouTubeBridgeV2/docs/implementation-plans/chat-display-ui.md`
- `YouTubeBridgeV2/docs/modules/youtube-adapter.md`
- `YouTubeBridgeV2/docs/implementation-plans/youtube-adapter.md`
- `YouTubeBridgeV2/docs/modules/presentation-tts.md`
- `YouTubeBridgeV2/docs/implementation-plans/presentation-tts.md`

Module design order:

1. `runtime-application-service.md`
2. `live-episode-plan.md`
3. `aftertalk.md`
4. `memoria-adapter.md`
5. `closing.md`
6. `storage.md`
7. `server-api-surface.md`
8. `access-control-security.md`
9. `observability.md`
10. `operator-console-ui.md`
11. `chat-display-ui.md`
12. `youtube-adapter.md`
13. `presentation-tts.md`

Every module task must end with the same local verification:

```powershell
$forbidden = @('TO' + 'DO', 'TB' + 'D', 'FIX' + 'ME') -join '|'
rg -n $forbidden YouTubeBridgeV2
git status --porcelain=v1 --untracked-files=all
```

Expected result:

- the marker scan returns no matching lines
- git status changes remain under `YouTubeBridgeV2/`
- no old `YouTubeBridge/` path appears in modified or untracked output

## Required Shape For Each Module

Each `docs/modules/<module>.md` must use this section order:

```markdown
# <Module Name> Module Design

## Purpose

## Ownership

## Inputs

## Outputs

## Dependencies

## Out Of Scope

## Public Entrypoints

## Failure Modes

## Test Strategy

## Open Questions
```

Each `docs/implementation-plans/<module>.md` must use this section order:

```markdown
# <Module Name> Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** <one sentence>

**Architecture:** <two or three sentences>

**Tech Stack:** Python 3.12, pytest, and any module-specific runtime surfaces.

---

## Scope

## Planned Symbols

## Red Cases

## Green Scope

## Refactor Boundary

## Adapter Strategy

## Docs Sync

## Execution Steps

## Acceptance Criteria
```

If a module is UI-only or docs-only, `Red Cases` must still define contract or smoke-test scenarios. If a module has no direct adapter dependency, `Adapter Strategy` must explicitly say so.

## Task 1: Runtime Application Service Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/runtime-application-service.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for runtime command orchestration, side-effect ordering, idempotency, recovery, and event publishing.
- [ ] Define planned public contracts for `RuntimeApplicationService`, command envelope, service result, event, transition reference, adapter dispatch result, and recovery decision.
- [ ] Create the implementation plan with red cases for command delegation, snapshot-first phase decision, next action dispatch, manual close priority, duplicate command id, retryable adapter error, storage failure, crash recovery, and public event redaction.
- [ ] Update API reference only for conceptual contracts. Do not add Source values until runtime code exists.
- [ ] Mark `runtime-application-service.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Task 2: LiveEpisodePlan Runner Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/live-episode-plan.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/live-episode-plan.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify if needed: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for the planned show runner. It must define how imported LiveEpisodePlan data becomes planned turn execution intent, speaker policy, audience event handling policy, completion signal, and handoff back to Runtime Phase.
- [ ] Keep the module out of prompt generation and adapter calls. It may output execution intent, but MemoriaCore calls stay in `memoria-adapter.md`.
- [ ] Define planned public contracts for plan status, current turn intent, planned turn result, and completion signal as conceptual entries.
- [ ] Create the implementation plan with red cases for valid plan loading, turn advancement, fixed speaker policy, audience insertion boundaries, plan completion, invalid plan contract, and no raw Topic Pack prompt injection.
- [ ] Update API reference only for conceptual public contracts. Do not add Source values until runtime code exists.
- [ ] Mark `live-episode-plan.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Task 3: Aftertalk Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/aftertalk.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/aftertalk.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify if needed: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for aftertalk as a V2 phase feature triggered by Runtime Phase.
- [ ] Define ownership around aftertalk entry conditions, stop conditions, cue generation, speaker rotation hints, and group chat handoff.
- [ ] Keep role personality, actual LLM output generation, MemoriaCore transport, YouTube polling, and UI rendering out of scope.
- [ ] Define planned public contracts for aftertalk cue, aftertalk turn request, aftertalk stop reason, and aftertalk session summary as conceptual entries.
- [ ] Create the implementation plan with red cases for auto entry when duration allows, disabled policy bypass, manual close, duration stop, cue metadata minimization, and no Legacy director usage.
- [ ] Update API reference only for conceptual public contracts. Do not add Source values until runtime code exists.
- [ ] Mark `aftertalk.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Task 4: MemoriaCore Adapter Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/memoria-adapter.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/memoria-adapter.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify if needed: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for translating V2 planned show and aftertalk requests into MemoriaCore chat or group chat requests.
- [ ] Define request ownership, response normalization, session id handling, correlation metadata, timeout behavior, retry boundary, and error classification.
- [ ] Keep phase decisions, plan advancement, storage transactions, YouTube polling, and UI rendering out of scope.
- [ ] Define planned public contracts for Memoria request payload, normalized Memoria response, adapter error, and correlation metadata.
- [ ] Create the implementation plan with red cases for planned show request mapping, aftertalk group chat request mapping, response normalization, timeout classification, transport failure classification, and hidden prompt minimization.
- [ ] Update API reference only for conceptual public contracts. Do not add Source values until runtime code exists.
- [ ] Mark `memoria-adapter.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Task 5: Closing Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/closing.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/closing.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for closing reason, final message intent, Super Chat acknowledgement, finalization result, completion status, idempotency, and recovery.
- [ ] Define planned public contracts for `ClosingStartContext`, `ClosingReason`, `ClosingPolicy`, `ClosingRequest`, `ClosingSuperChatAction`, `ClosingFinalizationResult`, `ClosingCompletionStatus`, and `ClosingDisplayEvent`.
- [ ] Create the implementation plan with red cases for manual close, duration reached, stream ended, pending Super Chat acknowledgement, malformed Super Chat, final message disabled, Memoria timeout, terminal fallback, duplicate closing command, completion status, and redacted display event.
- [ ] Update API reference only for conceptual contracts. Do not add Source values until runtime code exists.
- [ ] Mark `closing.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Task 6: Storage Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/storage.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/storage.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify if needed: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for V2 session, phase state, events, interactions, adapter metadata, and finalization records.
- [ ] Define repository boundaries and the session snapshot shape consumed by Runtime Phase.
- [ ] Keep durable persistence behind the existing `StorageManager` boundary. Do not design a V2-owned SQLite implementation.
- [ ] Define planned public contracts for session repository, event repository, interaction repository, transition write, and snapshot read.
- [ ] Create the implementation plan with red cases for create session, read snapshot, write transition, append event, append interaction, idempotent transition write, and no direct SQLite access from `YouTubeBridgeV2/`.
- [ ] Update API reference only for conceptual public contracts. Do not add Source values until runtime code exists.
- [ ] Mark `storage.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Task 7: Server/API Surface Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/server-api-surface.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/server-api-surface.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify if needed: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for HTTP and SSE entrypoints used by operator console, chat display, observer tools, and external runtime controls.
- [ ] Define request and response ownership, runtime service boundary, event stream boundary, and separation from phase decisions.
- [ ] Keep auth policy details delegated to `access-control-security.md`.
- [ ] Define planned endpoint contracts for session create/read, plan bind/import, phase status, manual close, aftertalk policy update, event stream, and display event stream.
- [ ] Create the implementation plan with red cases for request validation, service delegation, SSE event shape, manual close API, aftertalk toggle API, error response shape, and no direct adapter calls from routes.
- [ ] Update API reference with conceptual HTTP/SSE entries. Do not add Source values until routes exist.
- [ ] Mark `server-api-surface.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Task 8: Access Control / Security Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/access-control-security.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/access-control-security.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify if needed: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for loopback access, API key rules, MemoriaCore auth delegation, untrusted payload boundaries, secret handling, and safe error responses.
- [ ] Define which API groups are operator-only, display-readable, observer-readable, or internal.
- [ ] Keep route implementation details in `server-api-surface.md`.
- [ ] Define planned public contracts for auth requirement metadata, permission groups, sanitized error response, and secret/config boundary.
- [ ] Create the implementation plan with red cases for missing key, invalid key, loopback access, display read-only access, forbidden control action, sanitized errors, and no secret leakage.
- [ ] Update API reference with conceptual security entries. Do not add Source values until implementation exists.
- [ ] Mark `access-control-security.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Task 9: Observability Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/observability.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/observability.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify if needed: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for phase transition logs, adapter request summaries, error classification, trace lookup, and correlation ids.
- [ ] Define public/private boundaries so hidden prompts, raw Topic Packs, raw MemoriaCore payloads, and unnecessary user data stay out of visible diagnostics.
- [ ] Define planned public contracts for transition log entry, adapter trace summary, error event, and correlation id propagation.
- [ ] Create the implementation plan with red cases for transition log shape, adapter summary redaction, error classification, correlation id continuity, trace lookup, and hidden prompt exclusion.
- [ ] Update API reference only for conceptual event or observer entries. Do not add Source values until implementation exists.
- [ ] Mark `observability.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Task 10: Operator Console UI Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/operator-console-ui.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/operator-console-ui.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify if needed: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for the operator console information architecture and controls.
- [ ] Define visible state for phase, LiveEpisodePlan progress, Aftertalk switch, remaining time, closing state, errors, and manual controls.
- [ ] Keep runtime phase decisions, adapter calls, storage writes, and chat display rendering out of scope.
- [ ] Define planned UI event and API dependencies for status polling or SSE, aftertalk policy update, manual close, and error display.
- [ ] Create the implementation plan with red cases for phase state rendering, aftertalk toggle, remaining time display, manual close action, disabled control states, error banner, and API boundary.
- [ ] Update API reference only for conceptual UI-facing events or endpoint usage. Do not add Source values until implementation exists.
- [ ] Mark `operator-console-ui.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Task 11: Chat Display UI Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/chat-display-ui.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/chat-display-ui.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify if needed: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for the livestream-facing chat display.
- [ ] Define rendering responsibilities for audience messages, character messages, Super Chat, system phase status, aftertalk status, closing status, and presentation/TTS metadata.
- [ ] Keep operator controls, runtime decisions, adapter calls, and storage writes out of scope.
- [ ] Define planned display event contracts for message event, system state event, Super Chat event, character response event, and presentation metadata.
- [ ] Create the implementation plan with red cases for event rendering, role labeling, Super Chat styling metadata, aftertalk status rendering, closing status rendering, display-only permission, and no control API calls.
- [ ] Update API reference with conceptual display event entries. Do not add Source values until implementation exists.
- [ ] Mark `chat-display-ui.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Task 12: YouTube Adapter Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/youtube-adapter.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/youtube-adapter.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify if needed: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for live chat polling, event normalization, Super Chat metadata, stream status, pagination, rate limits, retries, and adapter error classification.
- [ ] Keep phase decisions, MemoriaCore calls, storage transaction details, UI rendering, and closing script generation out of scope.
- [ ] Define planned public contracts for normalized YouTube event, polling cursor, Super Chat metadata, stream status, and adapter error.
- [ ] Create the implementation plan with red cases for event normalization, pagination cursor, duplicate event handling, Super Chat metadata, live ended state, transient failure retry classification, and no phase transition side effects.
- [ ] Update API reference only for conceptual adapter entries. Do not add Source values until implementation exists.
- [ ] Mark `youtube-adapter.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Task 13: Presentation/TTS Documents

**Files:**

- Create: `YouTubeBridgeV2/docs/modules/presentation-tts.md`
- Create: `YouTubeBridgeV2/docs/implementation-plans/presentation-tts.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify if needed: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Write the module design for consuming completed interaction or response events and producing optional visual or voice output.
- [ ] Define queue ownership, ack behavior, timeout behavior, metadata visibility, and non-decision boundary with runtime phase.
- [ ] Keep LLM generation, phase transition, storage ownership, YouTube polling, and operator controls out of scope.
- [ ] Define planned public contracts for presentation event, TTS request, delivery ack, timeout result, and display metadata.
- [ ] Create the implementation plan with red cases for event consumption, queue ordering, ack success, timeout result, metadata redaction, disabled TTS behavior, and no phase decision side effects.
- [ ] Update API reference only for conceptual presentation/TTS entries. Do not add Source values until implementation exists.
- [ ] Mark `presentation-tts.md` complete in the architecture checklist.
- [ ] Run the shared verification commands.

## Final Verification

After all tasks are complete:

- [ ] Confirm every item in `docs/architecture-index.md` Module Design Checklist is checked.
- [ ] Confirm every remaining module has both `docs/modules/<module>.md` and `docs/implementation-plans/<module>.md`.
- [ ] Confirm `docs/api-reference-index.md` has conceptual entries for public contracts introduced by the module designs, with no Source values for unimplemented runtime symbols.
- [ ] Confirm docs remain under `YouTubeBridgeV2/`.
- [ ] Confirm no old `YouTubeBridge/` file is modified.
- [ ] Run the shared verification commands.

## Execution Notes

- Work one module at a time in checklist order.
- Do not pre-write implementation plans for later modules before the matching module design exists.
- If a module design changes lifecycle, Legacy boundary, or module dependency, update `README.md` and `docs/architecture-index.md` in the same task.
- If a module adds endpoint, event payload, public function, class, or adapter contract, update `docs/api-reference-index.md` in the same task.
- Keep root `YouTubeBridgeV2/` limited to `README.md`, `CLAUDE.md`, and `docs/`.
