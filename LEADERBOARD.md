# AVD Agent Model Leaderboard

Benchmark: 30 AVD network configuration tasks

| Rank | Model | Tasks | Syntax | Merge | Both | API Errors | Pass Rate |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | anthropic-claude-sonnet-4.6 | 30 | 30 | 30 | 30 | 0 | 100.0% |
| 2 | openai-gpt-5.4 | 30 | 30 | 30 | 30 | 0 | 100.0% |
| 3 | qwen2.5-coder-7b | 30 | 30 | 30 | 30 | 0 | 100.0% |
| 4 | google-gemini-3.1-pro-preview | 30 | 29 | 29 | 29 | 0 | 96.7% |

## Metric Definition

- **Syntax:** generated structured configuration passed syntax validation.
- **Merge:** generated configuration successfully merged into the AVD configuration.
- **Both:** task passed both syntax and merge validation.
- **Pass Rate:** Both / Tasks.

## Results

Qwen2.5-Coder 7B achieved 30/30 (100%) on the current syntax + merge validation benchmark.

These results measure structural/configuration validation success. They do not by themselves establish semantic equivalence, network correctness, latency, token efficiency, or cost.
