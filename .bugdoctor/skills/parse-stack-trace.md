---
name: parse-stack-trace
description: |
  Parse stack traces and runtime error logs to locate root cause in the user's project code.
  Use when the message contains Traceback, stack trace, Exception, Error, panic, or file:line.
  Do NOT use when there is no error text—only vague behavior descriptions like "result is wrong".
allowedTools:
  - read_file
  - grep_code
  - glob_files
mode: inline
---

# Stack Trace Reading — Field Guide

Your job is to turn a stack trace into **verifiable file:line hypotheses**, not to guess from the exception message alone.

<HARD-GATE>
Do NOT propose a root cause until you have `read_file` at least the **bottom-most in-project frame** (see below). Reading the error message is not reading the code.
</HARD-GATE>

## Step 0 — Check for module map

If `<system-reminder>` mentions `.bugdoctor/module-map.md`, or you suspect a multi-module project: `read_file` that map **first**, then locate where this traceback sits on the call chain.

## Recognize the format

**Python** — bottom of block is often the true raise site; read top-to-bottom for narrative, bottom-up for causality:

```
Traceback (most recent call last):
  File "app/runner.py", line 41, in run
    return loader.parse(raw)
  File "app/loader.py", line 42, in parse
    return int(text)
ValueError: invalid literal for int() with base 10: ''
```

**Java** — read **Caused by:** chains from bottom; your code is usually `com.yourcompany...`, not `java.base` / `org.springframework`:

```
Caused by: java.lang.NullPointerException
    at com.example.service.OrderService.process(OrderService.java:88)
```

## Signal vs noise (memorize this)

| Read this | Skip this (unless no alternative) |
|-----------|-------------------------------------|
| Paths under workspace / `--project` | `site-packages`, `venv`, `Lib\python` |
| `src/`, `app/`, `main.py`, company package | `lib/python3.11`, `jdk.internal` |
| Frame immediately above the raise | Deep framework internals |

**Anti-pattern:** blaming `requests` or `django` internal frames when a project file appears lower in the chain.

## Investigation loop (not a checklist — repeat until done)

Each iteration picks **one** uncertainty and resolves it with a tool:

1. **Identify anchor frame** — lowest project file:line in the trace
2. **`read_file` with offset/limit** — ±30 lines around anchor; note function signature and locals
3. **`grep_code`** — who calls this function? Where does the bad argument enter?
4. **Update hypothesis table** — confirm / reject / new question
5. **Stop early** if one hypothesis is confirmed with code evidence

Do NOT read every file on the stack before updating hypotheses. One frame → one thought → one tool.

## Exception type → where to look next (heuristic only)

| Exception | Often means | Next tool action |
|-----------|-------------|------------------|
| `ModuleNotFoundError` | missing dep / wrong PYTHONPATH | consider `load_skill(check-env-dependencies)` |
| `TypeError`, `ValueError`, `AttributeError` | bad value at boundary | grep upstream caller, read parent frame |
| `KeyError`, `IndexError` | data shape mismatch | read where collection built |
| Wrapped/re-raised errors | original cause hidden | search for `except` that swallows or returns None |

## Common failure modes (avoid)

- **Last-line fixation:** `ValueError: invalid literal` tells you *what*, not *why `text` was empty*
- **Library rabbit hole:** reading urllib3 source when your `fetcher.py` returns `""`
- **Premature fix:** suggesting `int(x or 0)` before tracing who passed `""`

## When ImportError appears in the trace

Stop stack-only analysis. Environment may be the real issue — load **`check-env-dependencies`** instead of continuing to grep application logic.

## Output (Chinese)

When ready to report (not before anchor frame is read):

```
| 假设 | 依据 | 工具结果 |
|------|------|----------|
| ...  | ...  | 确认/否定 |

**根因**：...（file:line）
**证据链**：帧 A → 帧 B → 落点
**修复思路**：（描述 only，不改代码）
```

If 3 tool rounds still inconclusive: list open questions and ask user for repro command / input data.
