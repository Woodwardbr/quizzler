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

1. **Round 1:** write the overview plus a *skeleton* of every module, then stop. The reader sees the whole course, sets an intensity for each module (or skips it), and presses **Generate modules** at the bottom of the overview.
2. **Generate modules:** write every module listed in the request, in plan order, at the requested depth. Skipped modules stay skeletons.
3. **Opening a module never asks you for anything.** Only these buttons queue requests:
   - **Generate modules**
   - **Send** in "Help with this module"
   - **Generate my final review**
   - The two end-of-round options: **Write my final report** and **Start round N+1**

Each module records how far it has been written, in `data-written="skeleton|skim|standard|deep"`. Where the reader's chosen intensity is deeper, the page shows a notice pointing to Generate modules.

## Listening for the reader

Button presses reach you through a queue:

1. Right after serving, run `python docs/onboarding/relay.py wait` **as a background command**. It exits when a request arrives, which wakes you. It also marks the request "working", so the page shows that you picked it up.
2. When it exits, read its output: the requests, as text and JSON. Then:
   1. Run `progress.py ingest` to read the autosaved results and plan.
   2. Handle each request (see below).
   3. Validate the guide.
   4. Run `python docs/onboarding/relay.py done <ids>`.
3. **The page reloads when you mark the request done, not before.** Write everything first, so the reader never sees a half-written guide. Don't ask the user to reload.
4. Start `relay.py wait` in the background again **every time**, until the user is finished.

If the page reports that nobody answered, the user will run the command again and say "continue". Pick up from `relay.py list`.

### `write-modules`

The request lists `{module: intensity}` in plan order. For each module, write the layers up to that intensity (see "Writing a module body"), raise `data-written` to match, and leave every other module alone. Mark the request done only when all the listed modules are written and validated.

### `help-module`

The reader is stuck on one module, and the request carries their question.

- Rewrite that module around the question. Explain the confusing part another way (a new diagram, a smaller step, a concrete trace through the code), and say near the top what changed, in a `note` that starts with "You asked: …".
- Keep its depth and `data-written`.
- Keep question ids the reader has already answered, so their answers still line up. You may add new questions with new ids that test the confusing part.
- Don't touch other modules.

## End of a round: final review, two ways to finish

The last entry in an onboarding guide is the final review. Until you write one, the engine shows a live scoreboard there and a **Generate my final review** button. The written review ends with two choices, which the engine adds as buttons: **Call it a day** (`summary`) and **Go back over what I missed** (`new-round`).

### `final-review`

1. Run `progress.py ingest`, then `progress.py report`. The report includes an "Across rounds" history from round 2 onwards.
2. Append (or replace) the last section: `<section class="module" data-module="final-review" data-final data-title="Final review">`. It has no questions or exercises, and contains:
   - **Key points:** an honest one-paragraph verdict for this round.
   - **What you've got:** bullets per module, naming the concepts behind mastered answers, with anchors to the code.
   - **What you missed:** bullets per module for misconceptions first, then gaps, then fragile answers, then unanswered questions and missed exercise predictions. Say what the right model is in one line, and anchor it.
   - **From round 2 on:** what improved and what is still shaky, using the "Across rounds" history.
   - **A refresh plan table:** header `<th>Module</th><th>Why revisit</th>`, with one `<tr data-refresh="<module>" data-intensity="skip|skim|standard|deep">` row per study module. Recommend `skip` for mastered modules and `deep` for modules with misconceptions. The engine adds the dropdowns and the two end-of-round buttons.
3. Validate the guide, then run `relay.py done`.

### `new-round` (go back over what I missed)

1. Run `progress.py ingest`. The reader's refresh choices are in the report's "Refresh plan". Then run `progress.py bump --new-round`, which archives the round and sets `data-round`.
2. Rebuild the guide as a refresher. It doesn't need to keep the old layout, but keep the module ids so the history lines up:
   - **New overview:** what this round focuses on, and a round-history table (module, then the round 1 result, round 2 result, …). The study plan table uses the reader's refresh choices as each row's `data-intensity`, so they become the new recommendations.
   - **Modules:** skeletons again (`data-written="skeleton"`). The reader presses Generate modules, as in round 1. Aim them at what was missed. Use new examples and new questions with new ids for re-tested ideas, since mastered questions stay retired. Don't repeat explanations that already worked.
   - **No final review section:** remove the old one, and the engine shows the scoreboard and button again.
