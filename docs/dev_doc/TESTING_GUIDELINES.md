# Testing Guidelines

**Audience:** LLM agents writing tests for any project in this codebase.
**Read this before writing any test file.**

You are biased toward making tests pass. A passing test is not evidence of correctness — it is evidence that your test setup permits success. Your job is to write tests that FAIL when bugs exist, not tests that PASS when your mocks cooperate.

---

## 1. Your Default Mode Is Wrong

When you write a test, your instinct is to:

1. Set up mocks that return success.
2. Call the function.
3. Assert the output looks right.
4. Report "all tests pass" and feel satisfied.

This is **testing your mocks, not the code.** The test passes because you told it to pass. It would still pass if the function under test had a critical auth bug, a data corruption bug, or silently swallowed every error. You have written a tautology.

A test has value only when it can answer: **"If a specific bug existed, would this test fail?"** If you cannot name the bug, the test is decoration.

---

## 2. The Five Biases

### Bias 1: Completion Bias

You treat a passing test as completed work. A failing test feels like a mistake to fix. So you unconsciously optimize for green.

**The problem:** Tests that always pass are worthless. The entire point of a test is that it CAN fail. If your setup guarantees success, you have written nothing.

**The override:** After writing a test, mentally introduce a bug into the code under test. Would your test catch it? If not, rewrite the test until it would.

### Bias 2: Mock-Everything Reflex

You aggressively mock dependencies to achieve "isolation." But every mock removes a bug surface. When you mock an HTTP client, you remove auth, serialization, network, and timeout logic from the test. Those are where bugs live.

**Real incident — what went wrong:**

```python
# BAD: This test passed for weeks while check_capacity was broken in production.
# The bug: aiohttp.ClientSession() was created WITHOUT auth headers.
# The mock: returns 200 regardless of headers. Bug invisible.

mock_session = MagicMock()
mock_session.get = MagicMock(return_value=fake_200_response)
mock_session.__aenter__ = AsyncMock(return_value=mock_session)

with patch("aiohttp.ClientSession", return_value=mock_session):
    result = await tool.execute(action="check_capacity", template="echo-gpu-standard")

assert result.success  # Always passes. Bug never caught.
```

```python
# GOOD: Same mock, but now asserts on the MECHANISM — were auth headers passed?

with patch("aiohttp.ClientSession", return_value=mock_session) as mock_cls:
    result = await tool.execute(action="check_capacity", template="echo-gpu-standard")

mock_cls.assert_called_once()
call_kwargs = mock_cls.call_args
passed_headers = call_kwargs.kwargs.get("headers")
assert passed_headers is not None, (
    "ClientSession created WITHOUT auth headers — "
    "requests to auth-protected endpoints will return 401"
)
assert "X-User-Id" in passed_headers or "Authorization" in passed_headers
```

**The override:** Before writing any mock, ask: "What bug surface does this mock remove?" If the mock removes auth, permissions, serialization, or network logic, you must add a mechanism assertion on the mock itself.

### Bias 3: Happy-Path Pattern Reproduction

The dominant pattern in your training data is arrange-act-assert-on-success. You reproduce it reflexively. Tests that inject failures, corrupt state, or use adversarial inputs are rare in training data and feel unnatural to generate.

**The override:** For every happy-path test, write at least two tests from this list:
- What happens when auth is missing or rejected?
- What happens with empty string, null byte, or SQL-injection input?
- What happens when the resource has been destroyed/expired?
- What happens when the network times out or returns 500?
- What happens under concurrent access?

### Bias 4: Effort Avoidance

Verifying that auth headers were passed to a constructor requires careful mock introspection. Checking `result.success == True` requires one line. You will take the easier path every time unless forced not to.

**The override:** If a test is easy to write, it is probably not testing anything hard to get right. The hardest parts of the code — auth, concurrency, error recovery — require the most effort to test. That effort is the point.

### Bias 5: False Comprehensiveness

You produce 20 tests that all verify minor variations of the happy path, then report "20/20 passing" with confidence. Quantity creates the illusion of coverage.

**Real incident — what went wrong:**

A test suite had 30 passing tests for sandbox management. None verified that internal HTTP calls included auth headers. Three separate methods in production were making unauthenticated HTTP calls. The 30 passing tests provided false confidence that the system worked.

**The override:** Coverage is not about test count. It is about bug-class coverage. One test that verifies auth headers are passed catches an entire class of bugs. Twenty tests that verify output formatting catch zero auth bugs.

### Bias 6: Surface-Symptom Acceptance

