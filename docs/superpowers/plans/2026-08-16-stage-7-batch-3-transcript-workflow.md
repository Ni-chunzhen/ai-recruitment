# Stage 7 Batch 3 Transcript Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a secure, versioned workflow for importing external TXT/MD interview notes, deterministically parsing speaker segments, manually proofreading them, confirming immutable transcript versions, and completing interview rounds.

**Architecture:** Add revision 012 with transcript master/version/segment tables, a pure deterministic parser, encrypted snapshot versions, a transactional proofreading service, typed FastAPI endpoints, and a Vue proofreading workspace integrated into the existing interview timeline. Reuse the existing interview state machine, Fernet encryption, RBAC, idempotency, optimistic locking and audit systems; do not call AI or external transcription services.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, PostgreSQL, Pydantic, pytest, Vue 3, TypeScript, Vitest, existing frontend component library.

## Global Constraints

- Read `docs/superpowers/specs/2026-08-14-stage-7-batch-3-transcript-workflow-design.md` before implementation.
- Revision must be exactly `012_transcript_workflow`, revise `011_stage7_invitation_confirmation_summary`, and remain under 32 characters.
- Never modify migrations 008–011 or downgrade the development business database.
- Reuse `DATA_ENCRYPTION_KEY`, existing Fernet helpers, RBAC, audit, idempotency and optimistic-lock patterns.
- Supported input is paste, TXT and MD only; maximum file size 2 MiB, text length 500,000 characters and parsed segment count 10,000.
- Decode only UTF-8, UTF-8 BOM and GB18030; process files in memory and never save source binaries or temporary files.
- Do not call AI, external transcription APIs, meeting providers, email/SMS, notification workers or Offer flows.
- Never place transcript plaintext, deleted content, ciphertext, keys, meeting passwords or full contacts in logs, errors or audit payloads.
- `T1` and every `Cn` are immutable; only one `EDITING` draft may exist per transcript.
- First confirmation atomically creates `Cn`, switches the current pointer and moves `PENDING_TRANSCRIPT → COMPLETED`.
- The generic first-batch complete action must not bypass transcript confirmation or the controlled no-transcript path.
- Use TDD: run and capture RED before production changes, then run GREEN.
- Do not use `Any` or untyped business dictionaries in frontend or backend contracts.

---

## File Structure

**Create**

- `backend/alembic/versions/012_transcript_workflow.py`
- `backend/app/models/interview_transcript.py`
- `backend/app/schemas/interview_transcript.py`
- `backend/app/repositories/interview_transcripts.py`
- `backend/app/services/transcript_parser.py`
- `backend/app/services/interview_transcripts.py`
- `backend/app/api/v1/endpoints/interview_transcripts.py`
- `backend/tests/db/test_migration_012.py`
- `backend/tests/integrations/test_migration_012_pg.py`
- `backend/tests/services/test_transcript_parser.py`
- `backend/tests/services/test_interview_transcripts.py`
- `backend/tests/api/v1/test_interview_transcripts.py`
- `frontend/src/components/interviews/TranscriptImportDrawer.vue`
- `frontend/src/components/interviews/TranscriptSegmentEditor.vue`
- `frontend/src/components/interviews/TranscriptConfirmDialog.vue`
- `frontend/src/components/interviews/CompleteWithoutTranscriptDialog.vue`
- `frontend/src/views/InterviewTranscriptView.vue`
- `frontend/tests/TranscriptImportDrawer.spec.ts`
- `frontend/tests/InterviewTranscriptView.spec.ts`

**Modify**

- `backend/app/models/__init__.py`
- `backend/app/models/interview.py`
- `backend/app/schemas/interview.py`
- `backend/app/services/interview_state.py`
- `backend/app/services/interviews.py`
- `backend/app/api/v1/router.py`
- `frontend/src/api/interviews.ts`
- `frontend/src/router/index.ts`
- `frontend/src/views/InterviewTimelineView.vue`
- `frontend/tests/InterviewTimelineView.spec.ts`

