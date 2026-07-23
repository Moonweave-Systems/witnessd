---
name: orro-trace-method
mode: trace
triggers: trace, debug, bug, failure, symptom, root cause, regression
boundary: advisory-only
---

# Migration note

Use `orro advise "<goal>" --mode trace` for an explicit trace route. The
default `orro advise` router selects this path for symptom-shaped goals and
preserves the existing trace artifact and provenance contract. `orro trace`
remains a deprecated alias for one release.
