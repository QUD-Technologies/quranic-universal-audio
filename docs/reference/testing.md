# Testing

> **Scope.** Backend tests live under `inspector/tests/` (pytest) and `qua_shared/tests/` (pytest). Frontend tests live co-located with their modules under `inspector/frontend/src/**/__tests__/*.test.ts` (vitest + happy-dom + @testing-library/svelte). Shared JSON fixtures live under `inspector/tests/fixtures/segments/` and are aliased to `@fixtures` in vitest. CI runs all three suites; coverage is measured but not gated.

Where it lives:

| Concern | Location |
|---|---|
| BE pytest tree | `inspector/tests/` (subdirs mirror the prod tree: `services/`, `routes/`, `db/`, `persistence/`, `command/`, `undo/`, `classifier/`, `registry/`, `identity/`, `admin/`, `parity/`) |
| BE shared fixtures + autouse substrate | `inspector/tests/conftest.py` |
| qua_shared pytest tree | `qua_shared/tests/` (standalone — uses its own sys.path bootstrap) |
| FE vitest tree | colocated under `inspector/frontend/src/**/__tests__/` |
| FE setup + global fetch stub | `inspector/frontend/vitest.setup.ts` |
| FE vitest config + coverage exclude list | `inspector/frontend/vitest.config.ts` |
| BE coverage config | `inspector/pyproject.toml` `[tool.coverage]` |
| CI workflow | `.github/workflows/inspector-checks.yml` |
| JSON fixtures (BE + FE) | `inspector/tests/fixtures/segments/` (`@fixtures` alias from FE) |
| Test-only segment factories | `inspector/frontend/src/tabs/segments/__tests__/helpers/make-segment.ts` |

A new subsystem maps to a new pytest subdir + a colocated FE `__tests__/` folder. Don't add tests at the repo root or under `inspector/tests/` directly — pick the subdir matching the subsystem reference doc (see [docs/reference/README.md](README.md)).

## Runners and tools

**Backend (pytest).** Python 3.11, pytest ≥7. The autouse `_substrate_db` fixture in `inspector/tests/conftest.py` provisions a fresh, migrated SQLite DB at `tmp_path/inspector-test.db` for every test and disables bucket sync. Teardown resets the global db_seq counter and invalidates the db_seq-keyed caches (`public_reciters`, `catalog_snapshot`, `admin_users`, `admin_requests`, `capability_matrix`). No test sees another's DB rows.

**Frontend (vitest).** vitest with the v8 coverage provider, happy-dom for the DOM, `@testing-library/svelte` for component rendering, `@testing-library/user-event` for interactions. `vitest.setup.ts` installs a global fetch stub that returns `200 {}` for every URL (with a special case for `/surah-info/*`).

**Shared mock infrastructure.**

| Need | BE | FE |
|---|---|---|
| Tmp filesystem | pytest `tmp_path` + `FilesystemBackend` | happy-dom |
| HTTP client | `flask_client` / `signed_in_client` | global `fetch` stub in `vitest.setup.ts` |
| Auth identity | `signed_in_client(role=...)` mints a signed cookie | `currentUser.set(...)` against the real store |
| Per-reciter bucket files | `tmp_reciter_dir.install(slug, fixture)` | n/a |

End-to-end Playwright lives under `inspector/frontend/tests/e2e/` (`npm run test:e2e`) — out of scope for the unit-test contract.

## Running tests

| Task | Command |
|---|---|
| BE all | `cd inspector && python -m pytest tests/ -v` |
| BE single test | `python -m pytest tests/db/test_repo_state.py::test_name -v` |
| BE single file | `python -m pytest tests/routes/test_route_save.py -v` |
| BE keyword | `python -m pytest tests/ -k "transition and reject" -v` |
| BE with coverage | `python -m pytest --cov=services --cov=routes --cov=domain --cov=adapters --cov-report=term-missing tests/` |
| qua_shared | `python -m pytest qua_shared/tests -v` |
| FE all | `cd inspector/frontend && npm run test` |
| FE single | `npx vitest run src/lib/playback/__tests__/audio-graph.test.ts` |
| FE watch | `npx vitest src/lib/playback/__tests__/audio-graph.test.ts` |
| FE with coverage | `npm run test:coverage` |
| FE typecheck | `npm run check` |
| FE lint | `npm run lint` |

