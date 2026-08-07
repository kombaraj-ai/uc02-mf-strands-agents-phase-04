# AMC RFP & Portfolio Insight Orchestrator — Interview Prep Notes

Personal cheat-sheet for explaining this project in an interview. Kept short and
talking-point-driven on purpose — not a technical reference (see
`docs/architecture.md` for that).

---

## 30-second pitch

> "I built a multi-agent AI system for an asset management company that
> generates institutional RFP responses — combining fund performance data and
> manager commentary — and puts every draft through an automated compliance
> review before it goes out, with a self-correcting revise loop instead of a
> human bottleneck. It's built on AWS Bedrock AgentCore, deployed with
> Terraform and a full CI/CD pipeline, and I designed it to run identically on
> a free local LLM for development and on Bedrock for production."

---

## The problem

Institutional RFP responses at an asset management company need two things
that don't naturally come from one source:

- **Quantitative**: NAV, Alpha, Beta, Sharpe ratio, trailing returns — hard data.
- **Qualitative**: manager strategy commentary, macro positioning — narrative.

Both then need **compliance sign-off** before they reach a client — regulated
firms can't let an LLM say "guaranteed returns" or invent a track record.
Doing this by hand doesn't scale; doing it with one unsupervised LLM call is a
compliance risk (hallucination, overclaiming, no audit trail).

## Why agentic AI (not just one prompt)

- **Separation of concerns**: one agent pulls quant data, another pulls
  qualitative commentary, a third — independent of the first two — judges the
  combined draft against a written compliance rubric. The judge never drafts,
  it only evaluates, so it isn't biased by its own writing.
- **Self-correction loop**: if the compliance judge rejects a draft, specific
  feedback routes back to a revision step, which redrafts and resubmits — up
  to a capped number of attempts. This mirrors a real draft → review → revise
  workflow instead of a single unreviewed generation.
- **Graceful degradation over silent failure**: if compliance still can't be
  confirmed after the attempt budget, the system escalates to a fixed,
  honest holding message instead of ever returning a non-compliant answer or
  crashing. Two independent termination layers guarantee this — one graceful,
  one a hard safety ceiling.
- **Tool use, not fabrication**: agents only state facts pulled from real tool
  calls (fund database, knowledge base) — never from parametric memory.

## Tech stack

| Layer | Choice |
|---|---|
| Agent orchestration | **Strands Agents** (graph-based multi-agent framework) |
| LLM | **Ollama** (local dev, free) / **Amazon Bedrock** (Claude / Nova) — config-switch only, zero code change |
| Cloud agent hosting | **Amazon Bedrock AgentCore** — Runtime, Gateway, Memory |
| Data | SQLite + ChromaDB (dev) → **DynamoDB** + **Bedrock Knowledge Base** (cloud) |
| API / UI | **FastAPI** REST API, **Streamlit** demo UI |
| Infra as Code | **Terraform**, 3 isolated environments (dev/staging/prod) |
| CI/CD | **GitHub Actions**, OIDC federated AWS auth (no long-lived keys) |
| Auth | IAM / SigV4 throughout — no static credentials in the app |
| Observability | structlog (JSON), CloudWatch |
| Quality | pytest (unit + integration), mypy, ruff |

## Architecture, one paragraph

Five agents as nodes in a graph: `quant_data_pull` and `qual_narrative_pull`
run in parallel, feed into `compliance_check` (an LLM-as-a-judge with
structured, rubric-based output), which routes to either `revise_draft`
(loops back to another compliance check) or `final_synthesis` — gated by
routing logic, never by chance. The judge and the drafting agents are
deliberately different roles even when they share a model, so review stays
independent of drafting.

---

## What each phase delivered

**Phase 01 — Core agentic system.** Designed and built the 5-agent compliance
graph end-to-end, running entirely locally (Ollama + SQLite/ChromaDB). REST
API, CLI, full test suite, documentation. Proved the architecture before
spending any cloud budget.

