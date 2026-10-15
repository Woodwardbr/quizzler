---
description: Generate or continue an interactive onboarding guide for this repository
argument-hint: "[focus area, or a QUIZZLER-ONBOARD result code]"
---

You are running Quizzler's onboarding flow. Read `docs/onboarding/README.md` for the guide contract before you write any guide content.

Arguments: $ARGUMENTS

## Writing style (required)

Readers skim, so structure every module for skimming:

1. **Open with key points.** Start each module with a `<div class="key-points">` that has a `<h3>Key points</h3>` heading and 2–4 bullets.
2. **Prefer lists to paragraphs.** Use bullets for facts and numbered lists for steps or flows. Keep any remaining paragraph to one or two sentences.
3. **Draw a diagram for every flow.** Each module has at least one inline SVG diagram of the path, state machine or structure it explains. Use a table for any comparison.
4. **Split modules into short `<h3>` sub-sections.** Each covers one idea.
5. **Make code look like code.**
   - Wrap identifiers, paths and commands in `<code>`.
   - Show any code longer than one line as `<figure class="code-listing" data-src="path" data-lines="A-B"><pre>…</pre></figure>`, copied exactly. `check.py` verifies it against the file, and the caption gets a "Show in file" button.
   - Never put code in a paragraph.
6. **Anchor anything the reader might want to see.** The code pane is open by default on wide screens and follows the anchor the reader is looking at. Without anchors it has nothing to show.
7. **Don't hand-copy exercise code.** The engine shows it from the file as step 1 ("Read the code"). Put setup boilerplate above a `# --- exercise ---` line to hide it.
8. **Use asides sparingly.** `<p class="note">` holds a short aside and `<div class="warning">` holds a trap or gotcha.

## How a guide grows

Modules are written on demand, not all at once:

1. **Round 1:** write the overview, plus a *skeleton* of every module, then stop. This lets the reader see the whole course, click around, and set the intensity of each module before you spend effort on any of them.
2. **Later rounds:** write modules in study-plan order, at the depth the reader chose, one or two per round. Never write a skipped module beyond its skeleton.

Each module records how far it has been written, in `data-written="skeleton|skim|standard|deep"`. The engine compares that with the reader's chosen intensity. Where the chosen depth is greater, it shows a "not written yet" notice and includes the module under `pending` in the results.

## Listening for the reader

The page asks you for work through a queue. When the reader opens a module that isn't written to their chosen depth (Start, Next, the sidebar, or "Ask Claude to write it now"), the server queues a request.

1. Right after serving, run `python docs/onboarding/relay.py wait` **as a background command**. It exits when a request arrives, which wakes you.
2. When it exits, read its output (the requests, as text and JSON). Then:
   1. Ingest the autosaved results.
   2. Write what was asked, in plan order, at the requested depth.
   3. Validate the guide.
   4. Run `python docs/onboarding/relay.py done <ids>`.
3. Start `relay.py wait` in the background again **every time**, until the user says they are finished. The page reloads itself when the guide file changes, so do not ask the user to reload.

If the page reports that nobody answered, the user will run the command again and say "continue"; pick up from `relay.py list`.

## Continuing a guide

Continue when the arguments contain a `QUIZZLER-ONBOARD {...}` result code, when the user says "continue" (or similar), or when they come back after changing the plan.

1. Get the results:
   - If they pasted a code, save it under `docs/onboarding/local/` and run `python docs/onboarding/progress.py ingest - < that-file`.
   - Otherwise run `python docs/onboarding/progress.py ingest`, which reads the autosaved results.
2. Run `python docs/onboarding/progress.py report`. Then, if any questions were answered, run `python docs/onboarding/progress.py bump`.
3. **Write what the plan asks for.** The report's "Write next" list is in plan order. Take the first one or two entries and add the missing layers. Raise each module's `data-written` to the layer you finished. Leave skipped modules as skeletons.
   - **skim layer** (untagged): key points, the main diagram, 2 core questions.
   - **standard layer** (`data-depth="standard"`): walkthrough sub-sections, code listings, the main exercise.
   - **deep layer** (`data-depth="deep"`): edge cases, internals, failure modes, 1–2 harder questions.
   - **Lowering** an intensity needs no work; the engine hides the deeper layers.
4. **Adapt what is already written,** using the report:
   - **Misconceptions:** confront the wrong mental model directly. Explain why the tempting answer is wrong.
   - **Gaps:** teach the idea again in smaller steps, with a diagram or an exercise.
   - **Fragile answers:** keep the question, but frame it differently.
   - **Mastered questions** are already marked `data-retired`. Do not reword them.
   - **Missed exercise predictions:** walk through the output line by line.
   - Do not change the plan rows' recommended `data-intensity`; the reader's choices are saved separately.
