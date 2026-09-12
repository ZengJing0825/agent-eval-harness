"""agent-eval-harness: a lightweight evaluation harness for LLM agents.

Five rules, one small package:

* objective before open-ended  - ``cases`` tiers + ``runner`` gates
* two people write every answer - owner/peer answer blocks + ``lint``
* judges must be calibrated     - versioned ``judge`` prompts + ``audit``
* versions form a matrix        - set x agent x judge versions, ``compare`` / ``matrix``
* bad cases get a category      - ``badcase`` categories, rewrite-on-ambiguity, changelog
"""

__version__ = "0.2.0"
