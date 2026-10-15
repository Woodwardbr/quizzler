# Quizzler guide engine

One engine for two entry points:

- **`/onboard`** teaches a repository through its entry points and data/control paths.
- **`/review-pr <N>`** teaches a pull request one module at a time, collects your responses to proposed findings and your own comments, and produces a SHA-bound preview. It never posts on its own.

Both produce static HTML guides that load the shared `app/guide.js` and `app/guide.css`. A small standard-library server shows them on loopback. There is no framework, no build step, and nothing to install beyond Python 3.10+ and Git. The PR flow also needs `gh`, and the layout gate needs Firefox.

## Layout

```text
docs/onboarding/
  README.md            this file
  app/guide.js         guide UI: modules, questions, exercises, source pane, Changes/Review views
  app/guide.css        responsive layout, light/dark themes, diagram styles
  app/layout_check.js  layout probe (loaded only with ?layoutcheck=1)
  guidelib.py          shared: path policy, anchors, symbols, guide parser, exercise runner, git diff
  serve.py             tokenized loopback server
  check.py             CI drift gate: anchors, symbols, guide contract, exercise outputs
  check_layout.py      headless layout gate (Firefox via Marionette)
  review.py            PR worktrees, diff bases, ingest, preview, cleanup
  progress.py          result codes -> progress records -> next guide version
  guide.html           shared onboarding guide (tracked)
  examples/            maintained authoring examples (tracked)
  exercises/ expected/ shared exercises and their recorded output (tracked)
  local/               ignored by Git: everything personal
    guide.html, progress.md, progress.json, results.json, exercises/
    results/<guide-id>.json      progress for tracked guides
    progress/<guide-id>/         progress records for tracked guides
    versions/                    archived versions of tracked guides
    reviews/pr-<N>/              meta.json, guide.html, results.json, progress.md, preview.json, exercises/
.claude/worktrees/pr-<N>/        ignored, detached checkout of the PR head
```

## Usage

```sh
# Shared onboarding guide (or local/guide.html if one has been generated)
python docs/onboarding/serve.py            # prints http://127.0.0.1:<port>/t/<token>/...

# PR review
python docs/onboarding/review.py prepare 42            # gh + git fetch, worktree, meta.json
python docs/onboarding/review.py prepare 7 --local-base main --local-head my-branch   # no GitHub
python docs/onboarding/check.py --review 42            # validate the generated guide against the worktree
python docs/onboarding/serve.py --review 42
python docs/onboarding/review.py ingest 42 -  < code.txt
python docs/onboarding/review.py preview 42            # exact text + targets; posts nothing
python docs/onboarding/review.py status 42             # has the PR moved since review?
python docs/onboarding/review.py cleanup 42 --yes      # remove worktree, refs and local state

# Adaptive loop for onboarding
python docs/onboarding/progress.py ingest 'QUIZZLER-ONBOARD {...}'
python docs/onboarding/progress.py report
python docs/onboarding/progress.py bump

# Gates (CI runs these)
python -m unittest discover -s tests
python docs/onboarding/check.py
python docs/onboarding/check_layout.py --require-browser
```

In the browser, `/` focuses the filter. Use the arrow keys to switch tabs, and Escape closes the source pane. Shift-click a line number in the source pane to select a range for a review comment.

## Guide document contract

A guide is an HTML file somewhere under `docs/onboarding/`. Its asset links are relative, so `app/guide.css` from `guide.html` and `../../../app/guide.css` from `local/reviews/pr-N/guide.html`. Inline `<script>` is not allowed because the server's CSP blocks it.

```html
<main data-guide data-mode="onboard|review" data-guide-id="pr-42" data-version="1"
      data-repo="owner/name" data-pr="42" data-head-sha="<40 hex>">   <!-- pr/head-sha: review only -->
  <header><h1>Title</h1><p class="lede">…</p></header>

  <section class="module" data-module="persistence" data-title="Persistence">
    <h2>Persistence</h2>

    <!-- literal anchor: text must appear exactly once (or add data-occurrence="2") -->
    <code data-src="src/db.py" data-ref="def save(self, row):">save</code>
    <!-- symbol anchor: function/class/constant must exist; Class.method allowed -->
    <code data-src="src/db.py" data-sym="Store.save">Store.save</code>
    <!-- line anchor -->
    <a data-src="src/db.py" data-lines="40-52">the retry loop</a>

    <figure class="diagram"><div class="diagram-scroll"><svg viewBox="0 0 640 200">…</svg></div>
      <figcaption>…</figcaption></figure>

    <div class="exercise" data-exercise="retry_backoff"></div>

    <div class="question" data-q="persist-1" data-answer="b">
      <p class="prompt">…</p>
      <ol class="choices"><li data-choice="a">…</li><li data-choice="b">…</li></ol>
      <div class="explain">Shown after answering.</div>
    </div>

    <div class="findings">   <!-- review mode; revealed after the module's questions are answered -->
      <article class="finding" data-finding="persist-race" data-severity="major"
               data-src="src/db.py" data-lines="44-47">
        <h3>Short title</h3>
        <p><strong>Scenario:</strong> concrete inputs/state that lead to the failure.</p>
        <p><strong>Evidence:</strong> anchors into the code at the reviewed SHA.</p>
        <p><strong>Impact:</strong> …  Say what is uncertain.</p>
        <div class="comment-draft">The comment text the reviewer can edit.</div>
      </article>
    </div>
  </section>
</main>
```

