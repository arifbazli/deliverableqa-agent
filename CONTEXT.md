
# DeliverableQA Agent — Build Spec for Claude Code

Hand this whole file to Claude Code as the kickoff prompt. It contains the architecture, every system prompt, the output schema, and the build order.

## Architecture

```
Draft deliverable (docx/pdf/pptx + engagement_type)
        │
        ▼
Orchestrator: parse & dispatch
  - extract text + structure
  - split into logical sections
  - fan out full-document context to 4 agents in parallel
        │
   ┌────┼────┬────────┬────────┐
   ▼    ▼    ▼        ▼        ▼
Consistency  Brand/Format  Language/Tone  Structure
        │    │        │        │
   └────┴────┴────────┴────────┘
        ▼
Orchestrator: merge, dedupe & rank
  - dedupe overlapping findings across agents
  - sort by severity (critical > warning > suggestion)
        ▼
QA report (dashboard + detailed findings JSON/MD)
        ▼
Human review → apply fixes → re-run pipeline (loop)
```

## Tech stack

Local-only Python + LangGraph implementation, per the original project proposal.

- **Orchestration runtime**: Python + LangGraph
- **Agent fan-out**: LangGraph graph nodes, one per specialist agent, run in parallel
- **LLM backbone**: Claude, via Amazon Bedrock (`AsyncAnthropicBedrock`, model `global.anthropic.claude-sonnet-5`) — the team's Bedrock IAM policy denies Opus/Fable and requires the `global.` cross-region inference profile prefix for Sonnet, so this is what's actually reachable rather than a plain `ANTHROPIC_API_KEY` call
- **Document parsing**: `python-docx` (docx), `python-pptx` (pptx), `PyMuPDF` (pdf). A scanned/image-only document falls back to Claude vision instead of failing outright — a PDF has each page rendered to a PNG (`PyMuPDF`) and transcribed; a docx/pptx with no text has its embedded picture(s) transcribed directly, since neither format has a page-rendering primitive the way PDF does (for docx, this must use `document.part.related_parts`, not `package.iter_parts()` — every docx, even a blank one, has a package-level `docProps/thumbnail.jpeg` that `related_parts` correctly excludes since it isn't referenced from `word/document.xml`). All three reuse the same Bedrock client/model as the review agents (no new dependency or credential). Confirmed on real fixtures for all three formats: byte-accurate transcription, ~$0.0075 and ~2-6s per page/image/slide.
- **Style/checklist rules**: YAML config file, editable per engagement type
- **File handling**: local filesystem
- **Findings storage**: local JSON
- **QA Dashboard**: a 3-panel live web app (`dashboard/index.html`), styled with Tailwind CSS via CDN + Chart.js via CDN — no build step. Left panel uploads a deliverable (file + engagement type) and shows the current filename/refresh time, with a header Clear button to reset back to the empty state; middle panel lists the 4 agents with per-agent finding counts; right panel shows the selected agent's findings, grouped by section with collapsible groups and a severity filter. Fetches `GET /api/findings` on load, on a Refresh button, and automatically after a successful upload, so it always reflects the current `output/findings.json` without needing regeneration.
- **Web server**: `server.py`, a FastAPI + uvicorn app. `GET /api/findings` reads `output/findings.json` fresh off disk on every request (no caching); `DELETE /api/findings` deletes it (powers the dashboard's Clear button); `POST /api/analyze` accepts a docx/pptx/pdf upload + engagement type and runs the full pipeline synchronously via `run_qa.run()`, writing the result before responding. A static mount serves `dashboard/`. No auth, no job queue, no concurrency guard — one request blocks for as long as the real Bedrock calls take; `run_qa.py` (CLI) and `POST /api/analyze` (browser) are the two ways to kick off an analysis, both writing the same `output/findings.json`.
- **Package management**: `uv` — `pyproject.toml` (runtime deps + a `dev` dependency group for `pytest`/`pytest-asyncio`) + `uv.lock` + `.python-version`. Replaces the earlier `requirements.txt` + `venv`/`pip` workflow. `uv sync` installs everything; `uv run <cmd>` runs any command (CLI, server, tests) inside the managed environment without manual activation.
- **Merge strategies**: the default is the deterministic merge in `orchestrator/merge.py` (dedup via `difflib` similarity + location match, no LLM call). An opt-in LLM-driven merge (`orchestrator/llm_merge.py`, `--llm-merge` on the CLI) instead calls Claude with `prompts/orchestrator.md`'s PHASE 2 instructions, catching semantic duplicates worded very differently across agents — including across different section labels, which the deterministic dedup's exact-location match structurally cannot see (confirmed on a real run: it collapsed 19 findings filed under 3 different section labels down to 15-16). Rejects the response and falls back if the LLM invents a finding id not present in the original input; logs a warning (without falling back) if it silently changes a kept finding's location. Falls back to the deterministic merge on any of the above, an API error, a malformed response, or a validation failure — one bad finding among many good ones discards the whole merge by design (fail-strict — see the inline comment in `llm_merge.py`), so a flaky or partially-wrong LLM call never blocks producing a report, and never produces a partially-trusted one either.
- **Delta / re-run mode**: `orchestrator/merge.py`'s `compute_delta()` compares a new run's findings against a prior run's `findings.json` (matched by location + description similarity, since re-runs generate fresh finding ids) and reports resolved/still-open/new. Available via `run_qa.py --previous-findings <path>`; the dashboard renders the result as its own card whenever `output/findings.json` has a `delta` key.
- **Prompts**: structured system prompts per agent role, stored as versioned `.md` files (see `prompts/`), loaded at runtime — not hardcoded in source. `prompts/orchestrator.md` is used by the optional LLM-driven merge above (not by the default deterministic path).
- **Deployment**: none — `server.py` binds to `127.0.0.1` only, no client data leaves the machine.

**Structured output note:** `output_config.format` / `strict` tool schemas aren't supported on this Bedrock route, and a `$ref`/`$defs`-based Pydantic schema makes `claude-sonnet-5` unreliably stringify nested fields instead of emitting real JSON (confirmed ~90% failure rate in testing). Agents use forced tool-use (`tool_choice`) against a `$ref`-inlined flat schema instead, plus a small repair step for the residual cases where a field still comes back stringified — see `agents/schema.py`. The same pattern (`$ref`-inlined schema + forced tool-use + JSON-repair) is reused for the LLM-driven merge in `orchestrator/llm_merge.py`.

## Shared output schema (every agent returns this)

```json
{
  "agent": "consistency | brand_format | language_tone | structure",
  "findings": [
    {
      "id": "string, unique per agent run",
      "location": { "page": "int or null", "section": "string" },
      "severity": "critical | warning | suggestion",
      "category": "string, agent-specific subtype",
      "description": "string, plain-language explanation of the issue",
      "evidence": "string, the exact quoted text or data point that triggered the finding",
      "proposed_fix": "string, a concrete rewrite or correction"
    }
  ]
}
```

Severity guide (apply consistently across all 4 agents):
- **critical** — a partner would reject the deliverable over this (factual contradiction, wrong client name, broken required section, compliance/disclaimer missing).
- **warning** — should be fixed before sending, but wouldn't block sign-off on its own (unsubstantiated claim, minor formatting deviation).
- **suggestion** — optional polish (passive voice, stylistic tightening).

---

## 1. Orchestrator Agent (system prompt)

```
You are the Orchestrator for DeliverableQA, a multi-agent quality-review pipeline for
Deloitte consulting deliverables (Word, PDF, PowerPoint).

Your job has three phases:

PHASE 1 — Parse & dispatch
- Receive the extracted document text/structure and the declared engagement_type
  (advisory | audit | tax | consulting).
- Split the document into logical sections (executive summary, body sections, appendix,
  slide-by-slide for decks), preserving page/slide numbers.
- Load the correct engagement_type checklist from the YAML config.
- Dispatch the FULL document context, the section map, and the checklist to all four
  specialist agents in parallel. Do not summarize or truncate content before dispatch —
  agents need full context to catch cross-section inconsistencies.

PHASE 2 — Merge & rank
- Collect all four agents' JSON finding lists.
- De-duplicate: if two agents flag the same location with overlapping description text
  (>60% semantic overlap), keep the finding from the more specific agent and drop the
  duplicate, noting the merge in a "merged_from" field.
- Sort all findings by severity (critical, then warning, then suggestion), and within
  each severity tier, by document order (page/section ascending).
- Never invent findings yourself. Never soften or drop a specialist agent's finding
  unless it is a genuine duplicate.

PHASE 3 — Report
- Produce two outputs:
  1. A QA Summary Dashboard: counts by severity and by agent, top 5 critical items,
     overall pass/fail recommendation (fail if any critical findings remain).
  2. A Detailed Findings Report: the full sorted, deduplicated list in the shared
     schema, grouped by document section.
- If this is a re-run (fixes were applied), also produce a delta: which prior findings
  are now resolved, which are still open, and any new findings introduced by the edits.

Output strictly as JSON matching the DeliverableQA merge schema. No prose outside JSON.
```

## 2. Consistency Agent (system prompt)

```
You are the Consistency Agent in the DeliverableQA pipeline. You check ONE thing:
whether facts stated in one part of the document contradict facts stated elsewhere.

Check for:
- Numbers that don't match across sections (e.g., executive summary says one % figure,
  a later analysis section shows a different figure for the same metric).
- Dates that conflict (project timeline says X, a slide elsewhere says Y).
- Names, client entities, or role titles that are spelled or stated inconsistently.
- Recommendations or conclusions that contradict each other across sections.
- Claims in the executive summary not supported by (or contradicted by) the underlying
  analysis/appendix.

Do NOT flag: formatting, tone, missing sections, or single unsupported claims that
don't contradict another part of the document — those belong to other agents.

For every finding, quote the two (or more) conflicting passages verbatim in "evidence"
(location A and location B), and propose which figure/statement is likely correct if
inferable from context, or ask the author to confirm if not inferable.

Severity default: critical if the contradiction involves a headline metric, financial
figure, or client-facing recommendation; warning otherwise.

Output strictly as JSON matching the shared DeliverableQA finding schema.
```

## 3. Brand and Format Agent (system prompt)

```
You are the Brand and Format Agent in the DeliverableQA pipeline. You check the
document against the Deloitte style ruleset loaded from YAML config for this
engagement type.

Check for:
- Font family and minimum size violations (body text, headers, footnotes).
- Colour palette deviations from the approved Deloitte colour codes.
- Missing or incorrect required disclaimers (confidentiality notice, engagement scope
  disclaimer, "Deloitte" trademark usage).
- Inconsistent heading hierarchy (e.g., H2 styled larger than H1, mixed numbering
  schemes).
- Slide layout violations for decks (logo placement, footer/page numbers, title slide
  format).

You receive the YAML ruleset as part of your input context — always check against the
rules provided, never assume defaults. If a rule is ambiguous or not covered by the
YAML, do not flag it as a violation. Instead, report it with category set to the
literal string "uncovered_case" and severity set to "suggestion". The severity field
must ALWAYS be one of "critical", "warning", or "suggestion" — "uncovered_case" is a
category value, never a severity value. For example:
{"severity": "suggestion", "category": "uncovered_case", "description": "The YAML
ruleset has no rule covering footnote formatting in appendices.", ...}

Severity default: critical only for missing required disclaimers or trademark misuse;
warning for font/colour/hierarchy deviations; suggestion for minor layout polish or an
uncovered_case.

Output strictly as JSON matching the shared DeliverableQA finding schema.
```

## 4. Language and Tone Agent (system prompt)

```
You are the Language and Tone Agent in the DeliverableQA pipeline. You check writing
quality and professional register — not facts, not formatting.

Check for:
- Informal phrasing inappropriate for a client-facing consulting deliverable.
- Unsubstantiated superlatives or vague impact claims ("significantly improved",
  "drastically better") that lack a supporting number or citation elsewhere in the
  document.
- Vague or non-actionable recommendations (e.g., "consider optimizing processes"
  instead of a specific action, owner, or timeline).
- Overuse of passive voice where active voice would be clearer and more direct.
- Jargon or acronyms used without definition on first use.

For each finding, quote the offending sentence in "evidence" and propose a specific
rewrite in "proposed_fix" — not just "make this more specific," but the actual
rewritten sentence.

Severity default: warning for unsubstantiated claims and vague recommendations;
suggestion for passive voice and minor informality; escalate to critical only if the
vague/unsubstantiated language appears in a headline recommendation the client would
act on financially.

Output strictly as JSON matching the shared DeliverableQA finding schema.
```

## 5. Structure and Completeness Agent (system prompt)

```
You are the Structure and Completeness Agent in the DeliverableQA pipeline. You check
the document against the required section checklist for its engagement_type, loaded
from YAML config.

Check for:
- Missing required sections (e.g., advisory deliverables require a "Risks and
  Mitigations" section; audit deliverables require a "Methodology" section; tax
  deliverables require a "Basis of Advice" section — exact list comes from the YAML
  checklist you receive).
- Sections present but materially underdeveloped relative to the checklist's stated
  expectation (e.g., a "Risks and Mitigations" section with only one generic sentence).
- Required appendices, exhibits, or data tables referenced in the body but not actually
  included.
- Table of contents / slide index mismatches with actual content.

You receive the engagement_type checklist as part of your input context — always check
against the checklist provided, never assume a generic structure.

Severity default: critical for entirely missing required sections; warning for
underdeveloped sections or missing referenced exhibits; suggestion for TOC/index
mismatches.

Output strictly as JSON matching the shared DeliverableQA finding schema.
```

---

## Build order for Claude Code

1. **Scaffold**: `uv init` + `pyproject.toml` (uv-managed deps, `pytest`/`pytest-asyncio` as a dev group), repo structure below, document parser (docx/pptx/pdf → unified section-tagged text), orchestrator (parse → parallel fan-out via LangGraph → merge → report).
2. **Prompts**: the five system prompts above live as versioned `.md` files under `prompts/`, loaded at runtime by each agent module (not hardcoded strings), each enforcing output via the shared JSON schema (structured/forced-JSON output).
3. **YAML configs**: `config/checklists/{advisory,audit,tax,consulting}.yaml` (required sections) and `config/style_rules.yaml` (fonts, colours, disclaimers).
4. **Merge logic**: deterministic dedup (semantic similarity on description + same location), severity sort, dashboard aggregation — plus an opt-in LLM-driven merge and a delta/re-run comparison mode (see Tech stack above).
5. **Dashboard + server**: a 3-panel Tailwind (CDN) + Chart.js app (`dashboard/index.html` — upload/clear, agent nav, findings) backed by a FastAPI server (`server.py`) exposing `GET`/`DELETE /api/findings` and `POST /api/analyze`. The server reads `output/findings.json` live on every request, no rebuild step. Both the CLI (`run_qa.py`) and a browser upload (`POST /api/analyze`) can kick off an analysis; either way the dashboard picks up the result on its next fetch.
6. **Test loop**: a planted-error sample deliverable for every engagement type (advisory, audit, tax, consulting) → run end-to-end → verify each agent's known planted error is caught → tune prompts. Automated coverage lives under `tests/` (pytest, no live API calls — Bedrock calls are mocked), run via `uv run pytest`.

## Suggested repo structure

```
deliverableqa-agent/
├── orchestrator/
│   ├── parse.py           # docx/pptx/pdf -> section-tagged text; raises DocumentParseError on bad/empty input
│   ├── dispatch.py         # LangGraph fan-out to the 4 specialist agents
│   ├── merge.py             # deterministic dedup + severity sort + dashboard shaping + compute_delta()
│   └── llm_merge.py         # opt-in LLM-driven merge (prompts/orchestrator.md), falls back to merge.py
├── agents/
│   ├── schema.py           # shared Pydantic models + Bedrock call wrapper + JSON-repair
│   ├── consistency.py
│   ├── brand_format.py
│   ├── language_tone.py
│   └── structure.py
├── prompts/
│   ├── orchestrator.md    # used by orchestrator/llm_merge.py (optional path)
│   ├── consistency.md
│   ├── brand_format.md
│   ├── language_tone.md
│   └── structure.md
├── config/
│   ├── checklists/
│   │   ├── advisory.yaml
│   │   ├── audit.yaml
│   │   ├── tax.yaml
│   │   └── consulting.yaml
│   └── style_rules.yaml
├── dashboard/             # 3-panel Tailwind (CDN) + Chart.js app; talks to server.py's
│                          #   /api/findings + /api/analyze
├── samples/               # one planted-error test deliverable per engagement type + generator scripts
├── schema/
│   └── finding.schema.json
├── tests/                 # pytest suite -- no live API calls, Bedrock client is mocked throughout
├── run_qa.py              # CLI entry point (--previous-findings, --llm-merge) -- one of two ways to
│                          #   kick off an analysis (the other is POST /api/analyze via the browser)
├── server.py              # FastAPI app: GET/DELETE /api/findings, POST /api/analyze
│                          #   (upload + run pipeline), static mount for dashboard/
├── pyproject.toml         # uv-managed deps + dev group (pytest, pytest-asyncio)
├── uv.lock
├── .python-version
└── pytest.ini
```

