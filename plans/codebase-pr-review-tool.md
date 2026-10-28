# Codebase and PR Review Tool Plan

## Goal

Build one review tool with two entry points: `/onboard` for learning a repository and `/review-pr <N>` for understanding and reviewing a pull request. Both use the same guide engine under `docs/onboarding/`. The tool should make the repository and proposed changes understandable in small, interactive steps, collect learner feedback, and keep review comments under the user's control.

## Product principles

- Use a static HTML guide and shared browser assets; avoid a framework and build pipeline.
- Keep the local server small, standard-library-only, and bound to `127.0.0.1` with a fresh per-run access token.
- Expose only the files needed by the guide. Never expose dotfiles, credentials, or `env.storage.*` files.
- Make guide claims traceable to source through literal text anchors and clickable symbol links.
- Split large reviews into coherent modules and complete each module's learning and review loop before moving on.
- Treat learner responses as evidence for adapting the next guide version, not as a score to optimize.
- Never publish a PR comment without explicit user authorization.
- Keep user-specific guides, progress, exercises, and review state out of version control.

## Proposed layout

```text
docs/onboarding/
  README.md                 # authoring and usage guide
  app/guide.js              # guide navigation, quiz, code pane, findings
  app/guide.css             # responsive layout and diagrams
  serve.py                  # tokenized loopback server and exercise runner
  guide.html                # shared onboarding guide, if project-specific
  exercises/
  expected/
  local/                    # gitignored user and PR artifacts
    guide.html
    progress.md
    exercises/
    reviews/pr-<N>/
      guide.html
      progress.md
      exercises/
      results.json
```

Generated review guides and all learner-specific state belong below `local/`.

## Architecture

### Shared guide engine

Define a small guide document contract shared by onboarding and review mode. Each guide contains metadata (mode, version, repository/PR identity), ordered modules, prose, inline SVG diagrams, source anchors, questions, exercise references, and optional review findings. Keep content as static HTML with narrowly scoped data attributes so the server can validate anchors and the UI can remain dependency-free.

The browser UI provides a filterable source outline, keyboard search (`/`), clickable symbol references, progressive module navigation, confidence-rated questions, exercise prediction/run controls, and progress export as a compact JSON results code. Review mode adds a docked code pane at wide viewports, a Changes view with diff markings against a selected base, and finding cards with agree/unsure/disagree choices plus editable comment drafts. At narrower sizes, the source pane becomes an accessible drawer or tab.

### Local server and source inspection

`serve.py` should:

1. Start only on `127.0.0.1`, choose a fresh random token per process, and require it on every route other than a minimal health check.
2. Resolve all paths against an explicitly configured repository root, reject traversal and symlink escapes, and allowlist source/config extensions and paths.
3. Deny hidden path components, `.git`, secret/config storage, generated local state except the active guide, and `env.storage.*` regardless of extension.
4. Render the selected guide and serve bounded source snippets for the viewer.
5. Run only exercises registered for the active guide, in-process with time/output limits and a restricted environment; do not accept arbitrary source code from browser requests.
6. Shut down cleanly and avoid writing tokens or learner data into tracked files.

Document the limits of in-process exercise execution. If repository exercises cannot be made safe under these constraints, run them in a separately constrained subprocess rather than broadening the server's execution surface.

### Exercises and drift protection

Exercises are deterministic, seeded, CPU-only scripts with a `PREDICT` docstring and recorded outputs. CI executes each exercise and compares normalized output with `expected/`. The same test suite checks every `data-src`/`data-ref` literal anchor against its current file and validates that symbols referenced by the guide still exist. Keep fixture inputs small and remove nondeterminism such as wall-clock time, network access, and ambient filesystem dependencies.

### PR review workflow

1. Resolve the requested PR number and fetch metadata, base/head SHAs, and diff using the repository's configured GitHub tooling.
2. Create or refresh a dedicated ignored worktree at `.claude/worktrees/pr-<N>/`; record exact SHAs and the chosen `--diff-base` in local review metadata.
3. Inspect the change and surrounding call paths, then divide it into understandable modules (for example, request/data flow, persistence, model behavior, UI/API).
4. Generate one module at a time. Each module includes context, diagrams, source and diff anchors, learning questions, exercises where useful, and proposed findings with evidence.
5. Serve the module guide and collect its results before generating/revealing the next module's findings. Classify answers as misconception (wrong + sure), gap (wrong + guessed), fragile (right + unsure), or mastered (right + sure); use classifications to adapt explanations and retire already graded questions in later versions.
6. Let the reviewer record independent comments as well as respond to proposed findings. Preserve confidence and reasoning with each response.
7. Aggregate findings in a final review view with ready, needs-another-look, and not-posting states. Preview the exact file and line range against the recorded head SHA and assemble one `QUIZZLER-PREVIEW {json}` payload.
8. Posting is a separate explicit action. Before any GitHub write, show the exact comment text and target, then require an unambiguous user instruction to post. Never infer posting permission from completing the review.

