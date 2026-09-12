"""agent-eval-harness: a lightweight evaluation harness for LLM agents.

Four ideas, one small package:

* ``cases``   - a dynamic golden set stored as versioned YAML files
* ``scorers`` - deterministic, tool-level objective scorers (plus an optional LLM judge)
* ``compare`` - peer comparison of two agent versions on the same cases
* ``badcase`` - a feedback loop that turns failing production samples into golden cases
"""

__version__ = "0.1.0"