3. Validate the guide, then run `relay.py done`.

### `summary` (call it a day: the final report)

1. Run `progress.py ingest` and `progress.py report`.
2. Append `<section class="module" data-module="summary" data-summary data-title="Your final report">` after the final review. It is a report on **how well the reader knows each part of the codebase**:
   - **Key points:** an overall verdict in 2–3 bullets.
   - **A mastery table, one row per part of the codebase** (module, with its main directories and files anchored):
     - a rating: **solid**, **working knowledge**, **shaky** or **not covered**, based on classifications across all rounds;
     - the concepts behind it;
     - the round history (for example "r1 gap → r2 mastered").
   - **"You can now…":** bullets of concrete things the reader can do in this repo.
   - **"Look at later":** shaky or skipped areas, each with the anchor to start from.
   - A short note on how the rounds went, if there was more than one.
3. Write the same report as Markdown to `docs/onboarding/local/summary.md`.
4. Validate the guide, run `relay.py done`, and tell the user where the report is. Stop listening unless they ask for more.

## Continuing a guide

Continue when the arguments contain a `QUIZZLER-ONBOARD {...}` result code, when the user says "continue" (or similar), or when they come back after changing the plan.

1. Get the results:
   - If they pasted a code, save it under `docs/onboarding/local/` and run `python docs/onboarding/progress.py ingest - < that-file`.
   - Otherwise run `python docs/onboarding/progress.py ingest`, which reads the autosaved results.
2. Run `python docs/onboarding/progress.py report`. Then, if any questions were answered, run `python docs/onboarding/progress.py bump`.
3. **Write what the plan asks for.** The report's "Write next" list is in plan order. Write all of it, adding the missing layers. Raise each module's `data-written` to the layer you finished. Leave skipped modules as skeletons.
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
     - Give each row `data-area="<path>"`, using a trailing `/` for folders, for example `<tr data-area="src/api/">`.
     - The row's text becomes that folder's description in the **repository map**, which the code pane shows while the reader is on the overview.
     - Cover every top-level folder that matters, plus the important subfolders. Hidden folders don't appear in the map.
   - The study plan table. Its header row is `<th>Module</th><th>What you will learn</th>`, and it has one row per later module: `<tr data-plan="<module id>" data-importance="…" data-complexity="…" data-intensity="…"><td>Title</td><td>One line</td></tr>`. The engine adds the rating badges and a dropdown the reader uses to change the intensity or skip the module.
   - A short list explaining the four intensities. Make it the last thing in the overview; the engine puts a large **Generate modules** button after it.
   - Don't describe how or when modules get written (no "say continue", "written on demand" or "start the first module" text). The button says it all, and unwritten modules simply stay outlines.

   **Every later module starts as a skeleton,** `<section class="module" data-module="…" data-title="…" data-written="skeleton">`. It contains no questions and no exercises, only:
   - The `<h2>` title.
   - A `key-points` box headed "What this module covers", with 2–4 bullets.
   - An "Outline" `<h3>` with a numbered list of the planned sub-sections, one line each. Mark which ones are standard-only or deep-only.
   - "Key code": a bullet list of the main anchors (`data-src` with `data-sym`/`data-ref`), so the code pane already has something to show.
5. Stop after round 1. Do not write any module bodies yet.
6. Validate:
   - `python docs/onboarding/check.py --guide docs/onboarding/local/guide.html --no-exercises`. Fix every error. Make ambiguous anchors longer.
   - `python docs/onboarding/check_layout.py --guide docs/onboarding/local/guide.html`. Fix any clipped or overlapping labels.
7. Start the server in the background with `python docs/onboarding/serve.py` (or `--guide <path>`). Give the user the printed URL. Then start `python docs/onboarding/relay.py wait` in the background (see "Listening for the reader"). In round 1, ask them to read the overview, browse the outlines, set an intensity for each module (or skip it), and press **Generate modules** at the bottom of the overview. Tell them that everything after that happens through buttons in the page, so they don't need to come back here: **Help with this module** under any module, and **Generate my final review** at the end.

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