Actual filenames may be adapted only after Task 1 reports the repository's existing conventions. Do not create duplicate modules when the project already has an equivalent focused file.

### Task 1: Audit current contracts and establish RED migration head

**Files:**
- Inspect all existing interview models, schemas, services, endpoints and frontend timeline files.
- Create: `backend/tests/db/test_migration_012.py`

**Interfaces:**
- Consumes: actual key types, state actions, permission dependencies, audit writer, idempotency service and crypto signatures.
- Produces: exact reuse report and failing head assertion.

- [ ] **Step 1: Record actual reused symbols in the test module docstring**

```python
"""Stage 7 batch 3 reuses the existing interview state machine,
crypto service, recruitment.manage/interview.execute permissions,
idempotency storage, audit writer and optimistic locking conventions."""
```

- [ ] **Step 2: Write the failing head test**

```python
def test_revision_012_is_head(alembic_script_directory):
    assert alembic_script_directory.get_current_head() == "012_transcript_workflow"
```

- [ ] **Step 3: Run RED**

Run: `cd backend && pytest tests/db/test_migration_012.py::test_revision_012_is_head -q`

Expected: FAIL because current head is 011.

- [ ] **Step 4: Report the audited contracts before changing production code**

Report actual PK types, current head, existing complete endpoint behavior, state transition function, crypto functions/exceptions, audit sanitizer, idempotency API, object-level permission helper, timeline action source and current test counts.

- [ ] **Step 5: Commit RED**

```bash
git add backend/tests/db/test_migration_012.py
git commit -m "test: define transcript workflow migration"
```

### Task 2: Add revision 012 and transcript ORM models

**Files:**
- Create: `backend/alembic/versions/012_transcript_workflow.py`
- Create: `backend/app/models/interview_transcript.py`
- Modify: `backend/app/models/interview.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/db/test_migration_012.py`
- Test: `backend/tests/integrations/test_migration_012_pg.py`

**Interfaces:**
- Produces: `InterviewTranscript`, `InterviewTranscriptVersion`, `InterviewTranscriptSegment` and enums for version type/status, source method, speaker role and source type.
- Produces round fields `transcript_completion_mode`, `transcript_completion_reason_code`, `transcript_completion_reason_description`, `transcript_completed_by`, `transcript_completed_at`.

- [ ] **Step 1: Add failing schema assertions**

```python
EXPECTED_TABLES = (
    "interview_transcripts",
    "interview_transcript_versions",
    "interview_transcript_segments",
)

def test_012_declares_transcript_tables(rendered_upgrade_sql):
    for name in EXPECTED_TABLES:
        assert f"CREATE TABLE {name}" in rendered_upgrade_sql
```

Also assert the five round completion-source fields, their user foreign key, the round uniqueness, version-label uniqueness, one ORIGINAL, one EDITING draft partial index, positive segment number, valid timestamp check and three circular current-version foreign keys.

- [ ] **Step 2: Run RED migration suite**

Run: `cd backend && pytest tests/db/test_migration_012.py -q`

Expected: FAIL because revision/model definitions are absent.

- [ ] **Step 3: Implement migration and matching models**

Use these values exactly:

```python
class TranscriptVersionType(str, Enum):
    ORIGINAL = "ORIGINAL"
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"

class TranscriptVersionStatus(str, Enum):
    EDITING = "EDITING"
    IMMUTABLE = "IMMUTABLE"

class TranscriptSourceMethod(str, Enum):
    PASTE = "PASTE"
    TXT = "TXT"
    MD = "MD"

class TranscriptSpeakerRole(str, Enum):
    CANDIDATE = "CANDIDATE"
    INTERVIEWER = "INTERVIEWER"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"

class TranscriptSegmentSource(str, Enum):
    ORIGINAL = "ORIGINAL"
    CORRECTED = "CORRECTED"
    MANUAL_ADDITION = "MANUAL_ADDITION"
```

