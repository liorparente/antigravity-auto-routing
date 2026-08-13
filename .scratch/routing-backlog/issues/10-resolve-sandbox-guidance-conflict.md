# 10 — Resolve the contradictory sandbox guidance

**What to build:** One answer to "how do I fix the worker socket-initialisation error", not two.
The institutional-memory note asserts that setting a temporary-directory variable together with a git
lock variable fully resolves it. The protocol states the fix is to bypass the IDE sandbox on the tool
call, and explicitly says filesystem permission changes do not address socket isolation. An agent
reading the note applies a remedy the protocol says does not work.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] A reproduction is run: a worker invocation under the sandbox with those variables set. The
      commands and their output are recorded.
- [x] Whichever document is wrong is corrected. The protocol is the contract; the note defers to it.
- [x] If the variables help partially, the note states exactly what they fix and what they do not — a
      partial remedy documented as a complete one is the failure being corrected here.
- [x] An `ERRORS.md` entry records the outcome. This is the third documented resolution this month
      found not to match reality, and that pattern is worth logging as such.