5. Validate (step 6 below), mark any handled relay requests done, and tell the user in one line which modules are new or deeper. The page reloads on its own. Then re-arm `relay.py wait`.

## Starting a new guide

1. If `docs/onboarding/local/guide.html` exists, ask whether to continue it or start fresh. If the user only wants the shared tour, serve `docs/onboarding/guide.html` and skip to step 7.
2. Inventory the repository: its language and build/test setup, its major entry points (CLI, HTTP handlers, jobs, UI), and the main data and control paths through them. Read real code. Never describe code you have not opened.
3. Plan 3–6 modules. Organize them around paths through the system, not around directories. If the user gave a focus area, start there. For each module, decide:
   - **Importance** (low/medium/high): how much a newcomer needs it to work in the repo.
   - **Complexity** (low/medium/high): how hard it is to follow.
   - **Recommended intensity**: `skip`, `skim`, `standard` or `deep`. Base it on both ratings, so central and tricky modules get `deep` and peripheral ones get `skim` or `skip`.
4. Write `docs/onboarding/local/guide.html` with `data-mode="onboard"`, a unique `data-guide-id` and `data-version="1"`. Its asset links are `../app/guide.css` and `../app/guide.js`. Follow the writing style above.

   **The overview comes first.** It is `<section class="module" data-module="overview" data-overview data-title="Overview and study plan">`, and it is always fully written, with no questions or exercises. It contains:
   - What the repo does, in 2–4 key-point bullets: its purpose, users, stack and how it runs.
   - A diagram of the whole system: the major components and how data or control flows between them.
   - A "where things live" table mapping directories or files to responsibilities, with anchors on the key entry points.
   - The study plan table. Its header row is `<th>Module</th><th>What you will learn</th>`, and it has one row per later module: `<tr data-plan="<module id>" data-importance="…" data-complexity="…" data-intensity="…"><td>Title</td><td>One line</td></tr>`. The engine adds the rating badges and a dropdown the reader uses to change the intensity or skip the module.
   - A short list explaining the four intensities, and a note that modules are written after the reader sets the plan.

   **Every later module starts as a skeleton,** `<section class="module" data-module="…" data-title="…" data-written="skeleton">`. It contains no questions and no exercises, only:
   - The `<h2>` title.
   - A `key-points` box headed "What this module covers", with 2–4 bullets.
   - An "Outline" `<h3>` with a numbered list of the planned sub-sections, one line each. Mark which ones are standard-only or deep-only.
   - "Key code": a bullet list of the main anchors (`data-src` with `data-sym`/`data-ref`), so the code pane already has something to show.
5. Stop after round 1. Do not write any module bodies yet.
6. Validate:
   - `python docs/onboarding/check.py --guide docs/onboarding/local/guide.html --no-exercises`. Fix every error. Make ambiguous anchors longer.
   - `python docs/onboarding/check_layout.py --guide docs/onboarding/local/guide.html`. Fix any clipped or overlapping labels.
7. Start the server in the background with `python docs/onboarding/serve.py` (or `--guide <path>`). Give the user the printed URL. Then start `python docs/onboarding/relay.py wait` in the background (see "Listening for the reader"). In round 1, ask them to read the overview, browse the outlines, set an intensity for each module (or skip it), and press "Start the first module". That button asks you to write it, so they don't need to come back here. In later rounds, ask them to answer questions with honest confidence ratings.

### Writing a module body (later rounds)

- Replace the skeleton's outline with real sub-sections. Keep the key points, and update them if needed.
- Open with key points and a diagram. Use bullets and numbered steps, with short `<h3>` sub-sections.
- Anchor every claim about code with `data-src` plus `data-ref` (a unique literal), `data-sym` or `data-lines`. Show multi-line code as a checked `code-listing`.
- Questions test understanding of behaviour, not recall of names. Each needs an `.explain`.
- Exercises go in `docs/onboarding/local/exercises/<name>.py`. Keep them deterministic and CPU-only, and give each a `PREDICT:` docstring.
- Write only up to the reader's chosen depth, and set `data-written` to match. `check.py` rejects `data-depth` content deeper than `data-written`.

## Rules

- Everything you generate goes under `docs/onboarding/local/`, which Git ignores. Never commit learner answers, progress or generated guides.
- Do not edit `docs/onboarding/guide.html` (the tracked shared tour) unless the user asks for it. If you do edit it, run `python docs/onboarding/check.py` afterwards.