When a component times out or returns a generic error, you accept the surface symptom ("timed out," "creation failed," "unavailable") and move on. You never interrogate the component's internal state to discover the actual cause. This makes your test a symptom reporter, not a bug finder.

**Real incident — what went wrong:**

A GPU sandbox E2E test created a sandbox and waited for it to become ready. The SDK's health-check loop exhausted its retries and raised `SandboxUnavailableError("timed out after 90 attempts")`. The test reported `FAIL: sandbox creation timed out` and moved on.

The actual bug: an outdated `dataclasses.py` PyPI backport in the container's heavy-venv shadowed Python 3.11's stdlib `dataclasses` module. Every time `uvicorn` started, it crashed with `AttributeError: module 'typing' has no attribute '_ClassVar'`. Docker restarted the container 4 times, each time crashing identically. The sandbox was crash-looping, not slow.

The test had the sandbox ID. It could have queried `GET /sandboxes/{id}` (status: `error`), fetched `GET /sandboxes/{id}/logs?tail=10` (the full `AttributeError` traceback), and reported the real crash. Instead, it reported a timeout. A human had to manually navigate to the sandbox's web UI, click "Logs," and read the traceback to diagnose the issue.

**The core failure:** The test treated a timeout as a terminal condition rather than a diagnostic opportunity. A timeout is a symptom — it means "something prevented readiness." The test's job is to answer *what* prevented readiness, not merely to observe that readiness was not achieved.

**The override:** Whenever a test observes a timeout, generic error, or unexpected failure from a system component (container, service, sandbox, server), the test must:

1. **Query the component's status** via its management API (e.g., `GET /sandboxes/{id}` → check if status is `error`)
2. **Fetch internal logs** (e.g., `GET /sandboxes/{id}/logs?tail=15`) and include them in the failure output
3. **Report the actual cause** in the test failure message, not the surface symptom

If the management API is unavailable, log that fact too — it is a separate failure worth reporting.

This applies to any layer: SDK timeout handlers, E2E test failure branches, integration test error paths. A "timed out" message with no further investigation is a test design bug.

---

## 3. Rules

Follow these rules when writing tests. They are not suggestions.

1. **Before writing any mock, identify the bug surface it removes.** If the mock removes auth, network, serialization, or permission logic, do not mock it away — assert on it. Use `assert_called_with`, `call_args`, or constructor introspection to verify the mock was used correctly.

2. **For every `assert result.success`, add a mechanism assertion.** HOW did it succeed? What function was called, with what arguments, with what headers? Assert on the call, not just the return value.

3. **E2E tests must exercise the production code path.** If production uses `ToolClass.method()`, the test must call `ToolClass.method()` — not the underlying API endpoint directly. If the test calls the API with a pre-authenticated session, it tests the API, not the tool's usage of the API. Those are different things.

   ```python
   # BAD: Tests the API endpoint, not the tool. Misses auth bugs in the tool.
   async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}) as session:
       resp = await session.get(f"{API_URL}/cluster/capacity")
       assert resp.status == 200

   # GOOD: Tests the tool's code path, which constructs its own session.
   from myapp.config import AppConfig
   from myapp.tools import CapacityTool
   from myapp.manager import Manager

   mgr = Manager(config=real_config, user_id="test-user", tenant_id="test-tenant")
   tool = CapacityTool(manager=mgr)
   result = await tool.execute(action="check_capacity", template="echo-cpu-standard")
   assert result.success, f"Tool failed: {result.error}"
   ```

4. **Every test must have a "this test catches X" rationale.** If you cannot name a specific bug class the test would catch, the test is useless. Do not write it.

5. **When a test fails, investigate root cause before attributing to environment.** "Infrastructure flakiness" is a hypothesis that requires evidence. Pull logs, check database state, examine the error. If 7 of 10 tests fail with empty responses, that is a systematic failure, not flakiness.

6. **Test failure modes more than success modes.** For every happy-path test, write at least two adversarial tests. The happy path is the least likely place for bugs.

7. **Never report "all tests pass" without answering: "Would these tests have caught the last 3 bugs found in this component?"** If the answer is no, your tests are incomplete.

---

## 4. Patterns

### Pattern A: Auth Verification

When the code under test makes HTTP calls, verify that auth credentials are attached.

```python
with patch("aiohttp.ClientSession", return_value=mock_session) as mock_cls:
    await function_under_test()

mock_cls.assert_called_once()
headers = mock_cls.call_args.kwargs.get("headers")
assert headers is not None, "HTTP session created without auth headers"
assert "Authorization" in headers or "X-User-Id" in headers, (
    f"Auth missing from headers: {headers}"
)
```

