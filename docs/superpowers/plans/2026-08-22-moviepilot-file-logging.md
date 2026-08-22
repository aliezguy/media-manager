# MoviePilot File Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every custom log emitted by `backend/services/mp_service.py` to the project's rotating `backend/logs/app.log` while retaining console output.

**Architecture:** Replace the Uvicorn-owned logger with the module logger `services.mp_service`. The module logger has no local handlers and propagates to the root logger, whose existing console and `RotatingFileHandler` configuration performs both outputs without changing individual log statements.

**Tech Stack:** Python 3.13, standard-library `logging`, pytest, FastAPI/Uvicorn existing logging configuration.

## Global Constraints

- Keep the existing 10 MB × 5 backup rotation policy unchanged.
- Keep `_sanitize_response_body` redaction and the 4096-character response limit unchanged.
- Do not change wash result evaluation, lookup behavior, database schema, API contracts, or logging behavior outside `mp_service.py`.
- Do not make a real MoviePilot request during implementation or verification.
- Implement directly on `main`, as previously authorized by the user.

---

### Task 1: Route MoviePilot service logs through the root file handler

**Files:**
- Modify: `backend/tests/test_mp_wash_subscription.py`
- Modify: `backend/services/mp_service.py:16`

**Interfaces:**
- Consumes: the root logger handlers configured in `backend/main.py`; the existing module-level `logger` used by all MoviePilot service log calls.
- Produces: `logger: logging.Logger` named `services.mp_service`, with normal propagation to the root logger.

- [ ] **Step 1: Write the failing file-persistence test**

Add `import logging` to `backend/tests/test_mp_wash_subscription.py`, then add this test near the existing diagnostics tests:

```python
def test_moviepilot_logger_propagates_to_root_file_handler(tmp_path):
    assert mp.logger.name == mp.__name__
    assert mp.logger.propagate is True

    log_path = tmp_path / "app.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    root_logger = logging.getLogger()
    old_root_level = root_logger.level
    old_mp_level = mp.logger.level
    try:
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
        mp.logger.setLevel(logging.INFO)
        mp.logger.info("moviepilot-file-log-probe")
        handler.flush()
    finally:
        root_logger.removeHandler(handler)
        handler.close()
        root_logger.setLevel(old_root_level)
        mp.logger.setLevel(old_mp_level)

    assert "moviepilot-file-log-probe" in log_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
backend/venv/bin/python -m pytest \
  backend/tests/test_mp_wash_subscription.py::test_moviepilot_logger_propagates_to_root_file_handler -v
```

Expected: FAIL at `assert mp.logger.name == mp.__name__`, showing the current logger is named `uvicorn` rather than `services.mp_service`.

- [ ] **Step 3: Make the minimal production change**

In `backend/services/mp_service.py`, replace:

```python
logger = logging.getLogger("uvicorn")
```

with:

```python
logger = logging.getLogger(__name__)
```

Do not modify the existing log calls, response sanitization, or logging configuration.

- [ ] **Step 4: Run the new test and verify GREEN**

Run:

```bash
backend/venv/bin/python -m pytest \
  backend/tests/test_mp_wash_subscription.py::test_moviepilot_logger_propagates_to_root_file_handler -v
```

Expected: PASS and the temporary `app.log` contains `moviepilot-file-log-probe`.

- [ ] **Step 5: Run the MoviePilot wash regression suite**

Run:

```bash
backend/venv/bin/python -m pytest backend/tests/test_mp_wash_subscription.py -v
```

Expected: all tests pass, including response redaction, truncation, explicit success, uncertain-result lookup, database diagnostics, and file logging.

- [ ] **Step 6: Run full backend verification**

Run:

```bash
backend/venv/bin/python -m pytest backend/tests -q
backend/venv/bin/python -m py_compile \
  backend/services/mp_service.py \
  backend/tests/test_mp_wash_subscription.py
git diff --check
git status --short
```

Expected: pytest exits successfully with no failures; syntax checks and `git diff --check` exit 0; Git status lists only the two intended source/test files before commit.

- [ ] **Step 7: Commit the verified change**

```bash
git add backend/services/mp_service.py backend/tests/test_mp_wash_subscription.py
git commit -m "fix: 持久化 MoviePilot 服务日志"
```

After commit, run `git status --short` and expect no output.
