# Project Recap — LLM Output Enforcement Study

## Goal

Evaluate how different LLMs respond to the same network configuration tasks when output format is enforced through different mechanisms: no enforcement (free-form YAML), API-level JSON mode, and prompt-engineering-only JSON enforcement.

---

## Infrastructure

### Provider

All API calls are routed through **[OpenRouter](https://openrouter.ai)** (`https://openrouter.ai/api/v1/chat/completions`), which provides a unified OpenAI-compatible endpoint across providers. The API key is loaded from a `.env` file via `python-dotenv`.

### Models tested
verification and minimal test:
'openai/gpt-40-mini'


```
openai/gpt-5.3-chat
openai/gpt-5.4
google/gemini-3.1-pro-preview
anthropic/claude-sonnet-4.6
```

Initial testing was done with `openai/gpt-4o-mini` before switching to the full model list.

### Schema context

`eos_designs.schema.yml` (the Arista AVD EOS Designs schema) is loaded once at startup and injected into every system prompt as a `<eos_designs_schema>` block. This gives all models a reference for correct AVD field names, types, and structure — regardless of the enforcement method used.

### Sampling parameters

No explicit sampling parameters are set in any script. OpenRouter applies its global default of **`temperature = 1.0`** and passes it to each provider. Relevant cross-provider differences:

| Parameter | OpenAI | Anthropic | Google Gemini |
|---|---|---|---|
| `temperature` default | 1.0 (range 0–2) | 1.0 **(range 0–1 only)** | 1.0 (range 0–2) |
| `top_p` default | 1.0 | unset | 0.95 |
| `top_k` | not supported | 5 (optional) | unset |
| `frequency_penalty` | 0 | not supported | not supported |

> Note: Claude's temperature range caps at 1.0, so it operates at maximum temperature relative to the 0–2 scale used by OpenAI and Gemini. For structured JSON tasks, a lower temperature (0.0–0.2) would produce more deterministic and schema-consistent output.

---

## Scripts

### 1. `baseline.py` → `output.txt`

**Method:** No output format enforcement. Free-form response.

- **System prompt:** Generic AVD/EOS YAML assistant.
- **User prompts:** Ask for YAML configurations directly.
- **API payload:** Standard messages array, no `response_format`.
- **Purpose:** Establishes a control baseline — what does each model produce when given no format constraints beyond the schema context?

---

### 2. `json_enforced.py` → `output_json_enforced.txt`

**Method:** API-level JSON mode enforcement via `response_format`.

- **API payload addition:**
  ```python
  "response_format": {"type": "json_object"}
  ```
- **System prompt:** Explicitly instructs the model to output a single valid JSON object only, with no markdown or prose. The word "JSON" must appear in the system prompt — this is a hard requirement from OpenAI when using `json_object` mode; failing to include it causes a 400 error.
- **User prompts:** Reworded from "Generate AVD YAML..." to "Generate a JSON object with the AVD configuration...".
- **Post-processing:** The raw response is passed through `json.loads()` and pretty-printed with `indent=2` if valid.
- **Provider compatibility:** OpenAI models honour `json_object` mode natively. For Anthropic and Google, OpenRouter passes the parameter through on a best-effort basis — it may be partially supported or silently ignored depending on the model version.

---

### 3. `prompt_enforced_only.py` → `output_prompt_enforced.txt`

**Method:** Prompt engineering only — no API-level `response_format` parameter.

- **API payload:** Standard messages array, no `response_format`.
- **System prompt (three layers of pressure):**
  1. Role framing as an AVD network engineer.
  2. Explicit instruction that the response will be passed directly to `json.loads()` in Python — any character outside the JSON will cause a parse error.
  3. Hard constraint: output ONLY the configuration JSON object, no reasoning, no markdown, no fences, no extra text.
  4. A final reminder line appended after the schema block.
- **User prompts:** Each prompt ends with an `_JSON_REMINDER` suffix reinforcing the JSON-only constraint. This suffix is stripped from the output file header for readability.
- **Validation:** Each response is attempted through `json.loads()`. The output file records `Valid JSON: YES` or `NO (model deviated from prompt)` per model per prompt, making compliance immediately visible without parsing the response manually.

---

## Output Structure

All three output files share the same format, appended continuously across runs:

```
######################################################################
# [METHOD LABEL] Run started: <ISO timestamp>
######################################################################

######################################################################
# PROMPT <N>
# <user prompt text>
######################################################################

============================================================
Timestamp  : <ISO timestamp>
Model      : <provider/model-name>
Valid JSON : YES | NO          ← prompt_enforced_only.py only
------------------------------------------------------------
User prompt sent:
  <user prompt>
------------------------------------------------------------
Response (<method label>):
<model response>
============================================================
```

Each run appends to the file rather than overwriting, so multiple runs accumulate in a single file for comparison.

---

## Prompt Variants

The same four tasks are used across all three scripts (YAML phrasing in `baseline.py`, JSON phrasing in the other two):

1. Configure BGP router-id using Loopback0 with IP `10.10.10.1/32` on a leaf switch
2. Configure VXLAN source interface to `Loopback1`
3. Configure VLAN `30` with VNI `1030` and override the EVPN route-target to `65000:1030`
4. Configure BGP peering with neighbor `10.0.0.2` remote-as `65100`

---

## File Summary

| File | Role | Output |
|---|---|---|
| `baseline.py` | Free-form YAML, no enforcement | `output.txt` |
| `json_enforced.py` | API-level `json_object` mode | `output_json_enforced.txt` |
| `prompt_enforced_only.py` | Prompt engineering JSON enforcement | `output_prompt_enforced.txt` |
| `eos_designs.schema.yml` | AVD schema injected as context | (input) |
| `.env` | `OPENROUTER_API_KEY=...` | (config) |
