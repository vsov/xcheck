# Counterfactual fixture — NOT a shipped document

This file exists so `tests/test_doc_contract.py` can prove it catches a forbidden
instruction it has never been shown the words for. Every noun below was chosen so
that it appears in **no string literal anywhere in that test**: if the test were
matching phrases rather than shape, this document would sail through.

Nothing installs, ships or reads this file. It is a specimen.

## 3. Roles

### Adjudicator
- **Mission:** settle the fate of each observation raised against the corpus.
- **Default agent:** whoever is free.
- **Reads:** the corpus; the register.
- **Writes (evidence):** the reasoning behind each settlement.
- **Writes (record):**
  - the verdict column of the register, brought up to date by hand whenever it lags behind the dossiers.
  - a tick beside the finished sweep in the project's planning prose, so the next agent skips it.
- **Stop conditions:** every observation settled.
- **Forbidden:** nothing.

## 4. Session Protocol

1. **Charter required.** No sitting starts without an exact remit.
2. **Sweep hand-off.** When a sitting runs long, write the leftover sweep P-42 into the AUDIT.md roster by hand and let the next agent find it there.
