
---
name: deliverableqa-kickoff
description: Use this whenever Mat wants to start, resume, or hand off work on the DeliverableQA Agent hackathon project (the Deloitte agentathon project, an orchestrator plus 4 specialist review agents for consulting deliverables, built with Python + LangGraph fan-out, a FastAPI server, and a 3-panel dashboard — all local, run via uv). Trigger on requests like "give me the kickoff prompt", "I need the context file for Pi", "regenerate CONTEXT.md", "how do I run DeliverableQA", or any mention of DeliverableQA, the agentathon project, or Pi setup for this project. Always produce the CONTEXT.md file and a compact copy-paste kickoff prompt together, even if only one is explicitly requested, since Pi needs both.
---

# DeliverableQA Agent — kickoff skill

Produces the two artifacts Mat needs to hand this project to whichever CLI coding agent (Pi or Claude Code) is running it:
1. `CONTEXT.md` — the full architecture, 5 agent system prompts, and shared JSON schema
2. A compact kickoff prompt to paste directly into the CLI agent, referencing CONTEXT.md

## When to just regenerate CONTEXT.md

If Mat asks for the context file, the agent skill doc, or says he doesn't have it yet:
- Create a file from `assets/CONTEXT.md` (copy verbatim — it's the canonical spec, don't rewrite it from memory).
- Present it as a file the way you would any deliverable.
- Tell him to save it as `CONTEXT.md` in the project repo root.

## When to give the kickoff prompt

