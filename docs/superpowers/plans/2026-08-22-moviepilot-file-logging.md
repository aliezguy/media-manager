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

Add `import subprocess` and `import textwrap` to `backend/tests/test_mp_wash_subscription.py`, then add this test near the existing diagnostics tests. The subprocess loads the real Uvicorn logging configuration before importing MoviePilot, matching application startup order without mutating pytest's global logging state:

```python
def test_moviepilot_logs_reach_root_file_handler_under_uvicorn(tmp_path):
    log_path = tmp_path / "app.log"
    backend_dir = os.path.dirname(os.path.dirname(mp.__file__))
    script = textwrap.dedent(
        """
        import logging
        import logging.config
        import sys

        from uvicorn.config import LOGGING_CONFIG

        logging.config.dictConfig(LOGGING_CONFIG)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        handler = logging.FileHandler(sys.argv[1], encoding="utf-8")
        root_logger.addHandler(handler)

        from services import mp_service

        mp_service.logger.info("moviepilot-file-log-probe")
        handler.flush()
        root_logger.removeHandler(handler)
        handler.close()
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script, str(log_path)],
        cwd=backend_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "moviepilot-file-log-probe" in log_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
backend/venv/bin/python -m pytest \
  backend/tests/test_mp_wash_subscription.py::test_moviepilot_logs_reach_root_file_handler_under_uvicorn -v
```

Expected: FAIL because the temporary log file is empty. The current MoviePilot logger is Uvicorn-owned, has `propagate=False`, and therefore does not reach the root file handler.

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
  backend/tests/test_mp_wash_subscription.py::test_moviepilot_logs_reach_root_file_handler_under_uvicorn -v
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
