# Composition order matters here - see each module's own comments for why.
# Rough shape: independent leaf resources first (dynamodb/ecr/s3/opensearch
# collection) -> iam (needs their ARNs) -> the access policy that needed iam
# and opensearch flipped the other way -> lambda-tools -> the two
# phase-gated modules (knowledge base, agent runtime) -> observability.

module "dynamodb" {
  source = "../../modules/dynamodb"

  name_prefix                    = local.name_prefix
  point_in_time_recovery_enabled = var.dynamodb_point_in_time_recovery
  deletion_protection_enabled    = var.dynamodb_deletion_protection
  use_cmk                        = var.use_cmk
  tags                           = local.common_tags
}

module "ecr" {
  source = "../../modules/ecr"

  name_prefix                = local.name_prefix
  untagged_image_expiry_days = var.ecr_untagged_image_expiry_days
  max_tagged_images          = var.ecr_max_tagged_images
  tags                       = local.common_tags
}

module "s3_kb_docs" {
  source = "../../modules/s3-kb-docs"

  name_prefix = local.name_prefix
  account_id  = data.aws_caller_identity.current.account_id
  use_cmk     = var.use_cmk
  tags        = local.common_tags
}

module "opensearch_serverless" {
  source = "../../modules/opensearch-serverless"

  enabled          = local.opensearch_enabled
  name_prefix      = local.name_prefix
  use_cmk          = var.use_cmk
  standby_replicas = var.opensearch_standby_replicas
  tags             = local.common_tags
}

module "iam" {
  source = "../../modules/iam"

  name_prefix                       = local.name_prefix
  aws_region                        = var.aws_region
  account_id                        = data.aws_caller_identity.current.account_id
  dynamodb_table_arn                = module.dynamodb.table_arn
  opensearch_collection_arn         = module.opensearch_serverless.collection_arn
  s3_vectors_bucket_arn             = var.enable_knowledge_base && var.vector_store_backend == "s3_vectors" ? module.s3_vectors[0].vector_bucket_arn : ""
  kb_docs_bucket_arn                = module.s3_kb_docs.bucket_arn
  ecr_repository_arn                = module.ecr.repository_arn
  bedrock_model_arns                = local.bedrock_model_arns
  lambda_tool_names                 = var.lambda_tool_names
  additional_data_access_principals = var.additional_data_access_principals
  # Unlike WS8's two grants below (which needed standalone root-module
  # resources to avoid a module cycle), agentcore_memory does NOT take a
  # role ARN from modules/iam as an input, so there's no reverse edge and
  # this grant lives directly inside modules/iam/runtime_role.tf instead.
  agentcore_memory_arn = module.agentcore_memory.memory_arn
  tags                 = local.common_tags
}

module "opensearch_access_policy" {
  source = "../../modules/opensearch-access-policy"

  enabled         = local.opensearch_enabled
  collection_name = module.opensearch_serverless.collection_name
  principal_arns  = module.iam.data_access_principal_arns
  tags            = local.common_tags
}

module "lambda_tools" {
  source = "../../modules/lambda-tools"

  name_prefix               = local.name_prefix
  tool_names                = var.lambda_tool_names
  lambda_execution_role_arn = module.iam.lambda_execution_role_arn
  dynamodb_table_name       = module.dynamodb.table_name
  bedrock_knowledge_base_id = var.enable_knowledge_base ? module.knowledge_base[0].knowledge_base_id : ""
  log_retention_days        = var.log_retention_days
  tags                      = local.common_tags

  depends_on = [module.opensearch_access_policy]
}

# --- Phase 2: vector index + knowledge base (see var.enable_knowledge_base) -
# vector_store_backend picks exactly one of the next two modules - see
# environments/dev/variables.tf and docs/architecture.md's "Environment
# lifecycle" section for why dev can opt into the cheaper S3 Vectors backend
# while staging/prod stay OpenSearch-only.
module "opensearch_index" {
  source = "../../modules/opensearch-index"
  count  = var.enable_knowledge_base && var.vector_store_backend == "opensearch" ? 1 : 0

  providers = {
    opensearch = opensearch
  }

  embedding_dimension = 1024

  depends_on = [module.opensearch_access_policy]
}

module "s3_vectors" {
  source = "../../modules/s3-vectors"
  count  = var.enable_knowledge_base && var.vector_store_backend == "s3_vectors" ? 1 : 0

  name_prefix         = local.name_prefix
  account_id          = data.aws_caller_identity.current.account_id
  embedding_dimension = 1024
  use_cmk             = var.use_cmk
  tags                = local.common_tags
}

