# Agent answers: 5 sample questions

**Setup:** Claude Code (subscription) calling the `vscode-ticket-search` MCP server (`src/mcp_server.py`, tools `search_tickets` and `get_ticket`). Index: 100,000 `microsoft/vscode` issues (2023-06-29 to 2026-11-11) in turbopuffer `gcp-northamerica-northeast2`. Hybrid = vector (bge-small-en-v1.5) + BM25, fused server-side with RRF.

**Grading:** a citation counts as correct if the linked ticket actually supports the claim it's attached to (checked by hand).

## Retrieval pre-check (search.py, before the agent run)

| # | Question | Top results relevant? | Notes |
|---|---|---|---|
| 1 | Why does the integrated terminal freeze on startup? | Yes | Terminal freeze tickets ranked 1–6 |
| 2 | What causes "Extension host terminated unexpectedly"? | Yes | Exact error string matched; several Copilot-related reports |
| 3 | Open issues about high CPU from the file watcher on macOS? | Partly | Mostly renderer-CPU tickets; dataset may have few open macOS file-watcher tickets |
| 4 | How have people fixed Remote-SSH connection timeouts? | Yes | Mix of completed and duplicate SSH tickets |
| 5 | IntelliSense performance problems in the last year? | After rephrasing | Literal wording returned generic "slow" tickets; "suggestions slow to appear" worked |

## Agent run

| # | Question | Answer summary | Tickets cited | Citations correct? | Tool calls |
|---|---|---|---|---|---|
| 1 | Why does the integrated terminal freeze on startup? | No single root cause in the tickets. Freezes cluster around specific releases (1.97.0 Windows freeze, 1.109.5 conpty failure on Win10 1607, 1.112.0 auto-Enter loop). Rolling back fixed every report. Agent flagged that the official fix isn't in the index. | #240006, #240212, #240145, #239985, #298293, #303945 | 6/6 (4 read in full via `get_ticket`; #240145 and #239985 checked from snippets only) | 6 (2 search + 4 get_ticket) |
| 2 | What causes "Extension host terminated unexpectedly"? | The extension host is the separate process that runs extensions; VS Code shows this error after 3 crashes in 5 minutes. Causes in the tickets: a crashing extension (mostly Copilot, including a native N-API failure, exit code 134), an extension's error handler throwing (fixed in VS Code), release regressions that persist with extensions disabled, and a Remote variant tied to SSH disconnects. Advice: `--disable-extensions`, then Extension Bisect; otherwise roll back one version. | #274251, #274095, #254800, #194588, #277064, #330157, #313574 | 7/7 (5 read in full; #274095 and #313574 checked from snippets only, since `get_ticket` failed on #274095) | 10 (2 search + 8 get_ticket, incl. 3 failed calls: #274095 twice, #282433 as a diagnostic check) |
| 3 | Open issues about high CPU from the file watcher on macOS? | No exact open match. Closest open: Git watching all worktrees recursively (macOS path), 1.113.0 runaway ripgrep freezing macOS, Claude session refresh overloading file watching (no OS). Only open `file-watcher` CPU bug is Windows. Exact symptom exists in closed tickets (fixed / duplicate). | #305926, #305544, #328589, #287800, #226401, #214567 | 6/6 from snippets only (open tickets unreadable due to `get_ticket` bug, since fixed) | 5 (5 search, 0 get_ticket) |
| 4 | How have people fixed Remote-SSH connection timeouts? | Mostly by rolling back one version until a VS Code fix shipped (1.90 Pi/glibc, 1.123–1.125 WSL2/VPN). Fixed in product: 1.95 false timeout with non-default profile; later support for custom glibc on old Linux hosts. Tip: the real error often sits above the timeout in the log. Still open: 1.107 timeout possibly tied to Workspace Trust prompt. | #214462, #322556, #232602, #231623, #252039, #282771 | 6/6 (4 read in full; #231623 and #282771 from snippets) | 7 (3 search + 4 get_ticket) |
| 5 | IntelliSense performance problems in the last year? | Dominant theme: Copilot ghost text delaying or suppressing native suggestions (one fixed Sep 2026, one open), plus a Feb 2026 experiment adding `offWhenInlineCompletions`. Second: Copilot's own suggestions slow. Third: suggestions not appearing (TS popup open; terminal IntelliSense in 1.106 fixed). Few pure native-IntelliSense latency reports. | #318380, #320940, #314220, #293443, #301384, #325482, #341751, #315511, #277387 | 9/9 (2 read in full; 7 from snippets) | 5 (3 search + 2 get_ticket) |

## Run notes

### Q1: terminal freeze on startup (2026-11-11)

