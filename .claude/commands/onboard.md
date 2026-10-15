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

## If the arguments contain a `QUIZZLER-ONBOARD {...}` result code

1. Save it to a file under `docs/onboarding/local/` and run `python docs/onboarding/progress.py ingest - < that-file`. If the server autosaved results, read `docs/onboarding/local/results.json` instead (or `local/results/<guide-id>.json` for the shared guide).
2. Run `python docs/onboarding/progress.py report` and then `python docs/onboarding/progress.py bump`.
3. Revise `docs/onboarding/local/guide.html` using the report:
   - **Misconceptions:** confront the wrong mental model directly. Explain why the tempting answer is wrong.
   - **Gaps:** teach the idea again in smaller steps, with a diagram or an exercise.
   - **Fragile answers:** keep the question, but frame it differently.
   - **Mastered questions** are already marked `data-retired`. Do not reword them.
   - **Missed exercise predictions:** walk through the output line by line.
   - **The reader's study plan** (shown in the report): put effort where they chose `deep`, keep `skim` modules lean, and do not expand modules they skipped. Leave the plan rows' recommended `data-intensity` as they are; the reader's choices are saved separately.
4. Validate the guide and tell the user what changed (step 5 under "Otherwise").

## Otherwise

1. If `docs/onboarding/local/guide.html` exists, ask whether to continue it or start fresh. If the user only wants the shared tour, serve `docs/onboarding/guide.html` and skip to step 6.
2. Inventory the repository: its language and build/test setup, its major entry points (CLI, HTTP handlers, jobs, UI), and the main data and control paths through them. Read real code. Never describe code you have not opened.
3. Plan 3–6 modules. Organize them around paths through the system, not around directories. If the user gave a focus area, start there. For each module, decide:
   - **Importance** (low/medium/high): how much a newcomer needs it to work in the repo.
   - **Complexity** (low/medium/high): how hard it is to follow.
   - **Recommended intensity**: `skip`, `skim`, `standard` or `deep`. Base it on both ratings, so central and tricky modules get `deep` and peripheral ones get `skim` or `skip`.
4. Write `docs/onboarding/local/guide.html` with `data-mode="onboard"`, a unique `data-guide-id` and `data-version="1"`. Its asset links are `../app/guide.css` and `../app/guide.js`. Follow the writing style above.

   **The first module is the overview.** It is `<section class="module" data-module="overview" data-overview data-title="Overview and study plan">`, and it has no questions or exercises. It contains:
   - What the repo does, in 2–4 key-point bullets: its purpose, users, stack and how it runs.
   - A diagram of the whole system: the major components and how data or control flows between them.
   - A "where things live" table mapping directories or files to responsibilities, with anchors on the key entry points.
   - The study plan table. Its header row is `<th>Module</th><th>What you will learn</th>`, and it has one row per later module: `<tr data-plan="<module id>" data-importance="…" data-complexity="…" data-intensity="…"><td>Title</td><td>One line</td></tr>`. The engine adds the rating badges and a dropdown the reader uses to change the intensity or skip the module.
   - A short list explaining the four intensities.

   In each later module:
   - Write for the full range of intensities, and tag content with `data-depth`. Untagged content is the skim layer: key points, the main diagram, the core questions. `data-depth="standard"` adds walkthroughs, listings and the main exercise. `data-depth="deep"` adds edge cases, internals and extra questions. Skim hides both tagged layers, and standard hides `deep`.
   - Open with key points and a diagram.
   - Use bullets and numbered steps, with short `<h3>` sub-sections.
   - Anchor every claim about code with `data-src` plus `data-ref` (a unique literal), `data-sym` or `data-lines`. Show multi-line code as a checked `code-listing`.
   - Write 2–3 multiple-choice questions that test understanding of behaviour, not recall of names. Each needs an `.explain`.
   - Add an exercise where predicting output teaches something. Put it in `docs/onboarding/local/exercises/<name>.py`, keep it deterministic and CPU-only, and give it a `PREDICT:` docstring.
5. Validate:
   - `python docs/onboarding/check.py --guide docs/onboarding/local/guide.html --no-exercises`. Fix every error. Make ambiguous anchors longer.
   - `python docs/onboarding/check_layout.py --guide docs/onboarding/local/guide.html`. Fix any clipped or overlapping labels.
6. Start the server in the background with `python docs/onboarding/serve.py` (or `--guide <path>`). Give the user the printed URL. Tell them to answer each question with an honest confidence rating, and that their progress autosaves locally. When they are done they can say so, or paste the exported result code.

## Rules

- Everything you generate goes under `docs/onboarding/local/`, which Git ignores. Never commit learner answers, progress or generated guides.
- Do not edit `docs/onboarding/guide.html` (the tracked shared tour) unless the user asks for it. If you do edit it, run `python docs/onboarding/check.py` afterwards.
