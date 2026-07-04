---
name: check-env-dependencies
description: |
  Rule out environment, dependency, and configuration causes before editing application code.
  Use for ImportError, ModuleNotFoundError, connection refused, port errors, SyntaxError on
  valid modern syntax, or "it worked yesterday". Do NOT use when traceback clearly points
  to a logic bug in project code and environment is already verified.
allowedTools:
  - get_environment
  - read_file
  - run_command
mode: inline
---

# Environment & Dependency Triage

Many "code bugs" are **wrong Python**, **missing package**, or **stale venv**. This skill prevents the agent from `edit_file` on code that was never the problem.

<HARD-GATE>
Do NOT call `edit_file` or suggest code patches until this skill either **confirms** an environment root cause or **explicitly records** what was ruled out.
</HARD-GATE>

## Fast path

```
get_environment          → platform, python version, dependency file previews, pip list snippet
read_file requirements.txt / pyproject.toml / .env   (whichever exist)
run_command              → only when get_environment is inconclusive
```

## Symptom routing

| User symptom | First action | If negative |
|--------------|--------------|-------------|
| `ModuleNotFoundError: No module named 'foo'` | `get_environment` + `run_command pip show foo` | typo in import vs package not installed |
| `ImportError: cannot import name 'X' from 'pkg'` | check installed version vs code expectation | load `lookup-framework-docs` (API removed/changed) |
| Connection refused / timeout | read config, check service URL/port | not a code bug in app logic |
| SyntaxError on walrus / match | compare `python --version` with code style | need newer interpreter |
| "昨天还能跑" | diff lock files, `pip list`, git log on requirements | ask what changed externally |

## Interpret `get_environment`

- **Two Pythons:** BugDoctor process python vs `python --version` in project — user may run with wrong interpreter
- **Package in requirements but not in pip list** → install issue, not logic bug
- **Package installed, import still fails** → PYTHONPATH / editable install / wrong venv

## Useful run_command examples (pick one, don't spam)

```bash
python --version
pip show requests
pip list | findstr langchain    # Windows
pip check                       # broken deps
```

If `run_command` fails due to sandbox, report that — don't fabricate env state.

## When to hand off

| Finding | Next step |
|---------|-----------|
| Package missing / wrong version | tell user install command; stop code diagnosis |
| Package OK, API error in library frame | `load_skill(lookup-framework-docs)` |
| Env ruled out | document exclusions, return to `parse-stack-trace` |

## Output template (Chinese)

```
## 环境检查
- Python: ...
- 相关依赖: ...
- 已排除: ...
- 结论: 环境根因 / 环境已排除

## 建议
（装包 / 换 venv / 改配置 / 继续查代码）
```