- **Searches:** hybrid, "integrated terminal freezes on startup", limit 10, 61 ms. Then vector with `labels=["terminal"]`, "terminal hangs when opening, blank terminal not responding at launch", limit 10, 16 ms. Both times are server-side `search_ms`, measured from a laptop in Toronto.
- **Retrieval:** hybrid surfaced the 1.97.0 duplicate cluster in the top 7, plus the 1.109.5 and 1.112.0 regressions. The vector-only search returned looser matches ("terminal is blank", "not opening"), which is a useful contrast for the README.
- **Answer quality:** grounded and cited. The agent said plainly that the tickets don't state a root cause and didn't invent a fix.
- **Limitation found:** the index stores only the original issue text. Maintainer comments and duplicate-parent links aren't included, so the agent can describe the symptoms and the workaround but can't confirm the official fix. Backlog candidate: index the closing comment or the duplicate target.

### Q2: "Extension host terminated unexpectedly" (2026-11-11)

- **Searches:** bm25, `"Extension host terminated unexpectedly"`, limit 10, 19 ms. Then hybrid with `state_reason=completed`, "Extension host terminated unexpectedly cause", limit 10, 19 ms. Both are server-side `search_ms`, measured from a laptop in Toronto.
- **Retrieval:** BM25 matched the exact error string well: 6 of the top 10 titles quote it. The hybrid search filtered to completed tickets surfaced fixed root causes (#194588, #277064) that BM25 alone ranked lower or missed.
- **Answer quality:** grounded and cited. Where a ticket was closed as fixed but the fix isn't in the indexed text (#277064), the agent said so.
- **Bug found: `get_ticket` fails on open tickets.** #274095 failed twice and #282433 failed once with a generic "Error executing tool get_ticket". Closed tickets work. Suspected cause: `closed_at` is null on open tickets (not yet confirmed). Fix in `src/search.py` / `src/mcp_server.py` before Step 5.
- **Fixed (2026-11-11, same day):** confirmed cause: turbopuffer omits null attributes from returned rows, so `r["closed_at"]` raised `AttributeError` on open tickets. `src/search.py` now reads all attributes through a null-safe `_get()`. Verified on #282433, #274095, #318380 (open) and #252039 (closed).

### Q3: open macOS file-watcher CPU issues (2026-11-11)

- **Searches:** hybrid `state=open, labels=[macos]` (19 ms); hybrid `state=open` with watcher terms (27 ms); bm25 "fileWatcher high CPU" `state=open` (15 ms); hybrid `state=closed` (24 ms); hybrid `state=open, labels=[file-watcher]` (16 ms).
- **Retrieval:** the first three searches returned renderer-CPU and Copilot-CPU tickets. The `file-watcher` label (spotted in closed results) was the filter that worked. Confirms the pre-check: few open macOS file-watcher CPU tickets exist in the data, so this is a data gap, not a search miss.
- **Answer quality:** the agent said plainly there was no exact match and pointed to the nearest open tickets plus closed precedents.
- **Change made:** added `file-watcher` and `ssh` to the common labels in the MCP server instructions.

### Q4: Remote-SSH timeout fixes (2026-11-11)

- **Searches:** hybrid `state_reason=completed` (22 ms); bm25 `remote.SSH.connectTimeout` (20 ms); hybrid `labels=[remote]` (22 ms).
- **Retrieval:** the `completed` filter was the most useful. BM25 on the setting name missed: no ticket in the index quotes it.
- **Limitation (again):** most "fixes" live in comments, which aren't indexed. Third question where this limited the answer (Q1, Q2, Q4).

### Q5: IntelliSense performance, last year (2026-11-11)

- **Searches:** hybrid "IntelliSense suggestions slow to appear" `created_after=2025-11-11` (27 ms); hybrid `labels=[suggest]` (16 ms); bm25 "IntelliSense slow" (15 ms).
- **Retrieval:** the rephrased hybrid query from the pre-check worked. Literal BM25 "IntelliSense slow" mostly returned unrelated "slow" tickets (file search, copy, backup).
- **Answer quality:** grouped into three themes. Caveat stated: few pure native-IntelliSense latency reports surfaced.

## Summary

- **5/5 questions answered with citations; 34/34 citations judged correct** (15 checked against full text, 19 against search snippets only).
- **Search latency** (server-side `search_ms`): 15–61 ms across all 15 searches. Informal, not a benchmark; Step 5 measures properly.
- **What made the difference:** BM25 for exact error strings (Q2), metadata filters (`state_reason=completed`, labels, dates) for Q3–Q5.
- **Main limitation:** only the original issue text is indexed. Next improvement: index the closing comment or the duplicate target.
