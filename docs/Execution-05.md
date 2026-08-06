# Execution Guide: AMC RFP & Portfolio Insight Orchestrator — Phase 04

This is the step-by-step "how do I actually run this" guide for the current state of the
repository (Phase 04: compliance grounding fixes + Gateway-routed tools). It is meant to be
followed top to bottom by someone who has just cloned the repo and knows nothing else about it.

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
4. **Current AWS state**: as of this writing, `environments/dev` has been **torn down to zero AWS
   resources** (a deliberate, cost-control teardown from the prior session — see `CLAUDE.md`'s
   Phase 04 log). None of the WS8 infra changes above have been applied to real AWS yet. Part B
   below covers the full from-scratch redeploy.

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
| `aws` CLI v2, `docker`, `boto3` | Post-deploy smoke testing, document uploads, image build/push | `aws --version`, `docker --version` |

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
uv run streamlit run src/amc_orchestrator/ui/streamlit_app.py
```

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

### B2. Bootstrap the Terraform state backend (one-time, local)

```powershell
cd infra/terraform/bootstrap
terraform init
terraform apply
terraform output state_bucket_name      # note this — every backend.hcl below needs it
terraform output state_bucket_region
```

### B3. Apply the GitHub OIDC identity module (one-time, local)

```powershell
cd ../github-oidc
Copy-Item backend.hcl.example backend.hcl
Copy-Item terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` (`github_org`, `github_repo`, `state_bucket_name` from B2) and
`backend.hcl` (`bucket` = same state bucket name, `key = "github-oidc/terraform.tfstate"`,
`region = "us-east-1"`), then:

```powershell
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
terraform output plan_role_arn
terraform output deploy_role_arns
```

Keep all four ARNs (`plan_role_arn`, `deploy_role_arns["dev"|"staging"|"prod"]`) — needed next.

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

(Or: Actions tab → **Deploy** → **Run workflow** → `environment = dev`.)

This runs `ensure-ecr` (idempotent ECR repo creation) → `build-and-push` (builds a
`linux/arm64` image, tags with the git SHA, pushes) → `terraform-apply` (all three phased passes
in one dispatch, since dev's committed tfvars already has `enable_knowledge_base` and
`enable_agent_runtime` both `true`). Expect several minutes; watch it in the Actions tab.

**Given the current torn-down state noted above, this first dispatch after this guide is written
provisions dev completely from scratch** — do not skip B7 below, the Knowledge Base is created
empty.

> #### ⚠️ Known issue: dev's deployed container does not set `DATA_BACKEND=aws`
>
> `environments/dev/main.tf`'s `agentcore_runtime` module explicitly sets
> `MODEL_PROVIDER = "bedrock"` on the deployed container, with a comment explaining why:
> `effective_model_provider` only auto-forces Bedrock when `environment != "dev"`, and the
> deployed container's own `ENVIRONMENT` is `"dev"`, so without this override it would try to
> reach a local Ollama that doesn't exist inside the container. **The identical case for data was
> never added** — `effective_data_backend` has the same `environment != "dev"` gate, so the dev
> container resolves it to whatever `DATA_BACKEND` is set to, which defaults to `"local"` since
> Terraform never sets it there (confirmed by inspection — no `DATA_BACKEND` entry exists in that
> module's `environment_variables` block as of this writing).
>
> **Practical effect**: the *dev* deployed Runtime likely falls back to an ephemeral local
> SQLite/Chroma store seeded with the same mock fund values, rather than genuinely reading
> DynamoDB/the Bedrock Knowledge Base provisioned in this part — even though the numbers returned
> look identical (the mock data is byte-for-byte the same in both stores), so this is easy to miss
> in a smoke test. staging/prod are not affected — their `ENVIRONMENT` is `"staging"`/`"prod"`,
> which unconditionally forces `"aws"` regardless of `DATA_BACKEND`.
>
> **Fix, not yet applied** (a deliberate decision, not a silent change): add one line to
> `environments/dev/main.tf`'s `agentcore_runtime` module's `environment_variables` block,
> mirroring the existing `MODEL_PROVIDER` line:
>
> ```hcl
> DATA_BACKEND = "aws"
> ```
>
> Ask if you'd like this applied before or after your next dev deploy.

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

Or use the Streamlit UI (Part A4) with **Target = Deployed AgentCore Runtime (AWS)**, or the AWS
Console's Bedrock → AgentCore → Runtimes → your runtime → **Test** tab.

### B9. Staging, then prod (deferred in this phase)

Not part of this phase's scope — [`ci_cd_runbook.md`](ci_cd_runbook.md#4-stagingprod-first-ever-rollout)'s
"Staging/prod first-ever rollout" section has the full 9-step sequence when you're ready for it.

### B10. Tear an environment down

```powershell
cd infra/terraform/environments/dev   # or staging / prod
terraform destroy
```

`force_delete`/`force_destroy` are already set on the ECR repo, S3 docs bucket, and OpenSearch
index modules — a fresh environment's first-ever destroy should complete cleanly
(`0 added, 0 changed, N destroyed`). Confirm with `terraform state list` (empty = fully torn down).

---

## Part C — Gateway-routed tools (WS8)

This is new this phase. Once `dev` is deployed (Part B), the same infrastructure now supports a
second way for the agents to reach quant/qual data: through the real AgentCore Gateway (MCP over
SigV4) instead of in-process Python calls. It is **off by default** — nothing in Part B changes
behavior on its own.

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

### C2. Turn it on for a given run

Set two settings — either as process environment variables, or in `environments/.env.<env>`:

```powershell
$env:TOOL_BACKEND = "gateway"
$env:GATEWAY_URL = "<gateway_url from `terraform output` in environments/dev>"
```

```powershell
cd infra/terraform/environments/dev
terraform output gateway_url
```

For the **deployed Runtime container**, this needs to be set the same way `MODEL_PROVIDER`/
`DATA_BACKEND` overrides are today — as an `environment_variables` entry in
`environments/dev/main.tf`'s `agentcore_runtime` module (`TOOL_BACKEND = "gateway"`,
`GATEWAY_URL = module.agentcore_gateway.gateway_url` — the latter is actually already wired in;
only `TOOL_BACKEND` needs adding if you want the *deployed* Runtime to default to Gateway-routed
tools rather than opting in per-local-run).

For a **local process** (CLI/API/runtime-entrypoint smoke test) pointed at the real deployed
Gateway, you additionally need real AWS credentials active (`aws configure` / an SSO login) with
permission to call `bedrock-agentcore:InvokeGateway` on that Gateway's ARN — the same credentials
you used for `terraform apply` already have this via the applier-ARN grant, or use the
`deploy-dev` CI role's permissions as a reference for what a narrower principal needs.

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

### C5. Known open item

The exact shape of the `event` payload the Gateway/MCP layer hands to the Lambda (flat top-level
keys vs. nested under `input`/`arguments`) was not confirmed by static research alone —
`handler.py` parses defensively (checks both shapes) and logs the raw event on every invocation so
the real shape can be read from CloudWatch on the first live call and the parsing tightened if
needed. If a Gateway-routed query returns an "Unrecognized" or missing-argument error, check the
Lambda's CloudWatch log for the logged raw event first.

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