### Pattern B: Mechanism Assertion

Do not just assert on the return value. Assert on what the code did internally.

```python
# BAD: Only checks the result. The mock guarantees the result.
result = await tool.execute(action="destroy", sandbox="worker")
assert result.success

# GOOD: Verifies the right internal method was called with the right args.
result = await tool.execute(action="destroy", sandbox="worker")
assert result.success
manager.destroy.assert_called_once_with("worker")
```

### Pattern C: Failure Injection

Test what happens when dependencies fail.

```python
# Network failure
mock_session.get = MagicMock(
    return_value=context_manager_raising(OSError("Connection refused"))
)
result = await tool.execute(action="check_capacity", template="echo-cpu-standard")
assert not result.success
assert "Connection refused" in result.error

# Auth rejection (the call succeeds but returns 401)
mock_session.get = MagicMock(return_value=mock_response(status=401))
result = await tool.execute(action="check_capacity", template="echo-cpu-standard")
assert not result.success
assert "401" in result.error

# Timeout
mock_session.get = MagicMock(
    return_value=context_manager_raising(asyncio.TimeoutError())
)
result = await tool.execute(action="check_capacity", template="echo-cpu-standard")
assert not result.success
```

### Pattern D: Adversarial Inputs

Parametrize with inputs designed to break assumptions.

```python
@pytest.mark.parametrize("bad_input", [
    "",                              # empty string
    "   ",                           # whitespace only
    "valid'; DROP TABLE users;--",   # SQL injection
    "../../../etc/passwd",           # path traversal
    "valid\x00hidden",              # null byte injection
    "a" * 10000,                     # overlong input
    "emoji🔥input",                  # Unicode
    "\n\r\t",                        # control characters
])
async def test_rejects_adversarial_input(bad_input):
    result = await tool.execute(action="create", template=bad_input)
    assert not result.success, (
        f"Adversarial input was accepted: {repr(bad_input)}"
    )
```

### Pattern E: State Corruption

Operate on resources that no longer exist.

```python
async def test_read_from_destroyed_sandbox():
    result = await tool.execute(action="create", template="echo-cpu-light", alias="temp")
    assert result.success

    result = await tool.execute(action="destroy", sandbox="temp")
    assert result.success

    # Now try to use the destroyed sandbox
    result = await read_tool.execute(file_path="test.txt", sandbox="temp")
    assert not result.success
    assert "temp" in result.error  # Should mention the sandbox name
```

### Pattern F: Production Code Path E2E

Instantiate real objects with real config. No mocks. No parallel paths.

```python
async def test_capacity_through_real_tool():
    """Uses the actual Tool → Manager → HTTP code path.
    Catches auth bugs that mock-based tests miss."""
    from myapp.config import ExecutionConfig, NodeConfig
    from myapp.runtime import SandboxManager
    from myapp.tools import SandboxManageTool

    config = ExecutionConfig(node=NodeConfig(server_url=REAL_API_URL, timeout=15))
    manager = SandboxManager(
        config=config,
        user_id="test@example.com",
        tenant_id="default",
    )
    tool = SandboxManageTool(sandbox=manager)

    result = await tool.execute(action="check_capacity", template="echo-cpu-standard")
    assert result.success, f"Real tool call failed: {result.error}"
    assert result.metadata["free_cpus"] >= 0
```

### Pattern G: Concurrent Stress

Test what happens when multiple operations race.

```python
async def test_concurrent_creates_do_not_corrupt_pool():
    results = await asyncio.gather(*[
        tool.execute(action="create", template="echo-cpu-light", alias=f"worker-{i}")
        for i in range(5)
    ], return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception) and r.success]
    errors = [r for r in results if isinstance(r, Exception) or not r.success]

    # At least some should succeed; none should corrupt internal state
    assert len(successes) + len(errors) == 5
    # Verify pool is consistent
    listing = await tool.execute(action="list")
    assert listing.success
```

### Pattern H: Failure Diagnostics on Timeout

When a component fails to become ready, query its internal state before reporting failure.

