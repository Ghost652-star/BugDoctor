---
name: lookup-framework-docs
description: |
  Fetch current third-party library documentation via Context7 MCP when training data may
  be stale. Use when errors touch site-packages/framework code, deprecated APIs, or
  ImportError says symbol missing after env check passed. Mentions LangChain, Spring AI,
  Django, FastAPI, etc. Do NOT use for pure project logic with no library/API uncertainty.
allowedTools:
  - mcp_context7_resolve-library-id
  - mcp_context7_query-docs
  - read_file
  - get_environment
mode: inline
---

# Framework Docs via Context7

Training data goes stale. **Context7 MCP** returns version-aware docs. This skill tells you exactly how to call it.

## Prerequisites

MCP tools must appear in your tool list:

- `mcp_context7_resolve-library-id`
- `mcp_context7_query-docs`

If missing, say so plainly — fall back to `read_file` on dependency files + conservative advice. Do not hallucinate API signatures.

## Workflow

```
resolve-library-id  →  pick best library ID  →  query-docs  →  read_file user code  →  reconcile
```

### 1. resolve-library-id

Extract library name from ImportError, stack frame (`site-packages/langchain/...`), or user message.

Call **`mcp_context7_resolve-library-id`** with:

| Param | Value |
|-------|-------|
| `libraryName` | short name, e.g. `langchain`, `spring-ai`, `django` |
| `query` | full user error + what you need, e.g. "LangChain 0.3 replace LLMChain with LCEL" |

### 2. Pick library ID

From the result list prefer:

1. Exact name match
2. Higher **Benchmark Score** / **Source Reputation: High**
3. Version-specific ID if user stated version (`React 19` → pick `/reactjs/react.react/v19.x` style IDs when offered)

Wrong ID → garbage docs. Spend 10 seconds choosing.

### 3. query-docs

Call **`mcp_context7_query-docs`** with:

| Param | Value |
|-------|-------|
| `libraryId` | chosen ID, e.g. `/websites/langchain` |
| `query` | specific question: migration path, replacement API, config key |

**Be specific in query.** Bad: `"langchain"`. Good: `"How to migrate from LLMChain to LCEL RunnableSequence"`.

### 4. Reconcile with project

- `read_file` the user's call site
- Compare doc snippet vs actual installed version (`get_environment` / requirements)
- Explain: "docs say X, your code does Y, error Z means..."

## Anti-patterns

- Querying docs before confirming package is installed (run env check first if unsure)
- Pasting doc examples without adapting to user's file structure
- Using docs to justify a code edit without user asking — diagnosis only unless `apply-fix` loaded

## MCP failure modes

| Error | Meaning |
|-------|---------|
| Tool not found | MCP not configured — tell user to set `.bugdoctor/config.yaml` |
| Empty / rate limit | retry with narrower query or report limitation |
| Doc contradicts installed version | trust **installed version** + doc for that major version |

## Output (Chinese)

```
## 文档查询
- 库: ... | libraryId: ...
- 文档要点: ...
- 与用户代码差异: ...
- 根因判断: ...
- 修复思路: ...（不直接改码，除非 apply-fix）
```