CI does not gate on coverage thresholds (`thresholds: undefined` in `vitest.config.ts`; no `fail_under` in `pyproject.toml`). Coverage is informational. See [Coverage](#coverage) below.

## How to add a test for X

Four walkthroughs covering the common cases. Pick the one closest to your work and copy the shape.

### 1. State-machine transition rule (BE)

Tests for `transition()` lifecycle moves live under `inspector/tests/services/test_state_*.py` (event-centric) or `inspector/tests/db/test_repo_transitions.py` (storage-centric). Seed the FROM state via `seed_state`, call `transition()`, assert the post-state row + the audit row.

```python
from datetime import datetime, timezone

from qua_shared.schemas import Actor, ReciterState, Role
from services.db import repo_state, repo_transitions
from services.state import transition


def _actor(role: str = "contributor") -> Actor:
    return Actor(hf_user_id="u-1", login_at_time="alice", role=Role(role))


def test_request_moves_catalogued_to_awaiting_alignment(seed_state):
    seed_state("d1", state="catalogued")

    transition(
        "d1",
        event="reciter.requested",
        actor=_actor(),
        now=datetime.now(timezone.utc),
        payload={"request_id": "req-1"},
    )

    row = repo_state.get_row("d1")
    assert row.state == ReciterState.AWAITING_ALIGNMENT
    transitions = repo_transitions.for_slug("d1")
    assert transitions[-1].event == "reciter.requested"
    assert transitions[-1].actor.hf_user_id == "u-1"
```

Rules:
- Drive state through `transition()`, never via direct `repo_state.update_state` ad-hoc — that's the durability boundary the state-machine doc enforces.
- For audit assertions, prefer `repo_transitions.for_slug(slug)` over patching `audit.append`. If you must record-and-assert, **call through** to the real implementation (see [Mocking boundaries](#mocking-boundaries)).
- See [`state-machine.md`](state-machine.md) for the full event matrix.

### 2. HTTP route handler (BE)

Route tests live under `inspector/tests/routes/test_route_<name>.py`. Use `signed_in_client` to mint a cookie. If the route reads or writes per-reciter bucket content, also request `tmp_reciter_dir` and call `.install(slug, fixture)` or `.seed_under_review(slug, hf_user_id)`. Mutating routes go through `require_same_origin` — include the `Origin` header.

```python
import json

_HEADERS = {"Content-Type": "application/json", "Origin": "http://localhost"}


def test_save_assignee_succeeds(signed_in_client, tmp_reciter_dir):
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas", under_review_for="test-user-1")
    client, user = signed_in_client(hf_user_id="test-user-1", login="alice")

    res = client.post(
        f"/api/seg/save/{reciter}/112",
        data=json.dumps({"segments": [], "operations": []}),
        headers=_HEADERS,
    )

    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True


def test_save_cross_origin_rejected(signed_in_client, tmp_reciter_dir):
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas", under_review_for="test-user-1")
    client, _ = signed_in_client(hf_user_id="test-user-1")

    res = client.post(
        f"/api/seg/save/{reciter}/112",
        data=json.dumps({"segments": [], "operations": []}),
        headers={"Content-Type": "application/json", "Origin": "http://evil.example"},
    )

    assert res.status_code == 403
    assert res.get_json()["code"] == "SAME_ORIGIN_REQUIRED"
```

Rules:
- Assert exact `status_code == N`; never `in (200, 4xx)` — it masks which branch ran.
- For 4xx responses, assert `body["code"]` too. The error envelope is part of the contract — see [`auth-permissions.md`](auth-permissions.md).
- Anonymous requests use `flask_client` (no cookie). Don't reach for `flask_client` when `signed_in_client` is what you need.

### 3. Segments editor command (FE)

Command tests live under `inspector/frontend/src/tabs/segments/__tests__/command/<command>.test.ts`. Build state via `makeApplyCommandState` + `makeSegment` from the helpers module, call `applyCommand(state, command)` directly, and assert on `result.nextState` + `result.operation`.

```ts
import { describe, expect, it } from 'vitest';

import { applyCommand } from '../../domain/apply-command';
import type { TrimCommand } from '../../domain/command';
import { makeApplyCommandState, makeSegment } from '../helpers/make-segment';

const baseState = () => makeApplyCommandState([
  makeSegment(0, 0, 2000, { segment_uid: 'uid-trim' }),
]);

const baseCmd: TrimCommand = {
  type: 'trim',
  segmentUid: 'uid-trim',
  delta: { time_start: 250 },
};

describe('command/trim', () => {
  it('mutates time_start to the delta value', () => {
    const r = applyCommand(baseState(), baseCmd);
    const updated = r.nextState.byId?.['uid-trim'];
    expect(updated.time_start).toBe(250);
  });

  it('records before + after snapshots on the operation', () => {
    const r = applyCommand(baseState(), baseCmd);
    expect(r.operation.snapshots?.before).toBeTruthy();
    expect(r.operation.snapshots?.after).toBeTruthy();
  });
});
```

For the BE save-flow side of the same command (JSONL round-trip, undo), pair the FE test with a `inspector/tests/persistence/` or `inspector/tests/command/` test that drives the same command through `/api/seg/save/<slug>/<ch>` and reads the persisted batch back via `parse_edit_history_line`.

Rules:
- Don't gate new tests behind `loadOptional` + `describe.skipIf`. That convention is for tests landing ahead of their implementation on the same branch series — flag stale `skipIf` gates as soon as the module exists. See [Phase-gates](#phase-gates).
- Type command literals against the appropriate `SegmentCommand` member; avoid `as any`.
- See [`segments-editor.md`](segments-editor.md) for the command grammar.

### 4. Frontend component (@testing-library/svelte)

Component tests live colocated with the component under `__tests__/<Name>.test.ts`. Render via `render(Component, { props })`, query with `screen.getByRole`/`getByText`, and assert against the DOM. For components reading shared stores, import the real store and `set()` it before rendering.

```ts
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import type { ReciterTask } from '../../api/reciter-task';
import { currentUser } from '../../stores/current-user';
import ClaimButton from '../ClaimButton.svelte';

function makeTask(canClaim: boolean): ReciterTask {
  return {
    row: {
      slug: 'this-reciter', name: 'X', state: 'awaiting_review',
      state_since: '2026-01-01T00:00:00Z',
      assignee_hf_id: null, assignee_login: null, assignee_since: null,
      marked_ready: false, visibility: 'public',
    },
    predicates: { can_claim: canClaim, can_edit: false, /* ... */ },
  } as ReciterTask;
}

describe('ClaimButton', () => {
  it('renders nothing for an anonymous user', () => {
    currentUser.set(null);

    const { container } = render(ClaimButton, {
      props: { slug: 'this-reciter', task: makeTask(true), onClaimed: null },
    });

    expect(container.querySelector('button')).toBeNull();
  });
});
```

Rules:
- Svelte 5 (runes) components: pass callback props directly (`onClaimed: () => {}`); don't use `events: {...}` (that's the Svelte 4 `createEventDispatcher` pattern). See the Svelte conventions in `CLAUDE.md`.
- Svelte 4 legacy components keep working — the canvas/audio-imperative components (`TimestampsWaveform.svelte`, `WaveformCanvas.svelte`, etc.) are deliberately exempt from migration.
- For accessibility assertions, prefer `getByRole` over `querySelector`.

## Fixture cheatsheet

The autouse `_substrate_db` already gives every test a fresh migrated SQLite DB. Don't reach for `tmp_reciter_dir` if SQLite state is all you need.

| You need | Use |
|---|---|
| Just a SQLite DB | nothing — autouse `_substrate_db` runs for every test |
| Seed a `delivery_states` row + FK chain | `seed_state(slug, state=, assignee_hf_id=, marked_ready=, ...)` |
| Seed a member's role | `seed_role(hf_user_id, login=, role=)` |
| Mint a signed-in test client | `signed_in_client(role=...)` → `(client, user_dict)` |
| Per-reciter bucket content from a fixture | `tmp_reciter_dir.install(slug, fixture_name, under_review_for=...)` |
| Per-reciter content without a fixture (hand-author files) | `state_persistence` — installs `FilesystemBackend`, returns it |
| Just the lock decorator to pass (no fixture) | `tmp_reciter_dir.seed_under_review(slug, hf_user_id)` |
| Load a JSON fixture by name | `load_fixture(name)` → reads `<name>.detailed.json` |
| Load an expected/baseline output | `load_expected(name, kind)` → reads `expected/<name>.<kind>.json` |
| Flask client without auth | `flask_client` |
| Inspect the FilesystemBackend root | `tmp_reciter_dir.backend` / `tmp_reciter_dir.data_dir` |

`tmp_reciter_dir.install(reciter, fixture)` does three things: seeds a state row (default `AWAITING_REVIEW`, or `UNDER_REVIEW` if `under_review_for` is given), writes `detailed.json`, and writes `edit_history.jsonl` if a sidecar fixture exists. It also rebuilds `segments.json` to match. (It no longer seeds `pipeline_meta.json`: validation stopped reading it when `basmala_amin` was retired.)

`signed_in_client` seeds the role into the SQLite substrate then mints a signed cookie via `auth_service.encode_session`. Returns `(client, {"hf_user_id", "login", "role"})`. The same fixture works for owner / maintainer / contributor by passing `role=`.

## Mocking boundaries

Pin every test against the real boundary it owns. Mocks that reach behind the boundary lose the regression signal.

| Boundary | Convention | Don't |
|---|---|---|
| BE bucket / data dir | `tmp_reciter_dir` or `state_persistence` (installs `FilesystemBackend`, sets `INSPECTOR_BACKEND=filesystem`) | patch `Path.read_text`, mock `hf_bucket` internals |
| BE env vars | `monkeypatch.setenv` | mutate `os.environ` directly at module load |
| BE HF OAuth / identity | `signed_in_client(role=...)` | mock `auth_service.encode_session` |
| BE external HTTP (QF, HF API) | monkeypatch the specific service function | mock `requests.get` |
| BE caches | rely on autouse teardown; add new caches to `_SEG_CACHE_NAMES` in `conftest.py` | mock cache primitives |
| BE audit | record-and-call-through (capture kwargs to a list, then invoke real `audit.append`) + assert against `repo_transitions.for_slug(slug)` | replace with `lambda **kw: None` (loses the durability check) |
| FE modules | real imports + props-driven state | `vi.mock(...)` for non-ambient modules |
| FE `fetch` | `vi.spyOn(window, 'fetch')` per-test | global mock that returns 200 `{}` for every URL |
| FE stores | real store + `.set()` in the test | mock the store |

The BE audit boundary is the most common foot-gun: stubbing `services.audit.append` to a no-op is tempting, but the test then cannot catch a regression that drops the call. Record-and-call-through:

```python
def _record_and_call_through(monkeypatch) -> list[dict]:
    from services import audit as audit_service
    calls: list[dict] = []
    real = audit_service.append

    def _spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(audit_service, "append", _spy)
    return calls
```

## Schema parity

When you edit anything under `qua_shared/schemas/`, regenerate the FE types and commit them in the same change:

```bash
python scripts/codegen/regen_fe_types.py
git add inspector/frontend/src/lib/types/generated/schemas.ts
```

CI's `schema-codegen-check` job runs the same script and fails the build via `git diff --exit-code` if `schemas.ts` is out of sync. Codegen sources `qua_shared.schemas.fe_types`, not `qua_shared.schemas` directly — a new FE-facing model has to be re-exported there too.

Persistence schemas (`DetailedSegment`, `EditHistoryBatch`, `EditOperation`, `PeaksRecord`, `AudioManifestSidecar`) MUST round-trip both directions:

- The offline extraction pipeline and Inspector save flow construct via Pydantic, not dict literals.
- The reader (`history_query.parse_edit_history_line`, `peaks_history`, `audio_meta`) parses via Pydantic.
- A round-trip test under `inspector/tests/persistence/test_<schema>_schema.py` pins the contract.

These bucket persistence models are pure `extra='forbid'` (no strip layer): an unknown/legacy field raises `ValidationError` rather than being silently dropped, so writer drift fails loudly. Don't loosen them to `extra='allow'` or add a `strip_and_warn` tolerance — the prod-data migration already rewrote on-disk artefacts to the canonical shape, so forbid is safe. `strip_and_warn` survives only on `TsShardDoc` (it strips unknown TOP-LEVEL keys with a WARNING); the shard's forward-compat surface is `_meta` itself, which is `extra="allow"` — see [shards.md](shards.md) §1.

See [`data-migrations.md`](data-migrations.md) for the writer/reader-drift rationale.

## Coverage

Coverage is measured on every CI run and emitted to the GitHub Step Summary, but no threshold gates the build.

**Backend.** `pyproject.toml` `[tool.coverage.run]` is `source=['.']`, `branch=false`, `skip_empty=true`. The pytest CI invocations add `--cov=services --cov=routes --cov=domain --cov=adapters --cov-report=term-missing --cov-report=xml:.coverage.xml`. `qua_shared` runs separately with `--cov=qua_shared --cov-report=xml:.coverage-qua-shared.xml`.

Local coverage:

```bash
cd inspector && python -m pytest --cov=services --cov=routes --cov=domain --cov=adapters \
  --cov-report=term-missing --cov-report=html tests/
open htmlcov/index.html
```

**Frontend.** `vitest.config.ts` uses the v8 provider with `coverage.all: true`, reporters `text` + `json` + `html`, and an explicit exclude list (`lib/types/generated/**`, `**/*.test.ts`, `**/*.spec.ts`, `**/__tests__/**`, `**/*.d.ts`, setup, config files). HTML lands under `inspector/frontend/coverage/`.

```bash
cd inspector/frontend && npm run test:coverage
open coverage/index.html
```

**Asymmetry to know about.** BE coverage is line-only (`branch=false`) for the baseline run; FE v8 reports branches by default. Headline percentages between the two are not directly comparable. Don't game coverage by writing assertion-free smoke tests — the audit will flag them as `dead-test`.

## Phase-gates

A `describe.skipIf(loadOptional(...))` block is acceptable **only** when introducing a test for a module that does not yet exist on `main` and will land in a follow-up commit on the same branch series. Pair with `it.todo('phase-N: <module> not yet present')`.

Remove the gate the moment the module lands. A phase-gate older than the PR that introduced it is a smell. When you touch `__tests__/command/` or `__tests__/normalized-state/`, audit for stale gates and delete them.

## Conventions

- **Test naming.** BE: `test_<unit>_<scenario>_<expectation>`. FE: `it('does X when Y', ...)` / `describe('<module>', ...)`.
- **Test location.** BE: mirror the prod tree (`services/state/state.py` → `tests/services/test_state_*.py`). FE: co-locate under `__tests__/` next to the module.
- **One conftest per major tree.** Promote duplicated seeders up the tree, don't fork them inline.
- **Subsystem map.** When you add a new subsystem, add a new pytest subdir + a row to [`docs/reference/README.md`](README.md).

## Commit + attribution

- **Format.** `prefix(scope): imperative description` plus 1–3 bullets. See `.claude/rules/commit.md`.
- **Scopes.** Tests roll up under the area they cover: `test(segs-be)`, `test(ts-fe)`, `test(global-fe)`, etc. — same areas as the production code they exercise.
- **No co-author attribution.** Never commit as Claude or add `Co-Authored-By: Claude`. The user is the author.

## See also

- [Subsystem index](README.md) — the per-subsystem reference docs (state machine, segments editor, validation, frontend, etc.) own the *what-is* for the code under test. Read the matching one before writing tests for a subsystem.
- [`data-migrations.md`](data-migrations.md) — Migration #5 records the writer/reader drift that motivates the schema round-trip policy.
- [`auth-permissions.md`](auth-permissions.md) — error envelope shape (`{error, code, context?}`), CSRF/Origin contract, predicate gates.