**Phase 02 — Cloud deployment.** Moved the same application, unchanged in
logic, onto **Amazon Bedrock AgentCore Runtime** — containerized, deployed via
Terraform, backed by DynamoDB and a Bedrock Knowledge Base for real
retrieval-augmented generation instead of local mock stores.

**Phase 03 — CI/CD.** Built a GitHub Actions pipeline with OIDC-based AWS
authentication (no stored AWS keys), automatic validation on every PR, and a
manual, deliberate **build-once / promote-the-same-image** deployment model
across dev → staging → prod, so what's tested is exactly what ships.

**Phase 04 — Advanced agentic capabilities.**
- **Gateway-routed tools**: agents call their data tools through a governed
  **AgentCore Gateway** (MCP protocol, IAM-authenticated) instead of in-process
  functions — a step toward tool governance and reuse across agents.
- **AgentCore Memory**: real multi-turn conversational continuity — a second
  question can reference "that fund" from an earlier turn and resolve
  correctly.
- **Compliance grounding fix**: closed a real hallucination gap — the
  narrative agent would occasionally invent commentary when retrieval
  returned nothing; fixed at the code layer, not just the prompt, plus a new
  rubric rule as a second line of defense.
- **Policy-based authorization (explored)**: prototyped Cedar-based,
  fine-grained tool authorization on the Gateway — built and IaC-validated,
  parked pending an AWS-side platform issue.

---

## Strong interview talking points

- **Independent judge pattern**: the compliance checker is architecturally
  separate from the agents that draft — same technique used for LLM-as-a-judge
  evaluation pipelines, applied here as a production safety gate, not just an
  offline eval.
- **Never fail unsafe**: two-layer termination (soft attempt budget + hard
  ceiling) means the system either returns a compliant answer or a clearly
  labeled escalation — never a crash, never a silent bad answer.
- **Provider abstraction done right**: switching the entire system from a free
  local LLM to a production cloud model is a **one config value**, not a code
  change — proved out by running the identical test suite against both.
- **Debugged framework/AWS behavior directly from source**, not blog posts —
  e.g. found and fixed a real multi-agent graph scheduling bug by reading the
  installed framework's source rather than trusting docs; confirmed several
  AWS IAM action names only via live `AccessDenied` errors, not assumptions.
- **Root-caused rather than patched**: traced an intermittent structured-output
  failure to the local LLM provider silently ignoring a forcing parameter, and
  made a documented decision to accept it as a known dev-only limitation
  rather than over-engineer around it — production (Bedrock) doesn't have the
  issue.
- **Security-first cloud design**: IAM/SigV4 everywhere, least-privilege
  per-environment roles, OIDC for CI (no long-lived AWS keys in GitHub).

## Anticipated questions

**"Why multiple agents instead of one big prompt?"**
Separation of concerns and independent review — a single agent grading its
own work is a weaker compliance control than a dedicated, separately-prompted
judge with a written rubric and structured output.

**"How do you prevent hallucination?"**
Tool-grounded answers only, an explicit rubric rule against fabrication, and
a code-level guardrail that forces an honest "not found" response when
retrieval genuinely returns nothing — enforcement isn't left to the prompt
alone.

**"What happens if the AI can't produce a compliant answer?"**
It escalates to a fixed, honest holding message after a capped number of
revise attempts — the system is designed to never return an unreviewed or
non-compliant answer, and never to crash instead of responding.

**"How would this scale / go to production?"**
It already runs on managed, serverless AWS infrastructure (AgentCore Runtime,
DynamoDB, Bedrock) provisioned by Terraform, with a CI/CD pipeline for
promotion across environments — the local Ollama path is dev-only, by design.

**"What would you do differently / improve next?"**
Finish rolling out staging/prod, add an automated integration test for the
Memory round-trip (currently manually verified), and revisit the Cedar policy
layer once the platform-side blocker clears.