### Overview and study plan (onboarding)

An onboarding guide starts with an overview module. `check.py` enforces it. The overview contains:

- What the repo does.
- A diagram of the whole system.
- A table of where things live.
- A plan table with no quiz:

```html
<section class="module" data-module="overview" data-overview data-title="Overview and study plan">
  …
  <table>
    <thead><tr><th>Module</th><th>What you will learn</th></tr></thead>
    <tbody>
      <tr data-plan="persistence" data-importance="high" data-complexity="medium" data-intensity="deep">
        <td>Persistence</td><td>How rows are written and retried.</td></tr>
    </tbody>
  </table>
</section>
```

The engine adds importance and complexity badges and an intensity dropdown to each row. The dropdown offers **skip**, **skim**, **standard** and **deep**, with the author's choice marked "recommended".

- **Skip** removes the module from Previous/Next and from progress. The module stays reachable from the sidebar, which offers to include it again.
- **Content depth** follows the intensity. Untagged content is always shown. `data-depth="standard"` is hidden when skimming, and `data-depth="deep"` is shown only at deep. Questions in hidden content don't count.
- **Claude adapts:** the reader's plan goes into the result code and `progress.py report`, so the next version is written at that depth.

Modules are **written on demand**. `data-written="skeleton|skim|standard|deep"` on a module records how far it has been written. A missing attribute means fully written.

1. `/onboard` writes the overview and a skeleton of every module: what it covers, an outline, and the key code anchors. A skeleton has no questions or exercises.
2. The reader sets intensities and presses the large **Generate modules** button at the end of the overview. Claude writes every pending module in one go, in plan order and only to the chosen depth, and leaves skipped modules as skeletons. Opening a module never triggers writing.
3. Skeletons show as "outline" in the sidebar. A module whose chosen intensity is deeper than what has been written shows a "not written yet" notice.
4. The gap is exported as `pending` and listed by `progress.py report` under "Write next".
5. `check.py` rejects questions or exercises in skeletons, and `data-depth` content deeper than `data-written`.

### Buttons that reach Claude (relay)

The page cannot call Claude directly, so the server keeps a small request queue next to the results file under `local/`. Only these buttons queue requests:

| Button | Request | What Claude does |
|---|---|---|
| Generate modules (overview) | `write-modules` | Writes every pending module to its chosen depth |
| Help with this module → Send | `help-module` | Rewrites that one module around the reader's question |
| Generate my final review | `final-review` | Writes what was got, what was missed, and a refresh plan |
| Write my final report | `summary` | Final report: mastery per part of the codebase |
| Start round N+1 | `new-round` | A refresher round on what was missed |
| Ask Claude for the next module (review mode) | `next-module` | Appends the next review module |

The steps:

1. The Claude session runs `relay.py wait` as a background command. It exits as soon as a request is pending, which wakes the session, and it marks the request "working". While it waits, it also writes a heartbeat file.
2. The page shows a generating panel with a spinner on every affected section. The panel follows the request's status: sent, picked up, writing.
3. If the heartbeat is stale, the panel says no session is listening.
4. The session writes the content, runs `relay.py done <ids>`, and starts waiting again.
5. The page reloads **once, when the request is done**, so it never shows a half-written guide. If you're typing, it offers a reload button instead.

### Writing style

Guides are read by skimming, and the code pane follows the reader, so:

- Open each module with `<div class="key-points">` containing 2–4 bullets.
- Prefer bullets and numbered steps to paragraphs. Give every flow an SVG diagram. Use short `<h3>` sub-sections.
- Show multi-line code as `<figure class="code-listing" data-src="path" data-lines="A-B"><pre>…</pre></figure>`. `check.py` verifies that every line shown still exists in that range, in order. A line of `...` marks a gap. Exercise code is shown from the exercise file automatically; `# --- exercise ---` hides the setup above it.
- Use `<p class="note">` and `<div class="warning">` for short asides.
- On wide screens the source pane is open by default and follows the anchor nearest the top of the screen. The anchor it is showing is outlined. Close pauses following, and clicking any anchor resumes it.

Diagram classes are `box`, `box-accent`, `box-warn`, `edge`, `arrowhead`, `text.title` and `text.small`. They follow the theme. Wrap wide tables in `<div class="table-wrap">`. Wrap the SVG in `.diagram-scroll` so phones scroll the diagram sideways instead of shrinking its labels.