### Onboarding workflow

`/onboard` inventories the repository, identifies major entry points and data/control paths, and generates or opens a guide organized around those paths. Users read a module, predict exercise output, run the exercise, and answer a confidence-rated quiz. An `QUIZZLER-ONBOARD {json}` result updates local progress and informs the next guide revision.

## Implementation phases

### Phase 0: Repository and integration discovery

- Confirm the actual repository root, existing onboarding/review commands, CI framework, GitHub integration, and current conventions for ignored worktrees.
- Map the language/build/test setup and identify safe source allowlists and representative modules.
- Decide whether the implementation belongs in the existing product or in an isolated prototype, and replace reference-specific naming with project terminology.
- Write down threat boundaries for browser access and exercise execution before enabling either.

**Exit condition:** agreed file layout, command integration points, supported source types, and security boundaries.

### Phase 1: Static guide prototype

- Add the guide contract, a representative onboarding guide, shared JS/CSS, responsive source outline, inline diagrams, keyboard filtering, and literal source links.
- Add a fixture repository or use a small stable part of the actual codebase to prove the anchors work.
- Keep progress in browser memory initially; verify keyboard navigation and basic accessibility.

**Exit condition:** one complete onboarding path is readable and navigable without a framework or build step.

### Phase 2: Local server and guided exercises

- Implement tokenized loopback serving, path validation, source snippet routes, and registered exercise execution.
- Add deterministic exercise fixtures, expected output files, result-code export, and local state ignore rules.
- Add CI checks for output drift and stale anchors.

**Exit condition:** CI detects deliberately changed expected output and broken anchors; server refuses traversal and secret-file requests.

### Phase 3: PR review mode

- Add PR metadata/diff ingestion, ignored worktree management, module generation, code pane, Changes view, and finding response cards.
- Add SHA-bound previews and final aggregation across modules.
- Keep GitHub comment publication out of the first delivery if no explicit user-triggered posting path exists; implement it only behind a separate confirmed action.

**Exit condition:** a sample PR can be reviewed module by module and yields a verifiable preview without posting anything.

### Phase 4: Adaptive loop and layout gate

- Parse onboarding and review result codes into structured local records.
- Implement response classification, guide versioning, question retirement, and targeted next-module content.
- Add `check_layout.py` using a headless browser to detect clipped/overlapping diagram labels, broken tables, and unusable narrow-screen layouts.

**Exit condition:** a completed loop produces an updated guide version based on answers, and the layout gate passes the maintained examples.

### Phase 5: Rollout and maintenance

- Document `/onboard` and `/review-pr <N>`, authoring conventions, privacy boundaries, and cleanup/recovery for stale worktrees.
- Add CI coverage and a small set of representative fixtures for onboarding, no-diff PRs, binary-heavy diffs, renamed files, and stale anchors.
- Trial the flow on one internal PR and one new contributor onboarding session; refine module size and pacing from observed feedback.

**Exit condition:** a maintainer can run both entry points from a clean checkout and recover or delete local state without affecting tracked files.

## Key decisions and risks

- **Review guide generation:** use the coding agent to author and adapt content from inspected source and diff; the server should render and validate content, not autonomously make correctness claims.
- **Finding quality:** require each proposal to include a concrete failure scenario, evidence anchored to the reviewed SHA, and impact. Surface uncertainty where evidence is incomplete.
- **Stale references:** literal anchors can match repeated text or nearby but semantically wrong code. Add path plus symbol context where possible and validate uniqueness or report ambiguity.
- **Execution safety:** a loopback token and path filtering protect against casual access but do not sandbox Python execution. Restrict exercises and isolate execution where untrusted code is possible.
- **Diff changes:** findings must be tied to the reviewed head SHA. If the branch changes during review, mark the review stale and refresh the worktree and anchors.
- **Privacy:** do not put learner answers, guide drafts, or private PR details in tracked files, logs, telemetry, or CI artifacts.
- **Portability:** GitHub-specific PR ingestion should sit behind a small adapter so local branch/diff review can be added later without changing guide UI.

## Initial acceptance criteria

- Both entry points use the same guide renderer and shared assets.
- The server binds only to loopback, uses an unguessable per-run token, and rejects path traversal, dotfiles, credentials, and `env.storage.*` requests.
- Guides navigate by source anchors and remain usable on desktop and narrow screens.
- Exercises are deterministic and CI catches expected-output drift and stale source anchors.
- Review mode shows diff context, supports module-by-module learning and finding responses, and produces a SHA-bound final preview.
- All generated personal state and worktrees are ignored by Git.
- No PR comments are sent without a separate explicit user instruction.
