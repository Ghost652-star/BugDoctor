---
name: apply-fix
description: |
  Apply a surgical code fix after root cause is confirmed, with mandatory impact scope
  assessment. Use ONLY when user explicitly requests a fix (fix, 帮我修, apply the fix).
  Requires prior diagnosis with file:line evidence. Do NOT use for diagnosis-only turns.
allowedTools:
  - read_file
  - grep_code
  - edit_file
  - run_command
mode: inline
---

# Apply Fix (with Impact Assessment)

User authorized code change. **Diagnosis is done; surgery begins.** Skipping impact analysis causes "fixed here, broke there."

<HARD-GATE>
Do NOT call `edit_file` until:
1. Root cause is documented (file:line or equivalent)
2. You completed **grep impact scan** for every symbol you will change
3. You stated risk level (低/中/高) in Chinese to the user
</HARD-GATE>

## Phase 1 — Confirm you belong here

If user only pasted an error and never said "fix/修": **stop** — unload this workflow mentally, diagnose first.

If root cause is still a guess: **stop** — more `read_file`, not edit.

## Phase 2 — Impact scope (mandatory)

Identify the **smallest change** that fixes root cause. Then:

### grep everything that breaks if you change it

```
grep_code "function_name"
grep_code "ClassName"
grep_code "from app.loader import parse"
```

For each hit classify:

| Hit location | Question |
|--------------|----------|
| Same file, private helper | usually 低 |
| Same module, multiple call sites | 中 — read each caller |
| Public API / exported symbol | 高 — prefer not changing signature |
| Tests only | note but lower user risk |

### Risk matrix

| 风险 | 条件 | 策略 |
|------|------|------|
| 低 | 局部逻辑, 单文件, 无签名变化 | proceed with edit_file |
| 中 | 多调用方, 行为变化 | read callers, minimal diff |
| 高 | 改函数签名 / 公共接口 | fix at data source instead; or warn user |

**Prefer:** fix where bad data enters, not where exception throws — often smaller blast radius.

## Phase 3 — edit_file discipline

1. `read_file` target — copy `old_string` **exactly** (spaces matter)
2. One logical change per edit
3. `old_string` must match **once** in file
4. After edit: `run_command` with user's repro command

## Phase 4 — Verify honestly

```
run_command  →  user’s failing command or closest test
```

| Result | Action |
|--------|--------|
| Pass | report success + impact summary |
| Fail | report failure, do NOT claim fixed |
| Cannot run | say what you would have run |

## Anti-patterns

- Drive-by refactors while fixing a bug
- Catching all exceptions to "fix" without root cause
- Changing return type to silence error without updating callers

## Output (Chinese)

```
## 修复报告
- 修改: file:line — 做了什么
- 影响范围: grep 命中 N 处 (列出关键路径)
- 风险等级: 低/中/高
- 验证: run_command 结果
```