Each module's questions share one **Check answers** button at the end. Rating your confidence is optional; an unrated answer counts as "not sure" when it's classified.

Answers live in the page source (`data-answer`). The guides are for learning, not for exams.

### Exercises

An exercise is a deterministic, CPU-only Python script named `[a-z0-9_]+.py`. Its module docstring contains a `PREDICT:` section. Shared exercises live in `exercises/`, and their normalized output is recorded in `expected/<name>.out`, which `check.py` enforces. Exercises generated for one guide live in `exercises/` next to that guide, under `local/`. A guide can run only the exercises it names with `data-exercise`.

Keep exercises seeded and free of wall-clock time, network access, ambient files, and environment-dependent output. If you change an exercise's behaviour on purpose, re-record its output with `check.py --update-expected` and review the diff.

### Result codes

The **Export results** button produces `QUIZZLER-ONBOARD {json}` or `QUIZZLER-REVIEW {json}`. The server also autosaves the same JSON under `local/`, so pasting is optional. `review.py preview` emits `QUIZZLER-PREVIEW {json}`, which is the exact set of comments, each with its path, line range, `commit_id` and text.

Each answer is classified as **misconception** (wrong and sure), **gap** (wrong, not sure), **fragile** (right, not sure) or **mastered** (right and sure). `progress.py bump` archives the current version, increments `data-version`, and marks mastered questions `data-retired`. Claude writes the new explanations from `progress.py report`.

## Security model

Threat boundaries, in order of strength:

| Boundary | Protects against | Mechanism |
|---|---|---|
| Loopback bind | other machines | `127.0.0.1` only; there is no host option |
| Host header check | DNS rebinding from web pages | only `127.0.0.1:<port>` / `localhost:<port>` |
| Per-run token | other local users and web pages guessing URLs | `secrets.token_urlsafe(32)` in the path, constant-time compare, never logged or written to disk |
| CSP and headers | injected script, framing, referrer leaks | `script-src 'self'`, `frame-ancestors 'none'`, `no-referrer`, `nosniff`, `no-store` |
| POST checks | cross-site writes | JSON content type, same-origin `Origin`, 1 MB cap |
| Path policy | reading secrets and private state | repo-relative paths only; no `..` or hidden components (so no `.git`); no secret-like names (`env.storage.*` with any extension, `*.pem`, `*.key`, `id_rsa*`, `*secret*`, `*credential*`, `*.env`, ...); no `docs/onboarding/local/`; a source-extension allowlist; resolved symlinks re-checked; 2 MB cap; 400 lines per request |
| Exercise registry | arbitrary code from the browser | the browser sends only a name, which must be registered by the active guide |

**What exercise execution does not do.** Exercises run in a child Python process with a 10 s wall-clock timeout, CPU, memory and output-size rlimits (POSIX), a scrubbed environment, a throwaway working directory, no stdin, and one run at a time. This is *not a sandbox*. An exercise can still read files the user can read, and it can still reach the network. That is acceptable only because exercises are repository code that someone reviewed, or code Claude generated in your session that you can read. Never register an exercise from an untrusted PR without reading it first. If you need to run untrusted code, run the server inside a container or VM rather than widening what it runs.

The token and the path policy stop casual and cross-site access. They do not protect against malware already running as your user.

## Privacy

- Learner answers, guide drafts, review state, previews and PR details stay under `docs/onboarding/local/` and `.claude/worktrees/`. Both are in `.gitignore`. If `.claude/worktrees/` is not ignored in a target repository, `review.py` adds it to `.git/info/exclude`, which is never committed.
- The server logs no request lines, because paths contain the token.
- CI never reads `local/`.

## Cleanup and recovery

- `review.py list` shows prepared reviews and whether each worktree still exists.
- `review.py cleanup N` is a dry run. `--yes` removes the worktree, the `refs/quizzler/pr-N/*` refs and `local/reviews/pr-N/`. Add `--keep-state` to keep your notes.
- If a worktree directory was deleted by hand, run `git worktree prune` and then `review.py prepare N` again.
- If a PR moved during review, `review.py status N` reports it as stale, and the guide shows a banner. Running `prepare` again checks out the new head, archives the old `results.json` as `results.<sha>.json`, and records the previous head in `meta.json`. Then run `check.py --review N` to find anchors that need refreshing.
- To reset onboarding progress, delete `docs/onboarding/local/` (or the relevant `progress/` and `results/` entries). None of it is tracked.

## Portability

GitHub access is isolated in `review.GitHubAdapter`. `LocalAdapter` reviews any two local refs with the same UI. To adopt the engine in another repository, copy `docs/onboarding/` (without `local/`), the two `.claude/commands/` files and the two `.gitignore` lines. Then replace `guide.html` with that repository's onboarding guide.
