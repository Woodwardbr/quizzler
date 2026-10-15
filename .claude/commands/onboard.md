---
description: Generate or continue an interactive onboarding guide for this repository
argument-hint: "[focus area, or a QUIZZLER-ONBOARD result code]"
---

You are running Quizzler's onboarding flow. Read `docs/onboarding/README.md` for the guide contract before you write any guide content.

Arguments: $ARGUMENTS

## If the arguments contain a `QUIZZLER-ONBOARD {...}` result code

1. Save it to a file under `docs/onboarding/local/` and run `python docs/onboarding/progress.py ingest - < that-file`. If the server autosaved results, read `docs/onboarding/local/results.json` instead (or `local/results/<guide-id>.json` for the shared guide).
2. Run `python docs/onboarding/progress.py report` and then `python docs/onboarding/progress.py bump`.
3. Revise `docs/onboarding/local/guide.html` using the report:
   - **Misconceptions:** confront the wrong mental model directly. Explain why the tempting answer is wrong.
   - **Gaps:** teach the idea again in smaller steps, with a diagram or an exercise.
   - **Fragile answers:** keep the question, but frame it differently.
   - **Mastered questions** are already marked `data-retired`. Do not reword them.
   - **Missed exercise predictions:** walk through the output line by line.
4. Validate the guide and tell the user what changed (step 5 under "Otherwise").

## Otherwise

1. If `docs/onboarding/local/guide.html` exists, ask whether to continue it or start fresh. If the user only wants the shared tour, serve `docs/onboarding/guide.html` and skip to step 6.
2. Inventory the repository: its language and build/test setup, its major entry points (CLI, HTTP handlers, jobs, UI), and the main data and control paths through them. Read real code. Never describe code you have not opened.
3. Plan 3–6 modules. Organize them around paths through the system, not around directories. If the user gave a focus area, start there.
4. Write `docs/onboarding/local/guide.html` with `data-mode="onboard"`, a unique `data-guide-id` and `data-version="1"`. Its asset links are `../app/guide.css` and `../app/guide.js`. In each module:
   - Write short prose that states one idea at a time.
   - Add an inline SVG diagram wherever a flow or structure is easier to see than to read about.
   - Anchor every claim about code with `data-src` plus `data-ref` (a unique literal), `data-sym` or `data-lines`.
   - Write 2–3 multiple-choice questions that test understanding of behaviour, not recall of names. Each needs an `.explain`.
   - Add an exercise where predicting output teaches something. Put it in `docs/onboarding/local/exercises/<name>.py`, keep it deterministic and CPU-only, and give it a `PREDICT:` docstring.
5. Validate:
   - `python docs/onboarding/check.py --guide docs/onboarding/local/guide.html --no-exercises`. Fix every error. Make ambiguous anchors longer.
   - `python docs/onboarding/check_layout.py --guide docs/onboarding/local/guide.html`. Fix any clipped or overlapping labels.
6. Start the server in the background with `python docs/onboarding/serve.py` (or `--guide <path>`). Give the user the printed URL. Tell them to answer each question with an honest confidence rating, and that their progress autosaves locally. When they are done they can say so, or paste the exported result code.

## Rules

- Everything you generate goes under `docs/onboarding/local/`, which Git ignores. Never commit learner answers, progress or generated guides.
- Do not edit `docs/onboarding/guide.html` (the tracked shared tour) unless the user asks for it. If you do edit it, run `python docs/onboarding/check.py` afterwards.
