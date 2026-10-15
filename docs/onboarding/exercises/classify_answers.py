"""How does the adaptive loop read a learner's answers?

PREDICT: The script grades four answers to the same question (correct answer
"b") and prints one line per learner: their choice, their confidence, and the
classification. Predict the four classifications, then the last line, which
lists the question ids the next guide version would retire.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from guidelib import grade_answers, parse_guide  # noqa: E402

GUIDE = """
<main data-guide data-mode="onboard" data-guide-id="demo" data-version="1">
  <section class="module" data-module="m1">
    <div data-q="q1" data-answer="b"><p class="prompt">Pick b.</p>
      <ol class="choices"><li data-choice="a">a</li><li data-choice="b">b</li></ol></div>
  </section>
</main>
"""

guide = parse_guide(GUIDE)
learners = {
    "ana": ("b", "sure"),
    "bo": ("b", "guess"),
    "cy": ("a", "sure"),
    "di": ("a", "unsure"),
}
retire = []
for name, (choice, confidence) in learners.items():
    payload = {"answers": {"q1": {"choice": choice, "confidence": confidence}}}
    graded = grade_answers(payload, guide)["q1"]
    print(f"{name:<4} chose {choice} ({confidence:<6}) -> {graded['classification']}")
    if graded["classification"] == "mastered":
        retire.append(f"{name}:q1")
print("retire:", ", ".join(retire) or "nothing")