module "knowledge_base" {
  source = "../../modules/knowledge-base"
  count  = var.enable_knowledge_base ? 1 : 0

  name_prefix               = local.name_prefix
  aws_region                = var.aws_region
  knowledge_base_role_arn   = module.iam.knowledge_base_role_arn
  docs_bucket_arn           = module.s3_kb_docs.bucket_arn
  vector_store_backend      = var.vector_store_backend
  opensearch_collection_arn = module.opensearch_serverless.collection_arn
  s3_vectors_index_arn      = var.vector_store_backend == "s3_vectors" ? module.s3_vectors[0].index_arn : ""
  embedding_model           = var.embedding_model
  tags                      = local.common_tags

  depends_on = [module.opensearch_index, module.s3_vectors]
}

module "kb_ingestion_sync" {
  source = "../../modules/kb-ingestion-sync"
  count  = var.enable_knowledge_base ? 1 : 0

  name_prefix             = local.name_prefix
  aws_region              = var.aws_region
  docs_bucket_id          = module.s3_kb_docs.bucket_name
  docs_bucket_arn         = module.s3_kb_docs.bucket_arn
  knowledge_base_id       = module.knowledge_base[0].knowledge_base_id
  data_source_id          = module.knowledge_base[0].data_source_id
  ingestion_sync_role_arn = module.iam.kb_ingestion_sync_role_arn
  log_retention_days      = var.log_retention_days
  tags                    = local.common_tags

  depends_on = [module.knowledge_base]
}

# --- Phase 3: agent runtime (see var.enable_agent_runtime) ----------------
module "agentcore_memory" {
  source = "../../modules/agentcore-memory"

  name_prefix       = local.name_prefix
  event_expiry_days = var.memory_event_expiry_days
  use_cmk           = var.use_cmk
  tags              = local.common_tags
}

module "agentcore_gateway" {
  source = "../../modules/agentcore-gateway"

  name_prefix      = local.name_prefix
  gateway_role_arn = module.iam.gateway_role_arn
  tags             = local.common_tags

  # Explicit per-tool literals, not a generic loop over function_arns - the
  # two tools have genuinely different real schemas now (WS8), matching the
  # same explicit-over-generic tradeoff already made in
  # modules/lambda-tools/main.tf's local.tool_env_vars.
  lambda_tools = {
    "quant-tools" = {
      lambda_arn    = module.lambda_tools.function_arns["quant-tools"]
      description   = "Fetches quantitative fund performance metrics (NAV, Alpha, Beta, Sharpe, etc.) by ticker."
      mcp_tool_name = "get_fund_performance"
      input_properties = [
        { name = "ticker", type = "string", description = "Fund ticker symbol, e.g. SMC3", required = true }
      ]
      output_properties = [
        # JSON-encoded metrics dict, or {"error": ...} if the ticker isn't found -
        # matches tools/quant_tools.py::get_fund_performance's exact return shape.
        { name = "result", type = "string", required = true }
      ]
    }
    "qual-tools" = {
      lambda_arn    = module.lambda_tools.function_arns["qual-tools"]
      description   = "Searches historical fund manager commentary and macro outlook by free-text query."
      mcp_tool_name = "search_fund_commentary"
      input_properties = [
        { name = "query", type = "string", description = "Free-text search query", required = true }
      ]
      output_properties = [
        # Newline-joined passages, or the exact "No relevant..." sentinel -
        # matches tools/qual_tools.py::search_fund_commentary's exact return
        # shape (QualGroundingHookProvider depends on this exact string).
        { name = "result", type = "string", required = true }
      ]
    }
  }
}

module "agentcore_runtime" {
  source = "../../modules/agentcore-runtime"
  count  = var.enable_agent_runtime ? 1 : 0

  name_prefix         = local.name_prefix
  runtime_role_arn    = module.iam.runtime_role_arn
  container_image_uri = var.container_image_uri
  protocol            = var.runtime_protocol
  tags                = local.common_tags

