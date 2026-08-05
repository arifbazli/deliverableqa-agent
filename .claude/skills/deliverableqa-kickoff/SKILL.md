
---
name: deliverableqa-kickoffa
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

Build target: Cloudflare Workers + Pages, TypeScript, Bun runtime, deployed via
Wrangler CLI (already authenticated). Not Python — rewrite the pipeline in TS.

Stack mapping:
- Orchestrator: Worker that fans out 4 parallel fetch() calls to the LLM API
  (plain Promise.all is enough for v1)
- Durable Object: one per QA run, holds in-flight state, powers a live dashboard
- R2: uploaded deliverables (docx/pdf/pptx)
- D1: merged findings storage
- Parsing: mammoth (docx), jszip (pptx), unpdf or pdfjs-dist (pdf)
- Config: YAML checklists/style rules as static assets, parsed with js-yaml
- Dashboard: Astro + Chart.js on Cloudflare Pages

Scaffold now:
1. bun create cloudflare@latest . (Worker, TS)
2. wrangler.toml with R2 bucket, D1 database, and Durable Object bindings
   (use existing: deliverableqa-uploads / deliverableqa-findings)
3. src/orchestrator/ — parse.ts, dispatch.ts, merge.ts
4. src/agents/ — one .ts file per agent from CONTEXT.md's prompts, each calling
   the shared JSON finding schema
5. dashboard/ — Astro app, Pages-deployable, renders merged findings
6. Commit scaffold, then run `wrangler deploy --dry-run` to confirm bindings
   resolve before first real deploy

Ask me before provisioning any new Cloudflare resources beyond the two already
created. Otherwise proceed autonomously through the scaffold.
```

## When to give deployment/environment setup

If Mat asks about WSL setup, `gh`, `wrangler login`, provisioning R2/D1/Pages, or the deploy loop — read `references/deployment-setup.md` and answer from it rather than reconstructing commands from memory (exact flags matter for reproducibility across the team's machines).

## Notes

- This skill is scoped to this one project — don't generalize the prompt template to other hackathon ideas unless Mat explicitly asks for that.
- If Mat says the architecture changed (e.g. switching off Cloudflare, adding a new agent), update `assets/CONTEXT.md` in place so future regenerations stay current — don't let the bundled copy drift from what he's actually building.

