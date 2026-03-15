# MCP Routing Shadow Eval

## Run

```bash
# Rule prefilter baseline (uses current live MCP state by default)
.\.venv\Scripts\python.exe examples/benchmarks/mcp_routing_shadow_eval.py --mode prefilter --details

# Planner shadow (requires configured LLM API key)
.\.venv\Scripts\python.exe examples/benchmarks/mcp_routing_shadow_eval.py --mode planner-shadow --details

# Force fixed benchmark server set (playwright/github/rag/trendradar/exa)
.\.venv\Scripts\python.exe examples/benchmarks/mcp_routing_shadow_eval.py --mode prefilter --server-source benchmark --details
```

## Dataset

Default dataset path:

`docs/plans/templates/mcp-routing-eval-samples.csv`

You can override with `--dataset <path>`.

## Server source

- `--server-source live` (default): load current MCP state from your runtime store/config.
- `--server-source benchmark`: use fixed benchmark servers for reproducible comparisons.

## Output metrics

- `first_step_accuracy`: first predicted server matches expected first step
- `sequence_accuracy`: predicted server sequence matches expected sequence
- `avg_predicted_steps`: mean number of predicted routing steps
- `fallback_rate`: planner fallback ratio (`execute_source=rule` or `gate_error_code` exists)