  environment_variables = {
    ENVIRONMENT = var.environment
    # `Settings.effective_model_provider` only forces Bedrock when
    # `environment != "dev"` - by design, so a *local* dev run (CLI/API on a
    # dev machine) can default to Ollama. But this deployed Runtime container
    # also sets ENVIRONMENT=dev, and there is no Ollama reachable from AWS -
    # without this, the container tries to reach localhost:11434 and every
    # invocation fails with a connection error. Force Bedrock explicitly here;
    # staging/prod don't need this since `environment != "dev"` already forces
    # it for them.
    MODEL_PROVIDER                 = "bedrock"
    DYNAMODB_TABLE_NAME            = module.dynamodb.table_name
    OPENSEARCH_COLLECTION_ENDPOINT = module.opensearch_serverless.collection_endpoint
    BEDROCK_KNOWLEDGE_BASE_ID      = var.enable_knowledge_base ? module.knowledge_base[0].knowledge_base_id : ""
    GATEWAY_URL                    = module.agentcore_gateway.gateway_url
    MEMORY_ID                      = module.agentcore_memory.memory_id
    # WS9: MEMORY_ID alone is inert (Settings.memory_backend defaults
    # "disabled", same pure-opt-in pattern as TOOL_BACKEND) - dev opts in
    # here so the deployed Runtime exercises real AgentCore Memory
    # read/write on every invocation, needed to live-verify the new IAM
    # grant actually works as the runtime role (not just under admin
    # credentials, which would pass regardless of whether the grant is
    # correctly scoped).
    MEMORY_BACKEND   = "agentcore"
    BEDROCK_MODEL_ID = var.bedrock_model_id
    AWS_REGION       = var.aws_region
  }
}

# --- WS8: Gateway-routed tools - two IAM grants that can't live inside
# modules/iam itself without creating a module cycle (module.agentcore_gateway
# and module.knowledge_base both already take role ARNs *from* modules/iam as
# inputs - modules/iam/knowledge_base_role.tf, gateway_role.tf - so feeding
# their own ARNs back into modules/iam would cycle). Same shape of problem
# this project already solved once for OpenSearch's access policy (see
# modules/opensearch-access-policy's history in CLAUDE.md's Phase 02 notes) -
# resolved here the same way, as standalone root-module policy attachments
# instead of a new module (overkill for one statement each).

# Runtime -> Gateway: lets the deployed Agent Runtime actually call the
# AgentCore Gateway (AWS_IAM/SigV4 authorizer) instead of only in-process tools.
data "aws_iam_policy_document" "runtime_invoke_gateway" {
  statement {
    sid       = "InvokeAgentCoreGateway"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = [module.agentcore_gateway.gateway_arn]
  }
}

resource "aws_iam_role_policy" "runtime_invoke_gateway" {
  name   = "${local.name_prefix}-runtime-invoke-gateway"
  role   = module.iam.runtime_role_name
  policy = data.aws_iam_policy_document.runtime_invoke_gateway.json
}

# Qual tool Lambda -> Knowledge Base: lets the qual-tools Lambda call
# bedrock-agent-runtime's Retrieve API (mirrors
# data/knowledge_base_store.py::search_commentary). Gated on
# enable_knowledge_base since module.knowledge_base is itself count-gated.
#
# The IAM action is "bedrock:Retrieve", NOT "bedrock-agent-runtime:Retrieve"
# (the boto3 client name doesn't match the IAM action namespace here) -
# confirmed via a real AccessDeniedException from a live Lambda invocation,
# not assumed; the first guess was wrong and only caught by testing against
# real AWS, consistent with this project's repeated experience that first-pass
# IAM action names are frequently wrong.
data "aws_iam_policy_document" "lambda_kb_retrieve" {
  count = var.enable_knowledge_base ? 1 : 0

  statement {
    sid       = "BedrockKnowledgeBaseRetrieve"
    effect    = "Allow"
    actions   = ["bedrock:Retrieve"]
    resources = [module.knowledge_base[0].knowledge_base_arn]
  }
}

resource "aws_iam_role_policy" "lambda_kb_retrieve" {
  count = var.enable_knowledge_base ? 1 : 0

  name   = "${local.name_prefix}-lambda-kb-retrieve"
  role   = module.iam.lambda_execution_role_name
  policy = data.aws_iam_policy_document.lambda_kb_retrieve[0].json
}

module "observability" {
  source = "../../modules/observability"

  name_prefix         = local.name_prefix
  aws_region          = var.aws_region
  dynamodb_table_name = module.dynamodb.table_name
  lambda_function_names = concat(
    values(module.lambda_tools.function_names),
    var.enable_knowledge_base ? [module.kb_ingestion_sync[0].lambda_function_name] : [],
  )
  kb_ingestion_dlq_name = var.enable_knowledge_base ? module.kb_ingestion_sync[0].dlq_name : ""
  alarm_email           = var.alarm_email
  log_retention_days    = var.log_retention_days
  tags                  = local.common_tags
}