Use encrypted text-capable columns, not short varchar fields. Match project UUID/timestamp conventions.

- [ ] **Step 4: Run unit migration GREEN**

Run: `cd backend && pytest tests/db/test_migration_012.py -q`

Expected: PASS.

- [ ] **Step 5: Add and run live PostgreSQL migration tests**

Test an isolated database through `011 → 012 → 011 → 012`, inspect all constraints and insert rows proving duplicate round transcript, duplicate label, second ORIGINAL, second EDITING draft, non-positive segment numbers and invalid time ranges are rejected.

Run: `cd backend && pytest tests/integrations/test_migration_012_pg.py -q`

Expected: PASS and not SKIP when `TEST_DATABASE_URL` is configured.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/012_transcript_workflow.py backend/app/models backend/tests/db/test_migration_012.py backend/tests/integrations/test_migration_012_pg.py
git commit -m "feat: add transcript persistence"
```

### Task 3: Implement safe decoding and deterministic parsing

**Files:**
- Create: `backend/app/services/transcript_parser.py`
- Test: `backend/tests/services/test_transcript_parser.py`

**Interfaces:**
- Produces: `decode_transcript(data: bytes, filename: str) -> DecodedTranscript`.
- Produces: `parse_transcript(text: str) -> ParsedTranscript`.
- Produces frozen typed records for metadata and parsed segments.

- [ ] **Step 1: Write failing decode and parser tests**

```python
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030"])
def test_decodes_supported_encodings(encoding):
    payload = "面试官：请介绍自己".encode(encoding)
    assert "面试官" in decode_transcript(payload, "notes.txt").text

@pytest.mark.parametrize("line", [
    "面试官：请介绍自己",
    "候选人：我有五年经验",
    "Speaker 1: Hello",
    "[00:01:20] 面试官：继续",
    "00:01:20 - 00:01:35 候选人：回答",
])
def test_parses_supported_speaker_formats(line):
    assert parse_transcript(line).segments
```

Add tests for UNKNOWN fallback, multiline continuation, timestamp conversion, UTF decoding failures, TXT/MD extension checks, binary content, empty input, 2 MiB, 500,000 characters and 10,000 segments.

- [ ] **Step 2: Run RED**

Run: `cd backend && pytest tests/services/test_transcript_parser.py -q`

Expected: import failures.

- [ ] **Step 3: Implement pure parser and validators**

Do not access DB, filesystem, network or AI. Normalize CRLF/LF deterministically. For a lone timestamp, persist both time fields as null rather than inventing duration.

- [ ] **Step 4: Run GREEN**

Run: `cd backend && pytest tests/services/test_transcript_parser.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/transcript_parser.py backend/tests/services/test_transcript_parser.py
git commit -m "feat: parse external transcript text"
```

### Task 4: Add typed schemas, repository locks and preview/import service

**Files:**
- Create: `backend/app/schemas/interview_transcript.py`
- Create: `backend/app/repositories/interview_transcripts.py`
- Create: `backend/app/services/interview_transcripts.py`
- Test: `backend/tests/services/test_interview_transcripts.py`

**Interfaces:**
- Produces typed preview/import/list/detail schemas.
- Produces `preview_transcript`, `import_transcript`, `list_transcript_versions`, `get_transcript_version`.

- [ ] **Step 1: Write RED preview/import tests**

Assert preview writes no transcript rows or temp files; import recalculates SHA and parse output; one transaction creates master/T1/segments; original and segment text are encrypted; API-facing objects contain no ciphertext; duplicate import is idempotent; different body under the same key returns conflict; T1 cannot be changed.

- [ ] **Step 2: Run RED**

Run: `cd backend && pytest tests/services/test_interview_transcripts.py -q -k 'preview or import or original'`

Expected: FAIL.

- [ ] **Step 3: Implement typed contracts, repository and service**

Repository mutations lock the round/master rows using existing transaction patterns. Derive `source_type` server-side by comparing submitted preview corrections with deterministic parse output. Encrypt aggregate text and every segment separately.

- [ ] **Step 4: Run GREEN**

Run: `cd backend && pytest tests/services/test_interview_transcripts.py -q -k 'preview or import or original'`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/interview_transcript.py backend/app/repositories/interview_transcripts.py backend/app/services/interview_transcripts.py backend/tests/services/test_interview_transcripts.py
git commit -m "feat: preview and import transcript text"
```

