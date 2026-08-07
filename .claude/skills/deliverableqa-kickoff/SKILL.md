
---
name: deliverableqa-kickoff
description: Use this whenever Mat wants to start, resume, or hand off work on the DeliverableQA Agent hackathon project (the Deloitte agentathon project, an orchestrator plus 4 specialist review agents for consulting deliverables, built with LangGraph-style fan-out, deployed on Cloudflare Workers/Pages via Wrangler CLI). Trigger on requests like "give me the kickoff prompt", "I need the context file for Pi", "regenerate CONTEXT.md", "how do I deploy DeliverableQA", or any mention of DeliverableQA, the agentathon project, or Pi setup for this project. Always produce the CONTEXT.md file and a compact copy-paste kickoff prompt together, even if only one is explicitly requested, since Pi needs both.
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
- Dashboard: single-page HTML app with an upload -> processing -> results flow
  (drag-and-drop a deliverable, watch it process, see the report) + Chart.js.
  The processing screen polls, it doesn't block on one long request.
- Server: server.py (FastAPI + uvicorn). POST /api/analyze returns a job_id
  immediately (HTTP 200, {"status": "processing"}) and runs the pipeline as a
  background asyncio task; GET /api/jobs/{job_id} is polled for status/result.
  This is what makes concurrent uploads work — don't make /api/analyze block
  until the pipeline finishes. Calls the exact same orchestrator/agents
  functions as run_qa.py — don't fork the pipeline logic for the web path.
- Auth: optional, opt-in via DELIVERABLEQA_TOKEN env var (see auth.py). Unset
  means no auth at all (the default for local single-user use); set it and
  every /api/* request needs Authorization: Bearer <token>. Never make auth
  mandatory — that breaks the zero-friction local default.
- Merge strategy: default is deterministic (orchestrator/merge.py, difflib
  similarity + location match, no LLM call). Optional LLM-driven merge
  (orchestrator/llm_merge.py, --llm-merge on the CLI or llm_merge=true on
  /api/analyze) calls Claude with prompts/orchestrator.md's PHASE 2
  instructions to catch semantic duplicates worded very differently across
  agents — it MUST fall back to the deterministic merge on any failure
  (API error, malformed response, validation failure), never raise.
- Delta/re-run mode: orchestrator/merge.py's compute_delta() compares two
  runs' findings (matched by location + description similarity — re-runs get
  fresh finding ids, matching by id never works) into resolved/still_open/new.
  Wired via run_qa.py --previous-findings <path> and the previous_findings
  upload field on /api/analyze.
- Structured output: this Bedrock route doesn't support output_config.format
  or strict tool schemas, and a $ref/$defs Pydantic schema makes the model
  unreliably stringify nested fields. Use forced tool-use (tool_choice) with
  a $ref-inlined flat schema, plus a small JSON-repair step for the residual
  cases — see agents/schema.py for the working pattern before reimplementing
  (llm_merge.py reuses the same pattern for its own schema).

Scaffold now:
1. Python venv + requirements.txt (langgraph, anthropic[bedrock], boto3,
   python-docx, python-pptx, pymupdf, pyyaml, fastapi, uvicorn[standard],
   python-multipart, pytest, pytest-asyncio)
2. orchestrator/ — parse.py (raise a clear error on empty/corrupt input,
   don't return an empty section list silently), dispatch.py, merge.py
   (dedup + compute_delta), llm_merge.py (opt-in, falls back to merge.py)
3. agents/ — one .py file per agent from CONTEXT.md's prompts, each calling the
   shared JSON finding schema via a forced tool-use call
4. config/checklists/*.yaml and config/style_rules.yaml
5. dashboard/ — single-page HTML app (upload/processing/results states),
   processing screen polls GET /api/jobs/{id} rather than blocking + Chart.js
6. server.py — FastAPI app, job-queue POST /api/analyze + GET /api/jobs/{id},
   serves dashboard/ as static root; run_qa.py stays as the CLI entry point,
   both call the same run() function
7. auth.py — optional bearer-token check, opt-in via DELIVERABLEQA_TOKEN
8. tests/ — pytest suite covering parse.py edge cases, merge.py dedup/delta,
   the JSON-repair logic, and server.py's job lifecycle + auth — mock the
   Bedrock client throughout, no live API calls in the test suite
9. Commit scaffold once it runs end-to-end on a sample doc, via both the CLI
   and a browser upload, with the test suite green

Ask me before adding any dependency beyond the ones listed above. Otherwise proceed
autonomously through the scaffold.
```

## When to give deployment/environment setup

If Mat asks about WSL setup, `gh`, `wrangler login`, provisioning R2/D1/Pages, or the deploy loop — read `references/deployment-setup.md` and answer from it rather than reconstructing commands from memory (exact flags matter for reproducibility across the team's machines).

## Notes

- This skill is scoped to this one project — don't generalize the prompt template to other hackathon ideas unless Mat explicitly asks for that.
- If Mat says the architecture changed (e.g. switching off Cloudflare, adding a new agent), update `assets/CONTEXT.md` in place so future regenerations stay current — don't let the bundled copy drift from what he's actually building.

