
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

## Tech stack (Cloudflare-native, TypeScript)

- **Runtime**: Cloudflare Workers (Bun locally, Wrangler for deploy)
- **Orchestration**: plain `Promise.all()` fan-out to 4 parallel LLM calls (langgraphjs optional for v2)
- **Durable Objects**: one per QA run, holds in-flight state, powers a live dashboard
- **R2**: uploaded deliverables (docx/pdf/pptx)
- **D1**: merged findings storage
- **Parsing**: `mammoth` (docx), `jszip` (pptx), `unpdf` or `pdfjs-dist` (pdf)
- **Config**: YAML checklists/style rules as static assets, parsed with `js-yaml`
- **Dashboard**: Astro + Chart.js on Cloudflare Pages
- **LLM backbone**: configurable via `LLM_PROVIDER` env var — `claude` | `openai` | `ollama` | `workers-ai`. Agents call whichever endpoint is set; switching providers is a config change, not a rewrite. Defaults to `claude` for build/demo, with `ollama` (self-hosted) or Cloudflare's own `workers-ai` binding available if data-handling concerns favour a non-API-vendor model.

### Proposal doc → actual implementation

The original project proposal specified a Python/local stack. The team moved to a Cloudflare-native TypeScript stack for live deployment (matching the agentathon's cloud-deployment theme) instead of running locally. Mapping for reference:

| Component | Proposal doc | This implementation |
|---|---|---|
| Orchestrator & agents | Python + LangGraph | TypeScript Worker, `Promise.all()` fan-out (langgraphjs optional later) |
| LLM backbone | GPT-4o / Claude via API | `LLM_PROVIDER` config — Claude, OpenAI, Ollama, or Workers AI |
| Document parsing | python-docx, python-pptx, PyMuPDF | mammoth (docx), jszip (pptx), unpdf/pdfjs-dist (pdf) |
| Style rules | YAML config file | Same — YAML, parsed with js-yaml |
| QA dashboard | HTML + Chart.js, single-page, local | Astro + Chart.js, deployed to Cloudflare Pages |
| Prompts & instructions | Structured system prompts, versioned `.md` files | Same — see the 5 prompts below, versioned in this file and `src/agents/*.ts` |
| Deployment | None specified — runs locally, no client data leaves the machine | Cloudflare Workers + Pages via Wrangler CLI; provider choice above still lets you avoid third-party API vendors if needed |

This directly answers proposal discussion question #2 ("Azure OpenAI vs local Ollama + Llama 3 to avoid data-handling concerns") — both are now just a `LLM_PROVIDER` value away, no architecture change required.

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
YAML, do not flag it; note it as an "uncovered_case" instead.

Severity default: critical only for missing required disclaimers or trademark misuse;
warning for font/colour/hierarchy deviations; suggestion for minor layout polish.

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

1. **Scaffold**: repo structure below, document parser (docx/pptx/pdf → unified section-tagged text), orchestrator (parse → parallel fan-out → merge → report).
2. **Prompts**: drop the five system prompts above into `src/agents/*.ts`, each calling the shared JSON schema (enforce via structured output / JSON mode).
3. **YAML configs**: `config/checklists/{advisory,audit,tax,consulting}.yaml` (required sections) and `config/style_rules.yaml` (fonts, colours, disclaimers).
4. **Merge logic**: dedup (semantic similarity on description + same location), severity sort, dashboard aggregation.
5. **Dashboard**: Astro app + Chart.js, reads merged JSON from D1, renders severity counts + top critical items + full findings table.
6. **Test loop**: 3 sample deliverables with planted errors (one per engagement type) → run end-to-end → verify each agent's known planted error is caught → tune prompts.

## Suggested repo structure

```
deliverableqa-agent/
├── src/
│   ├── orchestrator/
│   │   ├── parse.ts
│   │   ├── dispatch.ts
│   │   └── merge.ts
│   └── agents/
│       ├── consistency.ts
│       ├── brand_format.ts
│       ├── language_tone.ts
│       └── structure.ts
├── config/
│   ├── checklists/
│   │   ├── advisory.yaml
│   │   ├── audit.yaml
│   │   ├── tax.yaml
│   │   └── consulting.yaml
│   └── style_rules.yaml
├── dashboard/            # Astro app, deployed to Cloudflare Pages
├── samples/              # 3+ planted-error test deliverables
├── schema/
│   └── finding.schema.json
└── wrangler.toml         # R2 bucket, D1 database, Durable Object bindings
```