### Task 5: Implement draft snapshots and proofreading operations

**Files:**
- Modify: `backend/app/services/interview_transcripts.py`
- Test: `backend/tests/services/test_interview_transcripts.py`

**Interfaces:**
- Produces: `create_transcript_draft(actor, transcript_id, request)` and `save_transcript_draft(actor, draft_id, request)`.

- [ ] **Step 1: Write RED draft tests**

Cover T1→D1, current Cn→next Dn, duplicate create returning existing draft, corrected text/speaker/role, merge, split, delete, reorder, manual addition, unclear mark, analysis exclusion, complete snapshot replacement, source type derivation, encrypted storage, diff counts and optimistic-lock 409.

- [ ] **Step 2: Run RED**

Run: `cd backend && pytest tests/services/test_interview_transcripts.py -q -k 'draft or merge or split or manual_addition or optimistic'`

Expected: FAIL.

- [ ] **Step 3: Implement draft workflow**

Save one ordered snapshot transactionally. Compute change counts on the server. Audit only IDs, labels and counts. Preserve source references; no-source additions become `MANUAL_ADDITION`.

- [ ] **Step 4: Run GREEN**

Run: `cd backend && pytest tests/services/test_interview_transcripts.py -q -k 'draft or merge or split or manual_addition or optimistic'`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/interview_transcripts.py backend/tests/services/test_interview_transcripts.py
git commit -m "feat: add transcript proofreading drafts"
```

### Task 6: Confirm immutable versions and control no-transcript completion

**Files:**
- Modify: `backend/app/services/interview_transcripts.py`
- Modify: `backend/app/models/interview.py`
- Modify: `backend/app/schemas/interview.py`
- Modify: `backend/app/services/interview_state.py`
- Modify: `backend/app/services/interviews.py`
- Test: `backend/tests/services/test_interview_transcripts.py`
- Test: `backend/tests/services/test_interviews.py`

**Interfaces:**
- Produces `confirm_transcript_draft(actor, draft_id, request)`.
- Produces `complete_without_transcript(actor, round_id, request)` and backend reason codes.

- [ ] **Step 1: Write RED confirmation/state tests**

Assert empty text, invalid time, zero included segments and stale version are rejected; confirmation freezes Dn, creates immutable Cn, switches pointers and completes PENDING_TRANSCRIPT in one transaction; forced failure rolls back all changes; COMPLETED can create C2 without state transition; repeated confirm is idempotent.

Also test reason codes, OTHER description, absence of any transcript before no-transcript completion, recorded completion mode/reason/actor/time, and rejection of the old generic complete bypass.

- [ ] **Step 2: Run RED**

Run: `cd backend && pytest tests/services/test_interview_transcripts.py tests/services/test_interviews.py -q -k 'confirm or without_transcript or complete'`

Expected: FAIL under existing generic completion behavior.

- [ ] **Step 3: Implement atomic confirmation and controlled exception**

Add explicit state actions only. Preserve backward compatibility at the route level only if requests must choose a valid mode; never silently complete. Store completion data on the round using the five fixed 012 fields. Confirmation writes mode `CONFIRMED_TRANSCRIPT` with empty reason fields; no-transcript completion writes mode `WITHOUT_TRANSCRIPT` with reason, actor and timestamp.

- [ ] **Step 4: Run GREEN**

Run: `cd backend && pytest tests/services/test_interview_transcripts.py tests/services/test_interviews.py -q -k 'confirm or without_transcript or complete'`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/interview.py backend/app/schemas/interview.py backend/app/services/interview_state.py backend/app/services/interviews.py backend/app/services/interview_transcripts.py backend/tests/services
git commit -m "feat: confirm transcripts and control completion"
```

