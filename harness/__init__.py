"""agent-eval-harness: a lightweight evaluation harness for LLM agents.

Five rules, one small package:

* objective before open-ended  - ``cases`` tiers + ``runner`` gates
* one owner writes, one peer reviews - answer blocks with a review record + ``lint``
* judges must be calibrated     - versioned ``judge`` prompts with three rules + ``audit``
* versions form a matrix        - set x agent x judge versions, ``compare`` / ``matrix``
* bad cases get a category      - ``badcase`` categories, rewrite-on-ambiguity, changelog

Get the questions and answers right first, then evaluate the agent.
"""

__version__ = "0.3.0"