```python
# BAD: Reports symptom, discards diagnostic opportunity.
result = await tool.execute(action="create", template="echo-gpu-light", alias="worker")
if not result.success:
    check("sandbox created", False, f"error={result.error}")
    return  # <-- test ends, no one knows WHY it failed

# GOOD: On failure, interrogate the component.
result = await tool.execute(action="create", template="echo-gpu-light", alias="worker")
if not result.success:
    check("sandbox created", False, f"error={result.error}")
    sandbox_id = (result.metadata or {}).get("sandbox_id")
    if sandbox_id:
        status_code, detail = await api_get(session, f"/sandboxes/{sandbox_id}")
        if status_code == 200:
            log(f"  Sandbox status: {detail.get('status')}")
            log(f"  Error: {detail.get('errorMessage', 'N/A')}")
        log_code, logs = await api_get(session, f"/sandboxes/{sandbox_id}/logs?tail=15")
        if log_code == 200:
            for entry in (logs.get("logs") or []):
                log(f"  LOG: {entry.get('line', '')[:150]}")
    return
```

This applies equally to SDK-level code. When `_wait_for_ready` is about to raise a timeout error, it should query the sandbox status and logs via the management API and include the real crash reason in the exception message. A "timed out" exception with no crash context forces the caller to manually investigate — the SDK had the information and threw it away.

---

## 5. Pre-Flight Checklist

Answer every question before declaring tests complete. If any answer is "no," the tests are incomplete.

1. **Can you name a specific bug that each test would catch?**
   If a test cannot name its bug target, it is a tautology.

2. **Do any tests verify mechanism (how), not just outcome (what)?**
   At least one test must assert on call args, headers, or constructor parameters.

3. **Did you test what happens when auth fails or is missing?**
   Auth is the #1 source of silent production failures.

4. **Did you test with adversarial inputs?**
   Empty strings, null bytes, injection payloads, overlong strings, Unicode.

5. **Did you test operations on invalid/destroyed/expired state?**
   Use-after-destroy, expired tokens, deleted resources.

6. **For mocked tests: does the bug surface survive the mocking?**
   If the mock removes the auth path, did you add an assertion on the mock call?

7. **For E2E tests: does the test exercise the production code path?**
   Not the API directly. The actual tool/service class that production uses.

8. **Did you investigate every test failure?**
   No failure was dismissed as "flaky" or "infrastructure" without evidence.

9. **Would these tests have caught the last known bug in this component?**
   If you know about a recent bug and your tests would not have caught it, add one that would.

10. **When a test observes a timeout or generic failure, does it fetch internal logs and status?**
    A timeout is a symptom. The test must query the failed component's management API for status, error messages, and recent logs, then include them in the failure output. "Timed out" without diagnostics is a test design bug.

---

## 6. E2E Test Design

### Execution

- Run E2E tests **sequentially** unless you are specifically testing concurrency. Concurrent test launches cause resource contention that masks real bugs with infrastructure noise.
- Set explicit timeouts on every network call. Do not let tests hang.
- Always clean up created resources (sandboxes, files, database records) in a `finally` block or equivalent, even when the test fails.

### Failure Investigation Protocol

When an E2E test fails:

1. **Pull server logs** from the time window of the failure.
2. **Check database state** for the resources involved.
3. **Check resource cleanup** — did a prior test leak resources that starved this one?
4. **Identify the HTTP status code** and the full error response, not just "request failed."
5. **Distinguish test bug from code bug.** Is the test setup wrong, or is the code broken?
6. **Never retry without understanding.** If a test is intermittent, the system has a concurrency or state bug. Adding retries hides the bug.
7. **On timeout, query the component's management API.** A "timed out" result means the component never became ready — not that the network was slow. Fetch its status (`error`? `crash-looping`?) and tail its logs. The crash reason is almost always in the component's own logs, not in the caller's timeout message.

### Resource Management

```python
async def test_something():
    resource_id = None
    try:
        resource_id = await create_resource()
        # ... test logic ...
    finally:
        if resource_id:
            await destroy_resource(resource_id)
```

Do not rely on test framework teardown alone. If the test raises an exception mid-way, the teardown might not run. Use `try/finally` at the operation level.

---

## 7. Handling Test Failures

- A failing test is **information**, not an error to fix in the test.
- Distinguish "test bug" from "code bug." Investigate before touching the test code.
- If a test failure is intermittent, the system has a **concurrency or state bug**. Do not add retries. Do not mark it as "flaky." Find the race condition.
- Document every failure investigation, even if the conclusion is "test setup was wrong." The investigation is as valuable as the fix.
- When multiple tests fail simultaneously, look for a **common cause** (resource exhaustion, auth expiry, service down) before investigating each failure independently.
- Never attribute failures to "infrastructure" without checking logs, database state, and resource counts. "Infrastructure" is a diagnosis, not a default explanation.