### Task 7: Expose APIs with RBAC, no-store and audit redaction

**Files:**
- Create: `backend/app/api/v1/endpoints/interview_transcripts.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/api/v1/test_interview_transcripts.py`

**Interfaces:**
- Produces the nine endpoints from the approved design.

- [ ] **Step 1: Write RED API/RBAC tests**

Cover preview multipart/paste, import, list, detail, draft create/save, confirm, no-transcript complete and reason codes. Assert manager business-scope checks, assigned interviewer current-confirmed-only access, T1/draft/old-confirmed denial, unassigned 404, lists without bodies, details without ciphertext, `Cache-Control: no-store`, 409 mappings, idempotency and audit redaction.

- [ ] **Step 2: Run RED**

Run: `cd backend && pytest tests/api/v1/test_interview_transcripts.py -q`

Expected: route/import failures.

- [ ] **Step 3: Implement typed endpoints**

Use existing permission dependencies and object lookup behavior. Never log multipart bodies. Stream/read at most the configured limit and reject excess bytes before decoding.

- [ ] **Step 4: Run GREEN and interview regression**

Run: `cd backend && pytest tests/api/v1/test_interview_transcripts.py tests/api/v1/test_interviews.py tests/services/test_interviews.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/endpoints/interview_transcripts.py backend/app/api/v1/router.py backend/app/models/__init__.py backend/tests/api/v1/test_interview_transcripts.py
git commit -m "feat: expose transcript workflow api"
```

### Task 8: Add typed frontend API, import drawer and proofreading workspace

**Files:**
- Modify: `frontend/src/api/interviews.ts`
- Create: `frontend/src/components/interviews/TranscriptImportDrawer.vue`
- Create: `frontend/src/components/interviews/TranscriptSegmentEditor.vue`
- Create: `frontend/src/components/interviews/TranscriptConfirmDialog.vue`
- Create: `frontend/src/components/interviews/CompleteWithoutTranscriptDialog.vue`
- Create: `frontend/src/views/InterviewTranscriptView.vue`
- Test: `frontend/tests/TranscriptImportDrawer.spec.ts`
- Test: `frontend/tests/InterviewTranscriptView.spec.ts`

**Interfaces:**
- Produces typed transcript API functions and the `/interview-rounds/:roundId/transcript` page.

- [ ] **Step 1: Write RED import tests**

Test paste/TXT/MD tabs, file accept restrictions, preview metadata, deterministic-parser notice, UNKNOWN display, preview corrections, second confirmation and idempotency key.

- [ ] **Step 2: Write RED editor tests**

Test version history, immutable T1/Cn, speaker/role/text editing, merge, split, delete, add, reorder, manual-addition label, unclear/excluded styles, save version/idempotency, unsaved-change guard, confirmation summary/blockers, 409 refresh and interviewer read-only mode.

- [ ] **Step 3: Run RED**

```bash
cd frontend
pnpm vitest run tests/TranscriptImportDrawer.spec.ts tests/InterviewTranscriptView.spec.ts
```

Expected: FAIL because components and API types do not exist.

- [ ] **Step 4: Implement typed API and focused components**

Use the existing PC console design, right drawers, status tags and fixed bottom action bar. Do not use `any`, render Markdown, or offer full-text export/copy.

- [ ] **Step 5: Run GREEN**

