github_org  = "kombaraj-ai"
github_repo = "uc02-mf-strands-agents-phase-04"

# Numeric IDs GitHub's OIDC tokens actually embed in the "sub" claim for
# this repo (root-caused 2026-08-09 by referring back to the earlier
# phase-03 repo's own identical failure and fix - see variables.tf's
# comment). Sourced from the real GitHub API response, not guessed:
# GET https://api.github.com/repos/kombaraj-ai/uc02-mf-strands-agents-phase-04
# ("id" and "owner.id" fields). Re-verify these if this repo is ever
# renamed or transferred again.
github_org_id  = "216298138"
github_repo_id = "1324644588"

# From: terraform -chdir=infra/terraform/bootstrap output state_bucket_name
state_bucket_name = "amc-orchestrator-tfstate-766354255780"
