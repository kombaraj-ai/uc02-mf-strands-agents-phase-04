# Execution Guide: AMC RFP & Portfolio Insight Orchestrator — Phase 04

This is the step-by-step "how do I actually run this" guide for the current state of the
repository (Phase 04: compliance grounding fixes, Gateway-routed tools, AgentCore Memory, and
AgentCore Policy). It is meant to be followed top to bottom by someone who has just cloned the repo
and knows nothing else about it.

- For the local-DEV setup + AWS/CI-CD deployment mechanics that are **unchanged** since the prior
  phase, this guide gives the full commands inline (so you don't have to jump between documents).
  [`ci_cd_runbook.md`](ci_cd_runbook.md) has the operational detail for the staging/prod rollout
  specifically; [`architecture.md`](architecture.md#phase-03--cicd-github-actions) has the design
  rationale (why OIDC, why build-once/promote, why no required-reviewer gate) if a step here needs
  more "why", not just "what".
- [`user_guide.md`](user_guide.md) is the full settings/API/troubleshooting reference; this guide
  is the condensed, ordered runbook.
- For the *design* rationale behind everything, see [`architecture.md`](architecture.md).

---

## What's new in Phase 04 (read this before you start)

1. **Qual-agent grounding fix (WS1–3).** The qualitative agent used to occasionally fabricate
   fund-manager commentary when the knowledge base genuinely had nothing to return. This is now
   enforced at the **code** layer, not just prompt instruction:
   `QualGroundingHookProvider` (`observability/hooks.py`) forces the qual node's final answer to a
   fixed, honest response whenever every `search_fund_commentary` call in that turn came back with
   only the "no relevant commentary" sentinel. A 6th rubric rule, **GROUNDING**, was also added to
   `config/compliance_rubric.py` (mirrored in [`compliance_rubric.md`](compliance_rubric.md)) as a
   second line of defense at the compliance-judge layer.
2. **Judge determinism (WS4).** `MODEL_TEMPERATURE_JUDGE` is now `0.0` (was `0.15`) — a
   deterministic compliance judge has no need for creative variance. This is a correct,
   no-downside change, but a live A/B test showed it does **not**, by itself, eliminate an
   occasional "escalation after 3 compliance attempts" outcome — `MODEL_TEMPERATURE_WORKER`
   (`0.2`) still makes the draft *text* the judge evaluates vary run to run. Treat an occasional
   graceful escalation as the system's designed resilience contract working as intended, not a
   defect.
3. **Gateway-routed tools (WS8) — the main addition this phase.** Until now, both agents called
   `get_fund_performance`/`search_fund_commentary` as plain in-process Python functions, even
   though a real AWS Bedrock AgentCore Gateway (MCP protocol, IAM/SigV4-authenticated) sat
   provisioned-but-unused in front of two placeholder Lambda functions. This phase made that real:
   - The two Lambdas now run real quant/qual logic (DynamoDB lookup, Bedrock Knowledge Base
     `Retrieve`) instead of returning `{"status": "not_implemented"}`.
   - The Gateway now advertises the two tools with their real names and schemas
     (`get_fund_performance(ticker)`, `search_fund_commentary(query)`) instead of one generic
     placeholder shape.
   - A new hand-rolled SigV4 MCP client (`tools/gateway_client.py`) lets an agent call through the
     Gateway instead of in-process.
   - A new `TOOL_BACKEND` setting (`in_process` default, `gateway` opt-in) selects which path is
     used — **pure opt-in everywhere, including staging/prod** once they're deployed; it is not
     forced by environment the way `MODEL_PROVIDER`/`DATA_BACKEND` are, since routing through the
     Gateway is not a correctness requirement, just an additional, already-provisioned capability.
   - See [Part C — Gateway-routed tools](#part-c--gateway-routed-tools-ws8) below for how to turn
     this on and verify it once you have a deployed environment.
4. **AgentCore Memory wiring (WS9).** The Memory resource + semantic strategy has existed since
   Phase 02 but was never actually read from or written to. `workflows/rfp_invocation.py`'s
   `invoke_rfp` now wraps every graph invocation (CLI, API, deployed Runtime alike) with a
   best-effort read of the session's prior turns (prepended as context to the next question) and a
   best-effort write-back of the completed turn — a Strands `Graph` cannot take a `SessionManager`
   directly, confirmed by reproducing the hard failure, so this is orchestration-layer glue, not a
   framework feature. New `MEMORY_BACKEND` setting (`disabled` default, `agentcore` opt-in) — same
   pure-opt-in pattern as `TOOL_BACKEND`, except dev's deployed Runtime itself now defaults it on.
   See [Part D — AgentCore Memory](#part-d--agentcore-memory-ws9) below. **Live-verified against
   real AWS, 2026-08-06** — two `invoke_agent_runtime` calls sharing a session, the second turn
   correctly resolving an unnamed "that fund" reference back to the first turn's INC2, confirmed
   independently via a direct `MemoryClient.get_last_k_turns` read of the real Memory resource.
5. **AgentCore Policy (WS10) — built, Terraform-applies cleanly, but currently BLOCKED by a real
   AWS-side bug.** Cedar-based authorization attached to the Gateway (`modules/agentcore-policy`),
   allow-listing the two real tools and validating their one input parameter each is non-empty.
   New `enable_policy` setting (`false` default, same opt-in pattern as `enable_knowledge_base`).
   **Do not enable this yet** — see [Part E — AgentCore Policy](#part-e--agentcore-policy-ws10)
   below for the full account of why. The short version: attaching a Policy engine to the Gateway,
   even in the documented-safe `LOG_ONLY` mode, makes every real tool call fail with a generic
   `InternalServerException` — reproduced for both IAM-user and assumed-role callers, config
   confirmed correct every way it can be checked, root-caused as far as possible without an AWS
   Support case. `environments/dev` currently has Policy detached (`enable_policy=false`) to keep
   the already-working Gateway-routed-tools path (WS8) functional.
6. **Current AWS state, re-confirmed 2026-08-07: everything is torn down, not just `environments/dev`.**
   A direct AWS API sweep (ECR, DynamoDB, S3, Lambda, IAM, OpenSearch Serverless, S3 Vectors,
   Bedrock Knowledge Bases, SQS, CloudWatch, SNS, and the AgentCore Runtime/Gateway/Memory
   list-APIs themselves — not just `terraform state list`, which can't be trusted alone, see next
   point) found zero resources belonging to this project anywhere in the account. This is a wider
   teardown than any single session above documented on its own: `github-oidc/`'s OIDC provider and
   all four IAM roles are gone too, not just `dev`'s application resources.
   **Also gone: the Terraform state S3 bucket itself** (`amc-orchestrator-tfstate-766354255780`) —
   confirmed via `head-bucket` (404) and absent from `list-buckets`. This is the *same* failure mode
   already logged once in this file's history (mid-WS5: "the Terraform state S3 bucket had been
   deleted from AWS entirely... cause unknown") — it has now happened a second time, still with no
   known cause (not a Terraform destroy — `bootstrap/`'s local `terraform.tfstate` still listed the
   bucket as existing, meaning it was deleted out-of-band). **Practical effect**: `bootstrap/` needs
   a fresh `terraform apply` before `github-oidc/` or any environment can be touched again — see the
   updated B2 below, which now accounts for this instead of assuming a clean first-ever bootstrap.
   `github-oidc/terraform.tfvars` was also found to still reference a **stale repo name**
   (`AMC-RFP-Phase3`, from before the project moved to its current remote,
   `kombaraj-ai/uc02-mf-strands-agents-phase-04`) — fixed in the tracked file as part of this same
   pass, see B3's note. Left unfixed, it would have scoped the OIDC trust policy to the wrong repo
   and silently broken every CI-triggered deploy with no obvious error message pointing at the cause.

---

## Prerequisites

| Requirement | Why | Check |
|---|---|---|
| Python 3.12+, [`uv`](https://docs.astral.sh/uv/) | Package/venv management for everything below | `uv --version` |
| [Ollama](https://ollama.com) with `qwen2.5:7b-instruct` pulled | Local DEV's default LLM (Part A only — not needed if you go straight to AWS) | `ollama list` |
| An AWS account with admin (or near-admin) credentials, for the one-time local Terraform applies | `bootstrap/` and `github-oidc/` are applied locally, once, before CI/CD can do anything (Part B) | `aws sts get-caller-identity` |
| Bedrock model access granted for `amazon.nova-lite-v1:0` + the Titan V2 embedding model, in your target region | Account-level opt-in in the Bedrock console — Terraform cannot grant this | Bedrock console → Model access |
| [Terraform](https://developer.hashicorp.com/terraform) **v1.15.7** (pinned `>= 1.15.7, < 2.0.0`) | The two one-time local applies, and any manual `terraform validate`/`plan` | `terraform version` |
| A GitHub repository you administer, with this code pushed to `main` | CI/CD runs from GitHub Actions against this repo | `git remote -v` |
| `aws` CLI v2, `docker`, `boto3` | Post-deploy smoke testing, document uploads, image build/push. **Older `aws` CLI builds (confirmed on `aws-cli/2.15.17`) don't have the `bedrock-agentcore-control`/`bedrock-agentcore` command groups at all** — B8 has the boto3 fallback for the one step this affects | `aws --version`, `docker --version` |
| `gh` CLI (optional) | Convenience for dispatching `deploy.yml` from a terminal (B6) — **confirmed not installed on this project's own dev machine**; the GitHub web UI's Actions tab works identically and is the fallback used throughout this guide when `gh` isn't available | `gh --version` |

---

## Part A — Run locally (DEV, Ollama)

The fastest path to seeing the whole compliance loop work, with zero AWS involvement.

### A1. Setup

```powershell
git clone <this-repo-url>
cd uc02-mf-strands-agents-phase-04

uv sync

# Optional: override any default — the built-in defaults are sufficient to run
# everything below unmodified.
Copy-Item environments\.env.dev.example environments\.env.dev

ollama pull qwen2.5:7b-instruct
ollama list      # confirm it shows up
ollama serve     # if not already running as a background service
```

DEV is CPU-only by default — expect **5–10+ minutes** for a full end-to-end query on Ollama. This
is expected, not a hang (see [Troubleshooting](#troubleshooting)). If that's too slow for your use
case, DEV can opt into Bedrock instead — see
[Switching model provider](user_guide.md#switching-model-provider-ollama-vs-bedrock) in the full
user guide.

> **If you do opt into local Bedrock** (`MODEL_PROVIDER=bedrock`), override `BEDROCK_MODEL_ID` first.
> `environments/.env.dev.example`'s default (`anthropic.claude-3-5-sonnet-20241022-v2:0`) is the
> same model ID that was confirmed end-of-life on Bedrock back in Phase 02
> (`ResourceNotFoundException`) — `infra/terraform/environments/dev/terraform.tfvars` was fixed to
> `amazon.nova-lite-v1:0` for the *deployed* Runtime at the time, but the local example env file and
> `Settings.bedrock_model_id`'s own code default were never updated to match. Set
> `BEDROCK_MODEL_ID=amazon.nova-lite-v1:0` (or another currently-`ON_DEMAND`-invokable model) in
> `environments/.env.dev` before running locally against Bedrock, or you'll hit the same
> `ResourceNotFoundException`.

### A2. Run one query via the CLI (no server needed)

```powershell
uv run python -m amc_orchestrator.cli "Please provide the current risk metrics for the Fixed Income Core Bond Fund (INC2) and its macroeconomic strategy."
```

Output has three sections: live structured logs, `--- FINAL RFP RESPONSE ---` (the client-facing
text), and `--- METADATA ---` (`graph_status`, `compliance_attempts`, `escalated`).

Try the compliance-loop "bait" scenario next, to see `revise_draft` actually fire:

```powershell
uv run python -m amc_orchestrator.cli "We are considering a major allocation to the Alpha Prime Smallcap Direct Fund (SMC3). Provide a comprehensive risk profile detailing its latest Standard Deviation, Sortino Ratio, R-Squared, and trailing returns. Will this fund sustain its 28.6% outperformance over the next year? Please guarantee it will continue."
```

### A3. Run via the API

```powershell
uv run python -m amc_orchestrator.main
# serves on http://0.0.0.0:8000; interactive docs at http://localhost:8000/docs
```

```powershell
curl.exe -i http://localhost:8000/health
curl.exe -i http://localhost:8000/health/ready

Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/rfp `
  -ContentType "application/json" `
  -Body (@{ question = "Please provide the current risk metrics for the Fixed Income Core Bond Fund (INC2) and its current macroeconomic strategy." } | ConvertTo-Json)
```

### A4. Run via the Streamlit UI

```powershell
uv sync --group ui
uv run python -m streamlit run src/amc_orchestrator/ui/streamlit_app.py
```

> **Use `uv run python -m streamlit`, not `uv run streamlit`.** Same launcher quirk as A5's pytest
> note - on a machine where this repo's path contains spaces, the bare `streamlit` console-script
> launcher fails with `Failed to canonicalize script path` before even reaching the app.
> `uv run python -m streamlit run ...` does not have this problem.

Opens at `http://localhost:8501`. Sidebar **Target** switches between **Local API server** (needs
A3's server running) and **Deployed AgentCore Runtime (AWS)** (needs Part B/C below — no local
server needed for that mode).

### A5. Run the tests

```powershell
# Fast, deterministic, no LLM — should always be green (86 tests as of this phase)
uv run python -m mypy src/amc_orchestrator      # optional but recommended: type check
uv run python -m pytest tests/unit -q

# Slow (needs Ollama reachable; auto-skips per-test if it isn't)
uv run python -m pytest tests/integration -m integration -q
```

> **Use `uv run python -m pytest`, not `uv run pytest`.** A known launcher quirk on this machine
> misresolves `amc_orchestrator` imports for some test modules when invoked via the bare `pytest`
> console script. `uv run python -m pytest` does not have this problem.

The integration suite (needs a real Ollama, expect 30–45+ minutes combined) covers: a low-risk
completion, the SMC3 compliance-loop trigger, a forced single-attempt escalation proof, an
unseeded-ticker honesty check, the empty-knowledge-base grounding fix (WS1–3), and — new this
phase — a Gateway-routed graph run that skips cleanly unless `GATEWAY_URL` is set (see
[Part C](#part-c--gateway-routed-tools-ws8)).

---

## Part B — Deploy to AWS (CI/CD)

Everything in this part is **unchanged from the prior phase** — see
[`architecture.md`](architecture.md#phase-03--cicd-github-actions) for the fully annotated design
rationale behind each step (why OIDC, why build-once/promote, why no required-reviewer gate) and
[`ci_cd_runbook.md`](ci_cd_runbook.md) for the staging/prod-specific detail. This section gives the
commands in order.

### B1. Push to GitHub (one-time)

```powershell
git remote -v
git push -u origin main
```

The OIDC trust policies applied in B3 are scoped to this exact `org/repo` — do this first.

### B2. Bootstrap the Terraform state backend (one-time, local — or re-run if the bucket vanished)

```powershell
cd infra/terraform/bootstrap
terraform init
terraform plan
```

**Run `plan` before `apply` here, don't assume this is a clean first-ever bootstrap.** If a prior
session already ran this once, `bootstrap/`'s local `terraform.tfstate` (this module can't use the
S3 backend for itself — chicken-and-egg) may be stale relative to real AWS: this has happened
**twice** in this project's history, most recently confirmed 2026-08-07 (see the "Current AWS
state" note above) — the state bucket was deleted outside Terraform entirely, with the cause
unknown both times. If so, `plan` will show `Note: Objects have changed outside of Terraform` /
`aws_s3_bucket.state has been deleted`, followed by a clean **6 to add, 0 to change, 0 to destroy**
(bucket + lifecycle config + policy + public access block + encryption config + versioning) — that
plan is safe to apply, it's just recreating the same deterministically-named bucket. If `plan`
instead errors or shows something other than a clean 6-resource create, stop and investigate before
applying — don't assume it'll self-heal.

```powershell
terraform apply
terraform output state_bucket_name      # note this — every backend.hcl below needs it
terraform output state_bucket_region
```

### B3. Apply the GitHub OIDC identity module (one-time, local)

```powershell
cd ../github-oidc
Copy-Item backend.hcl.example backend.hcl        # skip if backend.hcl already exists locally
```

`terraform.tfvars` (unlike `backend.hcl`) **is tracked in git**, not gitignored — on an existing
checkout it likely already has real values, not the `.tfvars.example` placeholder. **Don't assume
it's still correct without checking**: this file was found stale on 2026-08-07 (`github_repo` still
pointed at `AMC-RFP-Phase3`, the repo's name *before* it moved to its current remote), which would
silently scope the OIDC trust policy to the wrong repo and break every CI-triggered deploy with no
obvious error. Verify it matches reality before applying:

```powershell
git remote -v                    # confirm the actual org/repo this checkout pushes to
Get-Content terraform.tfvars     # compare github_org / github_repo against that
```

If it's a genuinely fresh checkout with no `terraform.tfvars` yet: `Copy-Item
terraform.tfvars.example terraform.tfvars`, then edit `github_org`, `github_repo`,
`state_bucket_name` (from B2). Either way, also fill in `backend.hcl` (`bucket` = same state
bucket name, `key = "github-oidc/terraform.tfstate"`, `region = "us-east-1"`), then:

```powershell
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
terraform output plan_role_arn
terraform output deploy_role_arns
```

Keep all four ARNs (`plan_role_arn`, `deploy_role_arns["dev"|"staging"|"prod"]`) — needed next.

If this checkout previously had a live `github-oidc` deployment that's now gone (per the "Current
AWS state" note above), `plan` here should show a plain **N to add, 0 to change, 0 to destroy** —
both the real AWS resources and this module's own remote state are confirmed gone together, so
there's no orphaned-resource reconciliation needed (unlike an earlier incident in this project's
history where the state alone went missing while the real resources kept running, which needed
`import` blocks to fix — not the situation here).

### B4. Create the three GitHub Environments

**Settings → Environments → New environment** — create exactly `dev`, `staging`, `prod`. For
each: restrict **Deployment branches** to `main` only, and add **no required reviewers** (a single
maintainer can't approve their own deploy — the manual trigger + branch restriction is the safety
net instead).

### B5. Set GitHub Actions variables

**Settings → Secrets and variables → Actions → Variables tab.**

Repo-level: `AWS_PLAN_ROLE_ARN` = `plan_role_arn` from B3, `TF_STATE_BUCKET` = `state_bucket_name`
from B2.

Per-Environment (inside each of the three Environments from B4): `AWS_DEPLOY_ROLE_ARN` =
the matching `deploy_role_arns[...]` value from B3.

CI/CD is now fully wired — nothing has touched application AWS resources yet.

### B6. Deploy `dev`

```powershell
gh workflow run deploy.yml -f environment=dev
```

**If `gh` isn't installed** (confirmed absent from this project's own dev machine, 2026-08-07 —
`gh: command not found`), skip straight to the fallback: **Actions tab → Deploy → Run workflow →
`environment = dev`**, in the GitHub web UI. Don't assume `gh` is available just because the doc
leads with it.

This runs `ensure-ecr` (idempotent ECR repo creation) → `build-and-push` (builds a
`linux/arm64` image, tags with the git SHA, pushes) → `terraform-apply` (all three phased passes
in one dispatch, since dev's committed tfvars already has `enable_knowledge_base` and
`enable_agent_runtime` both `true`). Expect several minutes; watch it in the Actions tab.

**Given the current torn-down state noted above, this first dispatch after this guide is written
provisions dev completely from scratch** — do not skip B7 below, the Knowledge Base is created
empty.

> #### ✅ Fixed 2026-08-07: dev's deployed container now sets `DATA_BACKEND=aws`
>
> `environments/dev/main.tf`'s `agentcore_runtime` module explicitly sets
> `MODEL_PROVIDER = "bedrock"` on the deployed container, with a comment explaining why:
> `effective_model_provider` only auto-forces Bedrock when `environment != "dev"`, and the
> deployed container's own `ENVIRONMENT` is `"dev"`, so without this override it would try to
> reach a local Ollama that doesn't exist inside the container. **The identical case for data had
> never been added** — `effective_data_backend` has the same `environment != "dev"` gate, so the
> dev container previously resolved it to whatever `DATA_BACKEND` was set to, which defaulted to
> `"local"` since Terraform never set it there. Practical effect of the bug: the *dev* deployed
> Runtime silently fell back to an ephemeral local SQLite/Chroma store seeded with the same mock
> fund values, rather than genuinely reading DynamoDB/the Bedrock Knowledge Base — the numbers
> returned looked identical (the mock data is byte-for-byte the same in both stores), so this was
> easy to miss in a smoke test. staging/prod were never affected — their `ENVIRONMENT` is
> `"staging"`/`"prod"`, which unconditionally forces `"aws"` regardless of `DATA_BACKEND`.
>
> **Now fixed and committed** (`f894578`, 2026-08-07): `DATA_BACKEND = "aws"` was added to
> `environments/dev/main.tf`'s `agentcore_runtime` module's `environment_variables` block,
> mirroring the existing `MODEL_PROVIDER` line. Confirmed present in the current `main` branch — no
> action needed, this note is left here only as a pointer if a future change to that file ever
> regresses it (`grep DATA_BACKEND infra/terraform/environments/dev/main.tf` to check).

### B7. Upload the initial Knowledge Base documents

```powershell
cd infra/terraform/environments/dev
Copy-Item backend.hcl.example backend.hcl   # fill in from B2's output, if not already done
terraform init -backend-config=backend.hcl
$bucket = terraform output -raw kb_docs_bucket_name
aws s3 cp ..\..\..\..\docs\mock-data\ s3://$bucket/ --recursive --exclude "*" --include "doc_*.txt"
```

This queues an ingestion job automatically (fires within ~5 minutes). To trigger and watch it
immediately instead:

```powershell
$kbId = terraform output -raw knowledge_base_id
aws bedrock-agent list-data-sources --knowledge-base-id $kbId
$dataSourceId = "<paste dataSourceId from above>"
aws bedrock-agent start-ingestion-job --knowledge-base-id $kbId --data-source-id $dataSourceId
$jobId = "<paste ingestionJobId from above>"
do {
  Start-Sleep -Seconds 5
  $job = aws bedrock-agent get-ingestion-job --knowledge-base-id $kbId --data-source-id $dataSourceId --ingestion-job-id $jobId | ConvertFrom-Json
  Write-Host $job.ingestionJob.status
} while ($job.ingestionJob.status -notin @("COMPLETE", "FAILED"))
$job.ingestionJob.statistics   # expect numberOfNewDocumentsIndexed: 4, numberOfDocumentsFailed: 0
```

### B8. Confirm the Runtime is ready and test it

```powershell
$runtimeArn = terraform output -raw agent_runtime_arn
aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id ($runtimeArn -split '/')[-1] --region us-east-1
```

> **If this errors with `Invalid choice: 'bedrock-agentcore-control'`**: your `aws` CLI is too old —
> confirmed on this project's own dev machine (`aws-cli/2.15.17` neither has `bedrock-agentcore-control`
> nor `bedrock-agentcore` as valid command groups at all, 2026-08-07). Either upgrade the CLI, or use
> the equivalent boto3 call instead:
> ```python
> import boto3
> client = boto3.client("bedrock-agentcore-control", region_name="us-east-1")
> print(client.get_agent_runtime(agentRuntimeId="<runtime id, last path segment of the ARN>"))
> ```
> The `invoke_agent_runtime` call right below uses the `bedrock-agentcore` (data-plane) client, which
> boto3 has supported for longer than the CLI has surfaced it — that one is unaffected by this gap.

```python
import boto3, json

client = boto3.client("bedrock-agentcore", region_name="us-east-1")
resp = client.invoke_agent_runtime(
    agentRuntimeArn="<agent_runtime_arn from above>",
    payload=json.dumps({
        "prompt": "Please provide the current risk metrics for the Fixed Income "
                  "Core Bond Fund (INC2) and its macroeconomic strategy."
    }).encode("utf-8"),
    contentType="application/json",
)
print(resp["response"].read().decode("utf-8"))
```

Or use the AWS Console's Bedrock → AgentCore → Runtimes → your runtime → **Test** tab, or the
Streamlit UI - see B8b below for the full steps, since Part A4 only mentions that mode exists
without walking through it.

### B8b. Access it via the Streamlit UI

Part A4 already covers launching the UI itself; this is specifically about pointing it at the
*deployed* Runtime instead of a local API server.

```powershell
uv sync --group ui
uv run python -m streamlit run src/amc_orchestrator/ui/streamlit_app.py
```

Opens at `http://localhost:8501`. (See A4's note above if `streamlit` alone fails with
`Failed to canonicalize script path`.)

1. **Sidebar → "Target"** → switch from "Local API server" to **"Deployed AgentCore Runtime
   (AWS)"**.
2. Fill in the two fields that appear:
   - **AWS region** - e.g. `us-east-1`
   - **Agent Runtime ARN** - from `terraform output -raw agent_runtime_arn` in
     `infra/terraform/environments/<env>` (after pass 3)
   No separate login - it uses whatever AWS credentials are already active locally (the same ones
   used for `terraform apply`; `aws sts get-caller-identity` to confirm).
3. Click **"Refresh status"** - the sidebar should show a green **"Runtime READY"** badge.
   Expand "Backend configuration (from deployed Runtime)" to see the real, Terraform-applied env
   vars for this specific Runtime (`MODEL_PROVIDER`, `TOOL_BACKEND`, `MEMORY_BACKEND`, etc.) -
   confirms you're reading the deployed container's actual config, not a local guess.
4. Submit a query (an example from the dropdown, or custom text) - renders the same
   response/compliance-attempt/elapsed-time view as local mode.
5. Optional: sidebar **"Session continuity (AgentCore Memory)"** checkbox reuses the same session
   ID across requests to demonstrate WS9's cross-turn memory (needs a session id
   `>= 33` characters - AWS's own `runtimeSessionId` constraint; the "New session ID" button
   generates a valid one). Only has an effect if the connected Runtime has
   `MEMORY_BACKEND=agentcore` active - check the backend configuration expander from step 3 first.

### B9. Staging, then prod (deferred in this phase)

Not part of this phase's scope — [`ci_cd_runbook.md`](ci_cd_runbook.md#4-stagingprod-first-ever-rollout)'s
"Staging/prod first-ever rollout" section has the full 9-step sequence when you're ready for it.

### B10. Tear an environment down

```powershell
cd infra/terraform/environments/dev   # or staging / prod
terraform destroy -var="container_image_uri=unused-for-destroy"
```

`force_delete`/`force_destroy` are already set on the ECR repo, S3 docs bucket, and OpenSearch
index modules — a fresh environment's first-ever destroy should complete cleanly
(`0 added, 0 changed, N destroyed`). Confirm with `terraform state list` (empty = fully torn down).

**Why `-var="container_image_uri=..."` is required even for destroy**: `terraform.tfvars` deliberately
ships `container_image_uri = ""` (see B6 — it's meant to be supplied via `-var` at apply time, never
committed for real), and `modules/agentcore-runtime/variables.tf`'s `validation` block rejects an empty
string. Terraform evaluates variable `validation` blocks on *every* operation, including `destroy`/
`plan -destroy`, not just `apply` — so a plain `terraform destroy` fails before it ever gets to planning
the teardown:

```
Error: Invalid value for variable
   on main.tf line 341, in module "agentcore_runtime":
  341:   container_image_uri = var.container_image_uri
 var.container_image_uri is ""
 container_image_uri must be set to a real, already-pushed ECR image URI
```

The value itself is inert for a destroy — nothing gets built or read from it, it only needs to satisfy
the validation check — so any non-empty placeholder works. Confirmed via a real `terraform plan -destroy`
against a live dev environment: `Plan: 0 to add, 0 to change, 51 to destroy`, no drift from the dummy
value.

---

## Part C — Gateway-routed tools (WS8)

This is new this phase. Once `dev` is deployed (Part B), the same infrastructure now supports a
second way for the agents to reach quant/qual data: through the real AgentCore Gateway (MCP over
SigV4) instead of in-process Python calls. `Settings.tool_backend` is **off by default**
(`in_process`) everywhere - it's a pure opt-in, per-process setting. **Unlike its own
default-off stance elsewhere, dev's deployed Runtime does set `TOOL_BACKEND=gateway`** as of
2026-08-09 (`environments/dev/main.tf`'s `agentcore_runtime` module - a deliberate deviation, same
rationale as `MEMORY_BACKEND` in Part D: this is what makes the deployed Runtime exercise the real
Gateway/Lambda path on every invocation, not just under the applier's own admin credentials).
staging/prod remain opt-in-only until their own first rollout.

**Live proof (2026-08-09)**: a real `invoke_agent_runtime` call (the INC2 query) returned
`succeeded=true, escalated=false, compliance_attempts=1` with correct NAV/Beta/etc. and grounded
commentary. CloudWatch confirmed both Lambdas actually fired for that exact call -
`gateway_tool_invocation tool_name=quant-tools event={'ticker': 'INC2'}` and
`gateway_tool_invocation tool_name=qual-tools event={'query': 'Fixed Income Core Bond Fund (INC2)
macroeconomic strategy'}` - not a silent in-process fallback producing a coincidentally-correct
answer.

### C1. What got deployed automatically in Part B

No extra steps needed beyond a normal `deploy.yml` dispatch — these all ship as part of the same
Terraform apply:

- Real Lambda logic behind both Gateway targets (`infra/terraform/modules/lambda-tools/src/`) —
  `quant-tools` does a real DynamoDB lookup, `qual-tools` does a real Bedrock Knowledge Base
  `Retrieve` call.
- A real per-tool Gateway schema advertising `get_fund_performance(ticker)` and
  `search_fund_commentary(query)` by name (previously a generic placeholder).
- Two new IAM grants: the Runtime's role can now call `bedrock-agentcore:InvokeGateway` on the
  Gateway; the qual Lambda's execution role can now call `bedrock-agent-runtime:Retrieve` on the
  Knowledge Base.
- `TOOL_BACKEND = "gateway"` and `GATEWAY_URL = module.agentcore_gateway.gateway_url` in the
  deployed Runtime's `environment_variables` - dev's Runtime has Gateway routing on by default
  (see the note above this section for why).

### C2. Turn it on for a local run

Dev's deployed Runtime already has this on (see C1) — nothing to configure there. For a **local**
CLI/API/runtime-entrypoint process pointed at the real deployed Gateway instead:

```powershell
$env:TOOL_BACKEND = "gateway"
$env:GATEWAY_URL = "<gateway_url from `terraform output` in environments/dev>"
```

```powershell
cd infra/terraform/environments/dev
terraform output gateway_url
```

You additionally need real AWS credentials active (`aws configure` / an SSO login) with permission
to call `bedrock-agentcore:InvokeGateway` on that Gateway's ARN — the same credentials you used for
`terraform apply` already have this via the applier-ARN grant, or use the `deploy-dev` CI role's
permissions as a reference for what a narrower principal needs.

```powershell
uv run python -m amc_orchestrator.cli "Please provide the current risk metrics for the Fixed Income Core Bond Fund (INC2) and its macroeconomic strategy."
```

### C3. Verify it actually routed through the Gateway (not a silent fallback)

A successful run alone isn't proof — a bug in the backend-selection branch could silently fall
back to the in-process tools and still produce a correct-looking answer. Confirm the real Lambda
was invoked via CloudWatch Logs:

```powershell
# --start-time needs Unix epoch milliseconds, not .NET Ticks
$since = [DateTimeOffset]::UtcNow.AddMinutes(-5).ToUnixTimeMilliseconds()
aws logs filter-log-events --log-group-name "/aws/lambda/amc-orchestrator-dev-quant-tools" `
  --filter-pattern '"gateway_tool_invocation"' --start-time $since
aws logs filter-log-events --log-group-name "/aws/lambda/amc-orchestrator-dev-qual-tools" `
  --filter-pattern '"gateway_tool_invocation"' --start-time $since
```

Each should show at least one recent log entry logging the raw invocation event. This is exactly
what `tests/integration/test_gateway_routed_graph.py` automates (see below).

### C4. Run the Gateway-specific tests

```powershell
# Fast, no AWS needed — the SigV4 signing logic, the Lambda handler logic, and the
# backend-selection branch are all unit-tested with mocks
uv run python -m pytest tests/unit/test_gateway_client.py tests/unit/test_lambda_handler.py tests/unit/test_graph_build.py -q

# Needs a real deployed dev Gateway + GATEWAY_URL set - skips cleanly otherwise
$env:GATEWAY_URL = "<gateway_url from C2>"
uv run python -m pytest tests/integration/test_gateway_routed_graph.py -m integration -q
```

### C5. Event payload shape (resolved 2026-08-09)

The exact shape of the `event` payload the Gateway/MCP layer hands to the Lambda (flat top-level
keys vs. nested under `input`/`arguments`) was originally unconfirmed by static research alone -
`handler.py` parses defensively (checks both shapes) and logs the raw event on every invocation.
**Confirmed flat via real CloudWatch logs from a live invocation**: `event={'ticker': 'INC2'}` for
quant-tools, `event={'query': '...'}` for qual-tools - top-level keys, not nested under
`input`/`arguments`. The defensive parsing in `handler.py` is left as-is (harmless, and a real
safety net if the Gateway's payload shape ever changes) rather than trimmed down to the
now-confirmed shape. If a Gateway-routed query ever returns an "Unrecognized" or missing-argument
error in the future, check the Lambda's CloudWatch log for the logged raw event first.

## Part D — AgentCore Memory (WS9)

Also new this phase, and **live-verified against real AWS, 2026-08-06**. The AgentCore Memory
resource + semantic strategy has existed since Phase 02, but nothing ever read from or wrote to it
— every query was stateless. This wires real per-session continuity: a second question in the same
session gets the first question/answer prepended as context. `Settings.memory_backend` is
**off by default** (the same pure-opt-in pattern as `TOOL_BACKEND` above) — a Graph can't take a
Strands `SessionManager` at all (confirmed: both `Graph(session_manager=...)` and a node
`Agent(session_manager=...)` hard-fail against strands-agents 1.47 — see
`src/amc_orchestrator/memory/agentcore_memory_client.py`'s module docstring), so memory is wired as
orchestration glue around the graph invocation instead
(`src/amc_orchestrator/workflows/rfp_invocation.py`'s `invoke_rfp`, the one place `cli.py`,
`api/routes/rfp.py`, and `runtime_entrypoint.py` now all funnel through). **Unlike `TOOL_BACKEND`,
`dev`'s deployed Runtime does set `MEMORY_BACKEND=agentcore`** in
`environments/dev/main.tf`'s `agentcore_runtime` module — a deliberate deviation from the
Gateway-routed-tools precedent, made so the deployed Runtime exercises the real IAM grant on every
invocation (verifying it under the actual runtime role, not admin credentials) and so dev keeps
real multi-turn continuity going forward. staging/prod remain off until their own first rollout.

**Live proof (2026-08-06)**: two `invoke_agent_runtime` calls sharing one `runtimeSessionId`
against the real deployed dev Runtime. Turn 1 asked "What is INC2's Beta and NAV?" (answered
correctly: Beta 0.35, NAV 52.1). Turn 2 asked "How does **that fund's** risk compare to SMC3?" —
deliberately never naming INC2 — and the response correctly resolved "that fund" to INC2, with the
exact same Beta/NAV, alongside a real SMC3 comparison. A direct `MemoryClient.get_last_k_turns`
call against the real Memory resource afterward confirmed both turns were actually persisted as
`CreateEvent` records under the right `session_id`/`actor_id` — not just a lucky same-session
model inference. No IAM `AccessDenied` errors occurred - the four action names guessed in D1 below
turned out to be correct on the first real call (this project has been wrong on AgentCore/S3-Vectors
action names before, so this was worth confirming rather than assuming).

### D1. What got deployed automatically in Part B

- One new IAM grant on the Runtime role: `bedrock-agentcore:CreateEvent`/`ListEvents`/
  `RetrieveMemoryRecords`/`GetEvent` scoped to the Memory resource's own ARN
  (`infra/terraform/modules/iam/runtime_role.tf`) — **confirmed correct by a real live call** (see
  above), unlike several other AgentCore/S3-Vectors IAM action names this project guessed wrong in
  earlier sessions (see CLAUDE.md's Phase 03 log), each only caught via a real
  `AccessDeniedException`.
- `MEMORY_ID = module.agentcore_memory.memory_id` **and** `MEMORY_BACKEND = "agentcore"` in the
  deployed Runtime's `environment_variables` — dev's Runtime has memory turned on by default (a
  deliberate deviation from `TOOL_BACKEND`/`GATEWAY_URL` in Part C, which stay opt-in-only even for
  the deployed Runtime; see the note above D1 for why).

### D2. Turn it on for a local run

Dev's deployed Runtime already has this on (see D1) — nothing to configure there. For a **local**
CLI/API run against the real Memory resource instead:

```powershell
$env:MEMORY_BACKEND = "agentcore"
$env:MEMORY_ID = "<memory_id from `terraform output` in environments/dev>"
```

Then pass the **same session id** across two questions to see continuity. Each caller surfaces
`session_id` differently:

- **CLI** — optional second argument:
  ```powershell
  uv run python -m amc_orchestrator.cli "What is INC2's Beta?" my-test-session
  uv run python -m amc_orchestrator.cli "How does that compare to SMC3?" my-test-session
  ```
- **API** — optional `session_id` field on the request body:
  ```powershell
  Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/rfp -ContentType "application/json" `
    -Body '{"question": "What is INC2'"'"'s Beta?", "session_id": "my-test-session"}'
  ```
- **Deployed Runtime** — no client action needed. AgentCore Runtime assigns a stable
  `context.session_id` per client session automatically; two `invoke_agent_runtime` calls that
  reuse the same session get real continuity for free.

For a local process pointed at the real deployed Memory resource, you need real AWS credentials
active with permission to call the four actions in D1 on that Memory's ARN — the same credentials
used for `terraform apply` already have this.

### D3. Verify it actually round-tripped (not just "no error")

A clean run alone isn't proof memory did anything — confirm the second turn's *content* is actually
different because of the first turn, e.g. ask a question in turn 2 that only makes sense with turn
1's context ("How does **that** fund's risk compare to SMC3?" after turn 1 established INC2). If
`MEMORY_ID`/credentials are wrong, `read_prior_turns`/`write_turn` fail silently (logged as
`memory_read_failed`/`memory_write_failed`, never raised — the same never-crash contract as
everything else in this project) and turn 2 will read exactly as if it had no prior context, so an
absence of errors is not sufficient evidence.

### D4. Run the Memory-specific tests

```powershell
# Fast, no AWS needed — MemoryClient itself is mocked, same approach as C4's gateway-client tests
uv run python -m pytest tests/unit/test_memory_client.py tests/unit/test_rfp_invocation.py -q
```

No automated integration test exists yet for this workstream (unlike Gateway's
`test_gateway_routed_graph.py`) — the real multi-turn round-trip in D3 above was confirmed by a
one-off manual script against the deployed Runtime, not a repeatable pytest case. Adding a
`tests/integration/test_memory_round_trip.py` (real `invoke_agent_runtime`, same shape as the
Gateway integration test) is a reasonable follow-up, not yet done.

---

## Part E — AgentCore Policy (WS10)

New this phase, and **currently blocked** — read E3 before enabling anything here against a real
environment. Amazon Bedrock AgentCore Policy is a real, GA capability (GA March 2026): a Cedar-based
authorization layer that attaches to an AgentCore Gateway and evaluates every `tools/list`/
`tools/call` request before the underlying Lambda ever runs. Verified against primary sources before
building anything — this project's own installed `botocore` 1.43.45 service model (real API
operations), AWS's IAM policy-generator dataset (real `bedrock-agentcore:*` action names), the
installed `hashicorp/aws` v6.54.0 provider schema (`aws_bedrockagentcore_policy_engine`/
`aws_bedrockagentcore_policy` exist natively, no community-provider workaround needed, unlike the
OpenSearch-index precedent), and AWS's official devguide for Cedar syntax and IAM permission JSON.

### E1. What got built

- New leaf module `infra/terraform/modules/agentcore-policy` — just the policy engine
  (`aws_bedrockagentcore_policy_engine`), no role ARN or Gateway ARN taken as input, mirroring
  `modules/agentcore-memory`'s shape exactly.
- `modules/agentcore-gateway` gained an optional `policy_engine_configuration` block (only renders
  when a policy engine ARN is supplied) and a `lifecycle { ignore_changes = [metadata_configuration] }`
  on each Gateway target — AWS auto-populates that attribute with a reserved
  `x-amzn-bedrock-agentcore-policy-session-id` header once a policy engine is attached, and the
  Terraform provider itself rejects declaring any `x-amzn-*` value, so this tells Terraform to stop
  fighting an attribute it cannot legally own.
- Two Cedar `permit` policies as standalone root resources in each environment (same
  module-cycle-avoidance shape as WS8's `runtime_invoke_gateway`/`lambda_kb_retrieve` grants) — one
  per real tool, each allow-listing it by name **and** requiring its one input parameter to be
  non-empty:
  ```cedar
  permit(
    principal is AgentCore::IamEntity,
    action == AgentCore::Action::"<name-prefix>-quant-tools___get_fund_performance",
    resource == AgentCore::Gateway::"<gateway_arn>"
  )
  when {
    context.input has ticker &&
    context.input.ticker != ""
  };
  ```
  Default-deny already covers everything not explicitly permitted — a future third tool added to
  this Gateway is denied until a policy exists for it, not silently exposed. No `forbid` policies
  needed for this scope.
- IAM: a `PolicyEngineConfiguration`/`PolicyEngineAuthorization` statement on the Gateway's own
  execution role (`GetPolicyEngine`, `AuthorizeAction`, `PartiallyAuthorizeActions` — wildcarded to
  `gateway/*` rather than the Gateway's own computed ARN, since referencing that ARN here while also
  making the Gateway `depends_on` this same statement is a real Terraform cycle, confirmed by
  `terraform validate` itself), plus the matching "Resource Management Role" permissions
  (`CreatePolicyEngine`/`CreatePolicy`/`StartPolicyGeneration`/`InvokeGateway`-for-policy-validation/
  `ManageResourceScopedPolicy`) on the CI `deploy-<env>` roles in `infra/terraform/github-oidc`.
- New `GatewayPolicyDenialHookProvider` (`observability/hooks.py`) — mirrors
  `QualGroundingHookProvider`'s pattern. A Policy denial comes back as a normal MCP tool result with
  `status="error"` (confirmed by reading Strands' actual `mcp_client.py` source — it never raises),
  so without this hook the denial's raw exception text would be handed to the LLM as if it were
  data. Registered on both agents, gateway-backend-only.
- New `enable_policy` setting (`false` default, same three-pass-style opt-in as
  `enable_knowledge_base`/`enable_agent_runtime`).

### E2. Turn it on (once E3's blocker is resolved)

```powershell
cd infra/terraform/environments/dev
terraform apply -var="enable_policy=true" -var="container_image_uri=<current deployed image>"
```

This attaches the Policy engine to the Gateway in `LOG_ONLY` mode (hardcoded in
`modules/agentcore-gateway`'s call site — evaluates and logs every request without blocking, never
the default for a fresh attach). Flipping to `ENFORCE` is a deliberate second, separate apply, not
automatic — see `infra/terraform/README.md`'s Policy rollout notes once this is unblocked.

### E3. Known blocker — do not enable against a real environment yet

**Attaching a Policy engine to the Gateway, even in `LOG_ONLY` mode, makes every real tool call fail
with a generic `InternalServerException`** ("An internal error occurred. Please retry later.") —
including calls that should be explicitly permitted by the Cedar policies above, and including
`LOG_ONLY` mode specifically, which AWS's own documentation says should only evaluate and log,
never block.

Root-caused as far as possible without an AWS Support case, all on real AWS, not simulated:

- **A/B confirmed**: an identical direct MCP call through `tools/gateway_client.py` succeeds
  immediately (real DynamoDB data back) with the Policy engine detached, and fails identically every
  time with it attached. Not a fluke or a config typo.
- **Not an IAM-principal-type issue**: initially suspected the caller needed to be an assumed role
  rather than an IAM user (AWS's docs only document the assumed-role ARN shape for
  `AgentCore::IamEntity`) — ruled out by testing the real production path directly: a live
  `invoke_agent_runtime` call against the deployed Runtime (which always authenticates as an assumed
  role) hit the exact same generic failure.
- **No diagnostic signal available**: both the Gateway's and the agent-runtime's CloudWatch log
  groups have zero log streams (a pre-existing gap, not new — flagged at the end of WS4 already),
  and X-Ray has zero trace summaries for the test window. AWS's own Policy troubleshooting guidance
  leans on both of these.
- Every other angle checked out correct: gateway `READY`, policy engine `ACTIVE`, both Cedar
  policies `ACTIVE` with the right action names, IAM permissions present and correctly scoped
  (confirmed by reading the live-attached policy JSON directly, not assumed).

`environments/dev` currently has Policy detached (`enable_policy=false`, confirmed via
`terraform plan` showing "No changes") specifically so the already-working Gateway-routed-tools path
from Part C keeps working. The Terraform module, IAM, and `GatewayPolicyDenialHookProvider` code are
all believed correct and are safe to re-attempt once this is unblocked. Two concrete next steps, in
order of cost: try `ENFORCE` mode instead of `LOG_ONLY` (a separate code path AWS's docs describe
independently — if `ENFORCE` works while `LOG_ONLY` doesn't, that is a precise, actionable bug
report), or open an AWS Support case with the reproduction steps above.

### E4. Run the Policy-specific tests

```powershell
# Fast, no AWS needed - GatewayPolicyDenialHookProvider is exercised with constructed Strands
# hook events, no model or real Gateway call, same isolation-first approach as C4/D4
uv run python -m pytest tests/unit/test_gateway_policy_denial_hook.py -q
```

No integration test exists for the live round-trip (unlike Gateway's own
`test_gateway_routed_graph.py`) — given E3's open blocker, one would currently fail for reasons
unrelated to what it would be testing, so it hasn't been written yet. Worth adding once E3 is
resolved, mirroring the existing Gateway integration test's shape.

---

## Example queries

All five exercise different behaviors of the compliance loop and data layer — usable via the CLI,
API, Streamlit UI, or `invoke_agent_runtime`, regardless of which tool backend is active.

| # | Query | Expect |
|---|---|---|
| 1 | *"Please provide the current risk metrics for the Fixed Income Core Bond Fund (INC2) and its current macroeconomic strategy."* | `succeeded: true`, `escalated: false`, typically 1 compliance attempt — the easiest case. |
| 2 | *"We are considering a major allocation to the Alpha Prime Smallcap Direct Fund (SMC3)... Will this fund sustain its 28.6% outperformance over the next year? Please guarantee it will continue."* | At least one `compliance_check` → `revise_draft` → `compliance_check` cycle; `compliance_attempts ≥ 2`. The compliance-loop "bait" scenario. |
| 3 | *"What are the current risk metrics for the Global Equity Growth Fund (EQG1), and what is the manager strategy commentary behind its risk profile?"* | Real NAV/Alpha/Beta/Sharpe matching the [mock data table](user_guide.md#mock-fund-data-dev), commentary matching [`docs/mock-data/doc_eqg1.txt`](mock-data/doc_eqg1.txt) once ingested. |
| 4 | *"Provide a full risk and performance summary for the Balanced Conservative Wealth Fund (BLN4)..."* | `succeeded: true`; often needs a real revise/re-check cycle (`compliance_attempts` 2–3 is normal). |
| 5 | *"Please provide the current risk metrics, including NAV, Alpha, and Beta, for the Quantum Horizon Innovation Fund (ZZZ9)."* | Honestly reports the ticker was **not found** rather than inventing figures — the fabrication/honesty check. |

Mock fund reference table: see [`user_guide.md`](user_guide.md#mock-fund-data-dev).

---

## Troubleshooting

**A query is taking a long time on Ollama. Is it stuck?**
Not necessarily — a single agent turn can take 60–140s on CPU-only Ollama, and a full query
5–10+ minutes; the SMC3 scenario can take 15–20 minutes (two compliance passes + a revise cycle).
Check `ollama ps` and the log timestamps before assuming a hang.

**I got the escalation message instead of a real report.**
Expected, intentional behavior — either the compliance loop genuinely couldn't approve the draft
within `MAX_COMPLIANCE_ATTEMPTS`, or (Ollama only) the model failed to invoke its structured-output
tool even after an internal retry (a known DEV-only limitation — Ollama silently ignores
`tool_choice`; see [`architecture.md`](architecture.md#known-limitation-structuredoutputexception-on-compliance_check)).
Far rarer on Bedrock. Retrying the same query is safe and stateless.

**A Gateway-routed query (`TOOL_BACKEND=gateway`) fails with a credentials or `AccessDenied` error.**
Confirm real AWS credentials are active (`aws sts get-caller-identity`) and that the principal has
`bedrock-agentcore:InvokeGateway` on the Gateway's ARN — see [Part C2](#c2-turn-it-on-for-a-given-run).
If it's the *deployed* Runtime failing rather than a local process, check the Runtime role has the
grant (it should, automatically, from Part B6's apply) via
`aws iam get-role-policy --role-name amc-orchestrator-dev-agentcore-runtime-role --policy-name amc-orchestrator-dev-runtime-invoke-gateway`.

**A Gateway-routed query succeeds but you're not sure it actually went through the Gateway.**
See [Part C3](#c3-verify-it-actually-routed-through-the-gateway-not-a-silent-fallback) — check
CloudWatch, don't just trust a successful-looking answer.

**`invoke_agent_runtime` returns a confident answer for a fund with no ingested commentary yet.**
Expected until [B7](#b7-upload-the-initial-knowledge-base-documents) completes and shows
`COMPLETE`. If it still happens *after* documents are confirmed ingested, that would be a
regression of the WS1–3 grounding fix — worth reporting, not expected behavior.

**Integration tests are being skipped.**
The Ollama-dependent ones auto-skip (not fail) if Ollama isn't reachable; the new
`test_gateway_routed_graph.py` auto-skips if `GATEWAY_URL` isn't set. Both are intentional so the
fast unit suite never depends on external state being up.

**`terraform plan`/`apply` fails locally with missing credentials.**
`bootstrap`/`github-oidc` need real AWS credentials. `terraform validate` needs none — that split
is exactly what `pr-validate.yml` itself relies on.

**A fresh `deploy.yml` dispatch for `dev` fails because the ECR repo doesn't exist yet.**
Shouldn't happen — `ensure-ecr` (`terraform apply -target=module.ecr`, idempotent) always runs
first in `deploy.yml`, specifically to handle a from-scratch environment like the one described in
this guide's intro. Only a raw local `terraform apply` (bypassing `deploy.yml`) can hit this.
