# The Quizzler

Quizzler is an interactive guide engine for learning a codebase and reviewing pull requests with Claude Code.

- **`/onboard`** builds a guide around the repository's entry points and data paths. Each module has diagrams, anchors into the source, exercises where you predict the output, and questions with a confidence rating. Your answers shape the next version of the guide.
- **`/review-pr <N>`** teaches a PR one module at a time. Proposed findings stay hidden until you have formed your own view. You then agree, disagree or edit them, add your own comments, and get a preview bound to the reviewed commit SHA. Nothing is posted without a separate, explicit instruction.

Everything runs locally with the Python standard library and Git. Generated guides and learner data stay in ignored directories.

```sh
python docs/onboarding/serve.py      # take the tour of Quizzler itself
```

See [docs/onboarding/README.md](docs/onboarding/README.md) for usage, the guide contract and the security model. The original design is in [plans/codebase-pr-review-tool.md](plans/codebase-pr-review-tool.md).
