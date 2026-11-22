---
paths:
  - "inspector/frontend/src/**/*.test.ts"
  - "inspector/frontend/src/**/*.spec.ts"
  - "inspector/frontend/vitest.setup.ts"
---

# Tests — FE vitest

- `@testing-library/svelte` for components: `render`, `fireEvent`, `waitFor`. No raw DOM construction.
- Import `describe` / `it` / `expect` from `'vitest'` explicitly — CI's svelte-check type-checks test files and fails on bare globals.
- Import style: `import { describe, expect, it }` with spaces after commas (ESLint enforces).
- Fake audio elements: use `audio._fireEvent('canplay')` — the stub method from `makeAudioStub`. Not `audio._fire`, not `audio.fire`.
- Imperative scalars hidden behind setters (`_activeTab`, etc.) — reset via the setter (`setActiveTab(...)`), not the bare store `.set(...)`.
- `describe.skipIf(!module)` phase gates are temporary. When the gated module lands in main, convert the dynamic import to static and drop the gate. Same for any `loadOptional<X>()` call.

## Anti-patterns

- `audio._fire(...)` → method doesn't exist; use `_fireEvent`.
- `as any` cast on a typed command literal → use the discriminated-union member.
- `expect(getAudioGraph(el)).toBeNull()` on a suspended ctx → the graph IS created; assert `_ctxResume` was called instead.
- `loadOptional<X>('path')` for a module that ships in main → static `import { X } from '../path'`.
- `vi.mock` on a non-ambient internal module that ships and works → real imports + props-driven state.