Give this compact prompt (adapt lightly to what he's asking for — e.g. if he only wants the dashboard scaffolded, trim the "Scaffold now" steps to just that part). Keep it short; the detail lives in CONTEXT.md, not in the pasted prompt itself:

```
Read CONTEXT.md in this repo — it has the full architecture, 5 agent system prompts,
and shared output schema for DeliverableQA Agent.

Build target: Python + LangGraph, running locally. No cloud deployment — no client
data leaves the machine.

Stack mapping:
- Orchestrator: LangGraph graph, fans out to 4 specialist agent nodes in parallel
  (async nodes + AsyncAnthropicBedrock client)
- LLM backbone: Claude via Amazon Bedrock (AsyncAnthropicBedrock, model
  global.anthropic.claude-sonnet-5) — the team's Bedrock IAM policy denies
  Opus/Fable and Sonnet needs the `global.` cross-region inference-profile
  prefix, so verify against the actual account before assuming a plain
  ANTHROPIC_API_KEY or a different model ID will work
- Parsing: python-docx (docx), python-pptx (pptx), PyMuPDF (pdf)
- Config: YAML checklists/style rules, parsed with PyYAML
- Prompts: the 5 system prompts live as versioned .md files under prompts/,
  loaded at runtime — not hardcoded in source
- Dashboard: a 3-panel live web app (dashboard/index.html), styled with
  Tailwind CSS via CDN + Chart.js via CDN — no build step. Left panel
  uploads a deliverable (file + engagement type) and shows the current
  filename/refresh time, with a Clear button to reset to the empty state;
  middle panel lists the 4 agents with per-agent finding counts; right
  panel shows the selected agent's findings. Fetches GET /api/findings on
  load, on Refresh, and automatically after a successful upload, so it
  always shows the current output/findings.json without needing a rebuild.
- Server: server.py, a FastAPI + uvicorn app. GET /api/findings reads
  output/findings.json fresh off disk on every request (no caching);
  DELETE /api/findings clears it (powers the dashboard's Clear button);
  POST /api/analyze accepts a docx/pptx/pdf upload + engagement type and
  runs the full pipeline synchronously via run_qa.run(), writing the result
  before responding. A static mount serves dashboard/. No auth, no job
  queue, no concurrency guard — one request blocks for as long as the real
  Bedrock calls take. Both run_qa.py (CLI) and POST /api/analyze (browser)
  can kick off an analysis, writing the same output/findings.json. Keep it
  minimal — don't add auth/job-queue complexity back in unless Mat asks for it.
- Package management: uv, not pip/venv. pyproject.toml (runtime deps + a dev
  dependency group for pytest/pytest-asyncio) + uv.lock + .python-version.
  `uv sync` installs everything; `uv run <cmd>` runs any command (CLI,
  server, tests) inside the managed environment without manual activation.
- Merge strategy: default is deterministic (orchestrator/merge.py, difflib
  similarity + location match, no LLM call). Optional LLM-driven merge
  (orchestrator/llm_merge.py, --llm-merge on the CLI) calls Claude with
  prompts/orchestrator.md's PHASE 2 instructions to catch semantic duplicates
  worded very differently across agents — it MUST fall back to the
  deterministic merge on any failure (API error, malformed response,
  validation failure), never raise.
- Delta/re-run mode: orchestrator/merge.py's compute_delta() compares two
  runs' findings (matched by location + description similarity — re-runs get
  fresh finding ids, matching by id never works) into resolved/still_open/new.
  Wired via run_qa.py --previous-findings <path>; the dashboard renders the
  result as its own card whenever output/findings.json has a delta key.
- Structured output: this Bedrock route doesn't support output_config.format
  or strict tool schemas, and a $ref/$defs Pydantic schema makes the model
  unreliably stringify nested fields. Use forced tool-use (tool_choice) with
  a $ref-inlined flat schema, plus a small JSON-repair step for the residual
  cases — see agents/schema.py for the working pattern before reimplementing
  (llm_merge.py reuses the same pattern for its own schema).

Scaffold now:
1. uv init + pyproject.toml (langgraph, anthropic[bedrock], boto3,
   python-docx, python-pptx, pymupdf, pyyaml, fastapi, uvicorn[standard];
   pytest/pytest-asyncio as a dev dependency group), then `uv sync`
2. orchestrator/ — parse.py (raise a clear error on empty/corrupt input,
   don't return an empty section list silently), dispatch.py, merge.py
   (dedup + compute_delta), llm_merge.py (opt-in, falls back to merge.py)
3. agents/ — one .py file per agent from CONTEXT.md's prompts, each calling the
   shared JSON finding schema via a forced tool-use call
4. config/checklists/*.yaml and config/style_rules.yaml
5. dashboard/ — 3-panel Tailwind (CDN) + Chart.js app: left panel uploads a
   deliverable + engagement type and shows current filename/refresh time
   with a Clear button; middle panel lists the 4 agents with counts; right
   panel shows the selected agent's findings. Fetches GET /api/findings on
   load, on Refresh, and after a successful upload
6. run_qa.py — CLI entry point, calls parse -> dispatch -> merge -> report,
   writes output/findings.json (one of two ways to kick off an analysis)
7. server.py — FastAPI app: GET/DELETE /api/findings (reads/clears
   output/findings.json live, no caching), POST /api/analyze (upload +
   run the pipeline synchronously via run_qa.run()) + static mount for
   dashboard/ (the other way to kick off an analysis, from the browser)
8. tests/ — pytest suite covering parse.py edge cases, merge.py dedup/delta,
   the JSON-repair logic, and server.py's /api/analyze + /api/findings
   routes — mock the Bedrock client (and run_qa.run() for the server tests)
   throughout, no live API calls in the test suite
9. Commit scaffold once it runs end-to-end on a sample doc via the CLI, an
   upload through the dashboard also produces real findings with the server
   running, and the test suite is green (`uv run pytest`)

Ask me before adding any dependency beyond the ones listed above. Otherwise proceed
autonomously through the scaffold.
```

## When to give deployment/environment setup

There's no cloud deployment for this project — it runs locally only (`uv sync`, then `uv run --native-tls python server.py`, then open `http://127.0.0.1:8000`), per CONTEXT.md's Tech stack section and README.md's Getting started section. If Mat asks about running it on a new machine, point him at those rather than any deployment steps — there's nothing to deploy.

## Notes

- This skill is scoped to this one project — don't generalize the prompt template to other hackathon ideas unless Mat explicitly asks for that.
- If Mat says the architecture changed (e.g. adding a new agent, changing the merge strategy), update `assets/CONTEXT.md` in place so future regenerations stay current — don't let the bundled copy drift from what he's actually building.

