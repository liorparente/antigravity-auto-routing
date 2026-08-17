# 01 — Extract Dialogue Contracts & Verdict Parser (Slice 1)

**What to build:** Extract the Critic verdict parsing, quote verification, and atomic objection parsing into an independent, pure text-parsing module (`dialogue_contracts.py`). Re-export symbols from `advisory_consultation.py` for backward compatibility. Add unit tests verifying contract parsing and anti-rubber-stamping behavior in isolation.

**Blocked by:** None — can start immediately.

**Status:** completed

- [x] Extract `CriticVerdict`, `VerdictContractResult`, and related contract data structures into `dialogue_contracts.py`.
- [x] Move `_parse_critic_verdict`, `_verify_critic_quotes`, and `_extract_critic_objections` into `dialogue_contracts.py`.
- [x] Re-export all extracted symbols in `advisory_consultation.py` to maintain 100% backward compatibility.
- [x] Add direct unit tests for `dialogue_contracts.py` verifying quote verification, malformed verdict handling, and substantive critique detection.
- [x] Verify all existing tests in `test_routing.py` pass without regression.
