"""Every span name and attribute name a span can hold, in one place.

Names that start with `gen_ai.` are the OpenTelemetry GenAI conventions at
version 0.1.0. `error.type` is OpenTelemetry's general name for an error's
kind. `openinference.span.kind` is the one name of Phoenix's own scheme,
because the conventions have no step. Names that start with `lamp.` are
this repo's. A name that changes here needs a new layout number.
"""

RUN_SPAN = "invoke_agent lamplighter"
STEP_SPAN = "step"
MODEL_SPAN = "chat"
TOOL_SPAN = "execute_tool"

# On every span.
CONVERSATION_ID = "gen_ai.conversation.id"
RUN_ID = "lamp.run_id"
CONFIG_ID = "lamp.config_id"
LAYOUT = "lamp.layout"
SEQ = "lamp.seq"
ERROR_TYPE = "error.type"

# On the run's span.
OPERATION = "gen_ai.operation.name"
AGENT_NAME = "gen_ai.agent.name"
RUN_TRIGGER = "lamp.run.trigger"
RUN_END_REASON = "lamp.run.end_reason"
RUN_END_DETAIL = "lamp.run.end_detail"
RUN_STEPS = "lamp.run.steps"
RUN_TOOL_CALLS = "lamp.run.tool_calls"
RUN_INPUT_TOKENS = "lamp.run.input_tokens"
RUN_CACHE_READ_TOKENS = "lamp.run.cache_read_tokens"
RUN_CACHE_WRITE_TOKENS = "lamp.run.cache_write_tokens"
RUN_OUTPUT_TOKENS = "lamp.run.output_tokens"
RUN_COST = "lamp.run.cost_micro_dollars"
RUN_CALLS_WITHOUT_COST = "lamp.run.calls_without_cost"
RUN_CALLS_WITHOUT_USAGE = "lamp.run.calls_without_usage"
RUN_CALLS_WITHOUT_REPLY = "lamp.run.calls_without_reply"

# On a step's span.
OPENINFERENCE_KIND = "openinference.span.kind"
STEP = "lamp.step"

# On a model call's span.
PROVIDER = "gen_ai.provider.name"
REQUEST_MODEL = "gen_ai.request.model"
RESPONSE_MODEL = "gen_ai.response.model"
FINISH_REASONS = "gen_ai.response.finish_reasons"
INPUT_TOKENS = "gen_ai.usage.input_tokens"
OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
CACHE_READ_TOKENS = "gen_ai.usage.cache_read.input_tokens"
CACHE_WRITE_TOKENS = "gen_ai.usage.cache_write.input_tokens"
COST = "lamp.cost_micro_dollars"
TOOL_CALLS = "lamp.tool_calls"

# On a tool call's span.
TOOL_NAME = "gen_ai.tool.name"
TOOL_TYPE = "gen_ai.tool.type"
ACTION_CLASS = "lamp.action_class"
EFFECT_ID = "lamp.effect_id"
STARTS = "lamp.starts"
RESULT_CHARS = "lamp.result_chars"
FULL_OUTPUT_SHA256 = "lamp.full_output_sha256"
FULL_OUTPUT_BYTES = "lamp.full_output_bytes"
OUTSIDE_TEXT = "lamp.outside_text"
HARNESS_KIND = "lamp.harness_kind"

# The fixed values some of the names above take.
INVOKE_AGENT = "invoke_agent"
CHAT = "chat"
EXECUTE_TOOL = "execute_tool"
AGENT = "lamplighter"
CHAIN = "CHAIN"
FUNCTION = "function"