Run: `cd frontend && pnpm vitest run tests/TranscriptImportDrawer.spec.ts tests/InterviewTranscriptView.spec.ts`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/interviews.ts frontend/src/components/interviews frontend/src/views/InterviewTranscriptView.vue frontend/tests/TranscriptImportDrawer.spec.ts frontend/tests/InterviewTranscriptView.spec.ts
git commit -m "feat: add transcript import and proofreading ui"
```

### Task 9: Integrate timeline route and completion actions

**Files:**
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/views/InterviewTimelineView.vue`
- Modify: `frontend/tests/InterviewTimelineView.spec.ts`

**Interfaces:**
- Consumes Task 8 page/components and backend allowed actions.
- Produces correct PENDING_TRANSCRIPT/COMPLETED/interviewer-only entry points.

- [ ] **Step 1: Write RED timeline tests**

Assert PENDING_TRANSCRIPT shows import/no-transcript, imported shows start/view original, draft shows continue/confirm, COMPLETED shows current/history/reproofread, assigned interviewer shows current-confirmed view only, reason codes load from API, OTHER validates, success refreshes, and forbidden DOCX/audio/video/AI/Offer controls are absent.

- [ ] **Step 2: Run RED**

Run: `cd frontend && pnpm vitest run tests/InterviewTimelineView.spec.ts`

Expected: FAIL on transcript actions.

- [ ] **Step 3: Implement route and timeline integration**

Use backend state, permissions and transcript summary as source of truth. Do not reproduce the backend state machine in frontend constants.

- [ ] **Step 4: Run GREEN**

Run: `cd frontend && pnpm vitest run tests/InterviewTimelineView.spec.ts tests/TranscriptImportDrawer.spec.ts tests/InterviewTranscriptView.spec.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/router/index.ts frontend/src/views/InterviewTimelineView.vue frontend/tests/InterviewTimelineView.spec.ts
git commit -m "feat: integrate transcript workflow timeline"
```

### Task 10: Full verification and completion evidence

**Files:**
- Modify only scoped defects discovered by verification.

**Interfaces:**
- Produces the 17-item completion report from the design specification.

- [ ] **Step 1: Run focused backend suites**

```bash
cd backend
pytest tests/db/test_migration_012.py tests/integrations/test_migration_012_pg.py tests/services/test_transcript_parser.py tests/services/test_interview_transcripts.py tests/api/v1/test_interview_transcripts.py -q
```

Expected: zero failures; PG live test executes when isolated URL is configured.

- [ ] **Step 2: Run all backend tests**

Run: `cd backend && pytest -q`

Expected: zero failures. Report every skip and its reason.

- [ ] **Step 3: Run focused and full frontend tests**

```bash
cd frontend
pnpm vitest run tests/TranscriptImportDrawer.spec.ts tests/InterviewTranscriptView.spec.ts tests/InterviewTimelineView.spec.ts
pnpm vitest run
```

Expected: zero failures.

- [ ] **Step 4: Run frontend type-check and build**

```bash
cd frontend
pnpm type-check
pnpm build
```

Expected: both exit 0.

- [ ] **Step 5: Verify migrations**

In isolated PostgreSQL run `011 → 012 → 011 → 012`. In development run only `alembic upgrade head`, then `alembic current`.

Expected: `012_transcript_workflow (head)`.

- [ ] **Step 6: Verify prohibited scope**

Inspect the diff and dependency manifests: no DOCX parser, audio/video handling, temporary upload persistence, transcription provider, AI call/task, full-text index, notification worker, application decision or Offer change.

- [ ] **Step 7: Commit scoped verification fixes**

```bash
git add backend/alembic/versions/012_transcript_workflow.py backend/app backend/tests frontend/src frontend/tests
git commit -m "test: verify transcript workflow closure"
```

- [ ] **Step 8: Produce completion report**

Report reused infrastructure, 012 schema, decoding/limits, parser rules, T1/Dn/Cn and encryption, proofreading/source types, optimistic locking, atomic confirmation, no-transcript path, RBAC, audit redaction, frontend evidence, scope exclusion, RED/GREEN, full verification, PG migration and current Alembic version.
