# The quant-tools Lambda reuses amc_orchestrator/data/{dynamodb_store,sqlite_store}.py
# unmodified (zipped in at their real package path) rather than duplicating
# quant-fetch logic - both files are boto3/stdlib-only (no pydantic-settings/
# strands/chromadb), confirmed by direct read before choosing this approach.
# qual's Knowledge Base lookup is a small hand-vendored copy instead
# (src/knowledge_base_lookup.py) since the real knowledge_base_store.py
# imports structlog, which isn't in the default Lambda runtime - bundling it
# via a pip install step for one log line inside a function the Lambda never
# calls (ensure_seeded) isn't worth the added build complexity.
locals {
  repo_root_data_dir = "${path.module}/../../../../src/amc_orchestrator"

  reused_quant_files = {
    "amc_orchestrator/__init__.py"            = "${local.repo_root_data_dir}/__init__.py"
    "amc_orchestrator/data/__init__.py"       = "${local.repo_root_data_dir}/data/__init__.py"
    "amc_orchestrator/data/sqlite_store.py"   = "${local.repo_root_data_dir}/data/sqlite_store.py"
    "amc_orchestrator/data/dynamodb_store.py" = "${local.repo_root_data_dir}/data/dynamodb_store.py"
  }

  # Per-tool environment variables, merged with the shared TOOL_NAME below.
  # Hardcoding "quant-tools"/"qual-tools" here (rather than staying fully
  # generic over var.tool_names) matches this module's own pre-existing
  # convention - var.tool_names' docstring already says these two specific
  # names must match modules/iam's naming convention exactly, it was never
  # actually generic.
  # AWS_REGION is deliberately NOT set here - it's a Lambda-reserved
  # environment variable key (the platform injects it automatically, set to
  # the function's own deployed region) and CreateFunction rejects any
  # attempt to set it manually (InvalidParameterValueException, confirmed
  # via a real apply). handler.py's os.environ["AWS_REGION"] still works -
  # it just reads Lambda's own automatic value instead of a Terraform-set one,
  # which is correct for this project's single-region deployment anyway.
  tool_env_vars = {
    "quant-tools" = {
      DYNAMODB_TABLE_NAME = var.dynamodb_table_name
    }
    "qual-tools" = {
      BEDROCK_KNOWLEDGE_BASE_ID = var.bedrock_knowledge_base_id
    }
  }
}

data "archive_file" "tool" {
  type        = "zip"
  output_path = "${path.module}/.build/tool.zip"

  # Exclude __pycache__/*.pyc explicitly - fileset("**") otherwise picks up
  # local bytecode cache dirs (e.g. from running tests/unit/test_lambda_handler.py
  # directly against src/), which aren't valid UTF-8 for file() and break
  # validate/plan/apply for anyone who happens to have one on disk.
  dynamic "source" {
    for_each = [for f in fileset("${path.module}/src", "**") : f if !strcontains(f, "__pycache__")]
    content {
      content  = file("${path.module}/src/${source.value}")
      filename = source.value
    }
  }

  dynamic "source" {
    for_each = local.reused_quant_files
    content {
      content  = file(source.value)
      filename = source.key
    }
  }
}

resource "aws_cloudwatch_log_group" "tool" {
  for_each = toset(var.tool_names)

  name              = "/aws/lambda/${var.name_prefix}-${each.value}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "tool" {
  for_each = toset(var.tool_names)

  function_name = "${var.name_prefix}-${each.value}"
  role          = var.lambda_execution_role_arn
  handler       = "handler.handler"
  runtime       = "python3.13"
  memory_size   = var.memory_size
  timeout       = var.timeout_seconds

  filename         = data.archive_file.tool.output_path
  source_code_hash = data.archive_file.tool.output_base64sha256

  environment {
    variables = merge(
      { TOOL_NAME = each.value },
      lookup(local.tool_env_vars, each.value, {})
    )
  }

  tags = var.tags

  depends_on = [aws_cloudwatch_log_group.tool]
}
