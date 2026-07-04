---
name: map-project-modules
description: |
  Build a reusable module relationship map before deep diagnosis in complex codebases.
  Use when glob shows many directories/packages, traceback spans multiple business modules,
  or you keep re-discovering structure every turn. Do NOT use for tiny projects (≤5 source
  files) where a single glob lists everything.
allowedTools:
  - glob_files
  - grep_code
  - read_file
  - write_file
mode: inline
---

# Project Module Map

Complex bugs are often **wrong-layer** bugs: data breaks at a boundary, not at the line that throws. This skill builds a **reusable map** so later turns (and later bugs in the same session) don't repeat `glob` archaeology.

## When you are done

You are done when you can answer in one breath:

> "Entry is X → flows through A → B → C; external IO happens at B; the user's error likely involves B→C."

Then **`write_file`** to `.bugdoctor/module-map.md` and stop expanding scope.

## Anti-pattern: architecture astronaut

Do NOT try to document every file. The map is a **navigation aid for diagnosis**, not a design doc. Leave sections as "未读" intentionally.

## Phase 1 — Size check (30 seconds)

```
glob_files **/*.py   (or **/*.java)
```

| Result | Action |
|--------|--------|
| ≤5–8 files, flat layout | **Abort this skill** — use parse-stack-trace directly |
| Multiple dirs / packages | Continue |
| `module-map.md` exists | `read_file` it; update only if stale |

## Phase 2 — Find entry (pick any that exist)

| Clue | Tool |
|------|------|
| `if __name__ == "__main__"`, `main()`, CLI | `grep_code` / `read_file` |
| README, pyproject `[project.scripts]` | `read_file` |
| Spring `@SpringBootApplication`, `Application.java` | `glob_files` + `read_file` |

## Phase 3 — Trace dependencies (stop at 2–3 hops from entry)

Use **grep**, not mass read:

```
grep_code "from app.loader import" 
grep_code "def fetch"
grep_code "class OrderService"
```

Draw arrows only for paths **relevant to the current bug** if Traceback already hints at modules; otherwise map the main pipeline.

## Phase 4 — Mark boundaries

For each arrow A→B, ask:

- Does B receive raw external data (HTTP, file, DB, argv)?
- Is there a try/except that returns None or default?
- Type conversion (str→int, JSON parse) here?

Mark these on the map — they are where bugs **often** live.

## Write artifact

Path: **`.bugdoctor/module-map.md`** (under diagnosis workspace, via `write_file`)

```markdown
# 项目模块关系图
> workspace: ... | updated: ...

## 入口
- `python -m app.cli` → `cli.main` → ...

## 模块
| 模块 | 职责 | 关键文件 |
|------|------|----------|

## 调用链（与当前 bug 相关）
entry → runner → fetcher → loader
              ↑ IO      ↑ 类型转换 (可疑)

## 边界 / 风险点
- fetcher 网络失败返回 ""，loader.parse 未校验

## 未读（故意）
- tests/, vendor/
```

## After writing

Return to normal ReAct. Combine map + traceback to pick the next `read_file` target — do not re-glob the whole tree.
