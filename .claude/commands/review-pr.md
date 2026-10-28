---
description: Learn and review a pull request module by module with Quizzler (never posts without explicit permission)
argument-hint: "<PR number> [--diff-base merge-base|base-tip|<sha>]"
---

You are running Quizzler's PR review flow for: $ARGUMENTS

Read `quizzler/README.md` for the guide contract. All state goes in `quizzler/local/reviews/pr-<N>/` and the code under review is in `.claude/worktrees/pr-<N>/`. Both are ignored by Git.

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
9. **Name each actor once.** Pick one name for each thing in the flow (for example "query flight" or "past traffic") and use exactly that name in text, diagrams, questions and findings.
10. **One prediction per exercise step.** Keep an exercise to about 10 lines, with shared setup in a `_helper.py`.
11. **Keep comment drafts to 1–2 lines.** State the problem and the fix, and phrase it as a question when it is a judgement call. If the reviewer rewrites a draft, use their wording.
12. **Answer an "unsure" with a diagram.** When the reviewer marks a finding unsure, add a diagram to it instead of more prose.
13. **Anchor by literal text where you can.** Prefer `data-ref` and `data-sym` to `data-lines`, because line numbers drift. Use `data-lines` only for a listing or a diff-specific range.

## Hard rule: no GitHub writes without explicit permission

Never post, edit, approve, request changes, or comment on GitHub as part of this flow. Finishing a review, marking findings "ready", or generating a preview is **not** permission to post. You may post only if all of these hold:
1. The user has seen the exact output of `review.py preview N`.
2. `review.py status N` shows the review is not stale.
3. The user then gives a separate, unambiguous instruction to post, such as "post these comments".

Before you post, show the exact text, file, lines and commit SHA one more time, and check every comment for secrets and customer or other private data. Remove or ask about anything you find.

## Trust boundary

Exercises import the PR's code and run it on this machine with the server's environment. That is fine for a PR from a trusted author. For a PR from an unfamiliar contributor, read the code under test first and tell the user that exercises will run it. Do not run exercises that you have not read.

## Steps

1. **Prepare.** Run `python quizzler/review.py prepare <N>` with the user's `--diff-base` if they gave one. This fetches the PR with `gh`, creates or refreshes the worktree at the exact head SHA, and records the SHAs in `meta.json`. If a review already exists and the head moved, tell the user that the earlier results were archived.
2. **Understand the change before teaching it.** Work in the worktree, not the main checkout.
   - A small PR: read the whole diff yourself. A large one: send parallel agents through each subsystem. Ask them for `file:line` evidence, shapes and units, defaults, and suspected bugs labelled *confirmed* or *suspected*. Re-read whatever you will teach.
   - Work through these questions:
     - **Claims vs code:** what does the PR say it does, and what does the code do, one claim at a time?
     - **Data flow:** what are the shapes, units and types, and what happens with empty, padded or missing input?
     - **Fit:** who calls this, and does behaviour stay the same when the feature is off?
     - **Risk:** look for future-data leakage, train/eval mismatch, compatibility with existing stored data and caches, performance, and silent failures.
     - **Scope:** are there unrelated changes, and does the PR follow the repo's conventions (validators, config examples, type annotations, how secrets are passed)?
   - Ask the user which parts matter most to them, then propose the modules, for example request/data flow, persistence, model behaviour, and UI/API. By default go deep on the core logic and keep plumbing short. Use one module for a small PR.
   - Write the plan to `quizzler/local/reviews/pr-<N>/plan.md`.
3. **Write module 1 only.** Create `quizzler/local/reviews/pr-<N>/guide.html` with `data-mode="review"`, `data-guide-id="pr-<N>"`, `data-pr`, `data-head-sha` (from meta.json) and `data-version="1"`. Its asset links are `../../../app/guide.css` and `../../../app/guide.js`. The module includes:
   - Context, following the writing style above: key points, a before/after diagram of the changed flow, and bullets explaining what changed and why. Show changed code as a `code-listing` of the head lines.
   - Anchors into the head code (`data-src` + `data-ref`, `data-sym` or `data-lines`).
   - 2–3 questions that check whether the reviewer understands the behaviour change.
   - Exercises where useful, in `local/reviews/pr-<N>/exercises/`. Read any PR code before an exercise runs it.
   - Proposed findings inside `.findings`. Each needs a concrete failure scenario (the inputs or state that lead to the wrong behaviour), evidence anchored at the head SHA, the impact, and an editable `.comment-draft`. Say what is uncertain. Do not include style nits unless they matter. If you have no real findings, include none.
4. **Validate.** Run `python quizzler/check.py --review <N>` and `python quizzler/check_layout.py --guide quizzler/local/reviews/pr-<N>/guide.html`, and fix what they report. Do not hand the guide over until the layout check ends with `OK`, and look at a screenshot of the new module yourself.
5. **Serve.** Start `python quizzler/serve.py --review <N>` in the background and give the user the URL. Findings stay hidden until the module's questions are answered. The reviewer can agree, be unsure or disagree with each one, edit the comment drafts, and add their own line comments from the source pane.
6. **Listen.** Start `python quizzler/relay.py --review <N> wait` in the background. When the reviewer finishes the last written module, they press "Ask Claude for the next module", which queues a `next-module` request and wakes you. Handle it as in step 7, run `relay.py --review <N> done <ids>`, and start the wait again each time. The page reloads itself when the guide changes.
7. **Loop.** When a `next-module` request arrives or the user says the module is done, read `local/reviews/pr-<N>/results.json` (autosaved), or ingest the code they paste with `python quizzler/review.py ingest <N> -`. Then:
   - Classify the answers and adapt. Clear up misconceptions before moving on, and use `python quizzler/progress.py --guide quizzler/local/reviews/pr-<N>/guide.html report`.
   - Take the reviewer's disagreements and "unsure" votes seriously. Re-check the evidence, and withdraw or correct a finding when they are right.
   - Check each of the reviewer's own comments against the code and label it *confirmed*, *misunderstanding* or *question for the author*. Credit the reviewer for a finding they found.
   - Give each finding a severity in `data-severity`: `blocking`, `should-fix`, `nit` or `question`.
   - Append a dated entry to `quizzler/local/reviews/pr-<N>/notes.md` with the head SHA, the score, the misconceptions, and the triage results. Do not edit `progress.md`, because `progress.py` writes it.
   - Run `progress.py --guide … bump`, then append the next module. Do not change earlier modules except to correct them. Validate and tell the user to reload.
8. **Final review.** After the last module, point the user to the Review tab, where findings and comments are grouped as ready, needs another look and not posting. Then run `python quizzler/review.py preview <N>` and show its output, including any PROBLEM lines, for example comments outside the diff. Stop here.
9. **Posting** happens only under the hard rule above, and then only with `gh api` against the `commit_id` from the preview.

When the user is finished, offer `python quizzler/review.py cleanup <N>`. It is a dry run without `--yes`.
