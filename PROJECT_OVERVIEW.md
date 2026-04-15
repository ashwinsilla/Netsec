# AVD-Eval: LLM-Powered Network Configuration Agent
### Project Documentation — Full Technical Reference

---

## Table of Contents

1. [What is Arista AVD?](#1-what-is-arista-avd)
2. [Project Goal](#2-project-goal)
3. [Phase 1 — Batch Evaluation (avd_harness.py)](#3-phase-1--batch-evaluation-avd_harnesspy)
4. [The 30 Tasks and Quadrant Structure](#4-the-30-tasks-and-quadrant-structure)
5. [What We Observed: Experiment 1 Results](#5-what-we-observed-experiment-1-results)
6. [Root Cause Analysis of Failures](#6-root-cause-analysis-of-failures)
7. [Phase 2 — Self-Healing (heal_configs.py)](#7-phase-2--self-healing-heal_configspy)
8. [Phase 3 — Production Agent (avd_agent.py)](#8-phase-3--production-agent-avd_agentpy)
9. [Are We Using RAG? Yes.](#9-are-we-using-rag-yes)
10. [Efficiency Optimizations](#10-efficiency-optimizations)
11. [Comment Preservation (subtree_merge.py)](#11-comment-preservation-subtree_mergepy)
12. [Function-by-Function Reference — avd_agent.py](#12-function-by-function-reference--avd_agentpy)
13. [Function-by-Function Reference — subtree_merge.py](#13-function-by-function-reference--subtree_mergepy)
14. [Library Reference](#14-library-reference)
15. [agent_runs/ Folder Structure](#15-agent_runs-folder-structure)
16. [Known Limitations](#16-known-limitations)

---

## 1. What is Arista AVD?

**Arista Validated Designs (AVD)** is an Ansible-based framework for automating network switch configuration at scale.

Instead of logging into each switch and typing CLI commands manually, a network engineer writes **YAML configuration files** that describe the desired state of the entire network fabric. AVD's `ansible-playbook build.yml` command reads those YAML files, validates them against a strict schema, and generates complete switch configurations (EOS CLI, structured configs, documentation).

### The fabric this project uses

The repo contains a single-DC Layer-3 Leaf-Spine (L3LS) fabric:

```
               [dc1-spine1]  [dc1-spine2]
                    │               │
      ┌─────────────┼───────────────┼─────────────┐
      │             │               │             │
[dc1-leaf1a] [dc1-leaf1b]  [dc1-leaf2a] [dc1-leaf2b]
      └─────MLAG─────┘        └─────MLAG─────┘
```

### The 5 group_vars files (the "inputs")

Every configuration lives in these 5 YAML files:

| File key | Path | What it controls |
|---|---|---|
| `fabric` | `group_vars/FABRIC/fabric_variables.yml` | NTP, DNS, BGP peer groups, AAA, timezone, MTU |
| `spines` | `group_vars/DC1_SPINES/spines.yml` | Spine nodes, BGP ASN, loopback pools |
| `l3leaves` | `group_vars/DC1_L3_LEAVES/l3_leaves.yml` | Leaf node groups, virtual router MAC, spanning tree, MLAG |
| `netsvcs` | `group_vars/NETWORK_SERVICES/network_services.yml` | Tenants, VRFs, VLANs, SVIs, IP helpers |
| `endpoints` | `group_vars/CONNECTED_ENDPOINTS/connected_endpoints.yml` | Server/host connections |

When `build.yml` runs, it validates these files against AVD's schema (thousands of rules) and fails with `[ERROR]:` messages if anything is wrong.

---

## 2. Project Goal

> **Can a frontier LLM make correct AVD configuration changes with zero human YAML knowledge?**

The project has three phases:

1. **Evaluate**: Run 30 structured AVD tasks against 3 frontier models. Measure build pass rates.
2. **Self-heal**: For tasks that failed, re-prompt the model with the exact Ansible error messages and see if it can self-correct.
3. **Agent**: Build a production-grade tool where a user describes any change in plain English and the system handles everything automatically — file identification, JSON generation, Ansible validation, and retry on failure.

---

## 3. Phase 1 — Batch Evaluation (avd_harness.py)

### What `avd_harness.py` does

The harness is an automated pipeline that:

1. Reads all 30 tasks from `avd_tasks.py`.
2. For each task, builds a prompt containing:
   - The full `eos_designs.schema.yml` (53 KB AVD schema).
   - The target YAML file's current content.
   - The `insertion_path` (exact location in the YAML tree to replace).
   - A precise `task_text` description.
3. Sends the prompt to each of 3 LLMs via OpenRouter.
4. Parses the returned JSON value.
5. Merges it into the YAML using `subtree_merge.py`.
6. Runs `ansible-playbook build.yml` to validate.
7. Records `PASS` / `FAIL` + error messages under `results/<model>/<quadrant>/<task>/`.

### Three models tested

| Model ID | Provider |
|---|---|
| `anthropic/claude-sonnet-4.6` | Anthropic |
| `openai/gpt-5.4` | OpenAI |
| `google/gemini-2.5-pro-preview` | Google |

### Results directory layout

```
results/
├── anthropic-claude-sonnet-4.6/
│   ├── quadrant_1_short_flat/
│   │   ├── p01_ntp_second_server/
│   │   │   ├── prompt.txt        ← full prompt sent to LLM
│   │   │   ├── raw_response.txt  ← exact LLM output
│   │   │   ├── parsed.json       ← extracted JSON value
│   │   │   ├── config.yaml       ← merged YAML file
│   │   │   └── ansible_output.txt ← build.yml stdout+stderr
│   │   └── ...
│   └── ...
├── openai-gpt-5.4/
└── google-gemini-3.1-pro-preview/
```

---

## 4. The 30 Tasks and Quadrant Structure

Tasks are organized into a **2×2 matrix** by two orthogonal dimensions:

| Axis | Short | Long |
|---|---|---|
| **Flat** (shallow insertion path) | Q1 | Q3 |
| **Deep** (deeply nested path) | Q2 | Q4 |

- **Short**: The JSON value the model must output is a scalar or small dict/list.
- **Long**: The model must output a large JSON structure (e.g., entire tenant with VRFs and SVIs).
- **Flat**: The insertion path is 1-3 levels deep (e.g., `["p2p_uplinks_mtu"]`).
- **Deep**: The insertion path is 4-7 levels deep (e.g., `["tenants", 0, "vrfs", 0, "svis", 0, "ip_address_virtual"]`).

### Task count per quadrant

| Quadrant | Tasks | Description |
|---|---|---|
| Q1 — Short Flat | 6 | Simplest: change a scalar or small list near the root |
| Q2 — Short Deep | 8 | Small value, deeply nested path |
| Q3 — Long Flat | 8 | Large JSON output, shallow path |
| Q4 — Long Deep | 8 | Hardest: large JSON + deep nesting |

### Selected tasks by quadrant

**Q1 (Short Flat):**
- P01: Add `1.pool.ntp.org` to `ntp_settings.servers`
- P03: Change `p2p_uplinks_mtu` from 1500 to 9214 (scalar)
- P06: Change `virtual_router_mac_address` (scalar string)
- P07: Change `spanning_tree_mode` to `rstp` (scalar string)

**Q2 (Short Deep):**
- P08: Add password to `bgp_peer_groups.evpn_overlay_peers`
- P11: Add `filters.tags` to a specific node_group
- P15: Set `ip_address_virtual` on a specific SVI (6 levels deep)

**Q3 (Long Flat):**
- P05: Add a third spine node (full node dict)
- P16: Add VRF20 with one SVI to TENANT1 (medium JSON, shallow)
- P22: Add a new server with full adapter definition

**Q4 (Long Deep):**
- P23: Add entirely new TENANT2 with VRF and SVI
- P24: Add a third leaf node_group with two nodes
- P27: Add two NTP servers AND set `prefer: true` on existing one
- P30: Two-field edit spanning defaults AND a specific node

---

## 5. What We Observed: Experiment 1 Results

### Overall pass rates

All three models performed surprisingly well on most tasks. Failures were concentrated in specific structural patterns, not random.

### Where every model failed: P06, P07, P27

Three tasks failed semantic validation across all models:

| Task | Expected output | What models returned | Why it failed |
|---|---|---|---|
| P06 — `virtual_router_mac_address` | `"00:1c:73:00:dc:01"` (raw string) | `{"virtual_router_mac_address": "00:1c:73:00:dc:01"}` (dict) | Models wrapped a scalar in a dict |
| P07 — `spanning_tree_mode` | `"rstp"` (raw string) | `{"spanning_tree_mode": "rstp"}` (dict) | Same scalar wrapping issue |
| P27 — `ntp_settings` | Full ntp_settings dict with 3 servers | Dict with `"prefer": true` on the first server | AVD schema has no `prefer` key — model hallucinated it |

### Gemini-specific issue: null content

Gemini (`google/gemini-2.5-pro-preview`) returned `content: null` with `completion_tokens=0` on several tasks. Root cause: the prompt format deviated from the structure Gemini expected, triggering its content filter.

Fix discovered: use the exact same XML tag names and terminal anchor wording proven in the original experiment. Any structural deviation caused Gemini to return empty.

---

## 6. Root Cause Analysis of Failures

### P06 / P07 — Scalar wrapping

The generation system prompt said:
> "The very first character of your JSON output MUST be `{` or `[`"

This instruction made models wrap everything in a dict. But `insertion_path = ["l3leaf", "defaults", "spanning_tree_mode"]` points to a scalar field — the correct output is the bare string `"rstp"`, not `{"spanning_tree_mode": "rstp"}`.

**Fix**: Added a SCALAR EXCEPTION clause to the system prompt:
> "SCALAR EXCEPTION: when the insertion_path targets a scalar field (string/int/bool), output the raw JSON scalar directly — e.g. `\"rstp\"` — without wrapping in `{...}`."

### P27 — `prefer` hallucination

AVD's `ntp_settings.servers` schema only accepts these keys: `name, burst, iburst, key, maxpoll, minpoll, version`. There is **no `prefer` key** — the first server in the list is automatically preferred by AVD. The task said "set prefer: true" which the model took literally and invented a non-existent key.

**Fix**: Include the exact targeted schema hint in the correction prompt showing all valid keys. Models fix this on retry when they see "Keys not listed above will cause Ansible to fail."

---

## 7. Phase 2 — Self-Healing (heal_configs.py)

### The algorithm

For the 3 failed tasks (P06, P07, P27), a self-healing loop was run:

```
For each failed task:
  For attempt in 1..3:
    1. Build prompt with:
       - Original task text
       - Full AVD schema
       - SCALAR EXCEPTION instruction
       - On attempt 2+: exact [ERROR]: lines from previous Ansible run
    2. Call LLM API
    3. Merge JSON into YAML
    4. Run ansible-playbook build.yml
    5. If PASS → done
       If FAIL → extract errors → carry forward to next attempt
```

### Self-healing results

| Task | Claude | GPT | Gemini |
|---|---|---|---|
| P06 — virtual router MAC | PASS attempt 1 | PASS attempt 1 | PASS attempt 1 |
| P07 — spanning tree mode | PASS attempt 1 | PASS attempt 1 | PASS attempt 1 |
| P27 — NTP multi-edit | PASS attempt 2 | PASS attempt 2 | PASS attempt 2 |

**P27 required attempt 2** for all models — the schema hint in the correction message taught them that `prefer` is invalid and to remove it.

---

## 8. Phase 3 — Production Agent (avd_agent.py)

### What it does

`avd_agent.py` is a production tool where **anyone can describe a network configuration change in plain English**. The agent handles everything:

1. Figures out which YAML file and exact path to edit (via RAG + LLM).
2. Generates the correct JSON value via LLM.
3. Temporarily patches the YAML file.
4. Runs `ansible-playbook build.yml` to validate.
5. If validation fails: feeds exact error messages back to the LLM and retries (up to 3 times).
6. On success: applies the change permanently. On all failures: restores the original file.

### Two-phase architecture

The agent makes **two LLM calls** per request:

```
User Request
     │
     ▼
┌──────────────────────────────────────────┐
│  PHASE 1: Intent Resolution (API call 1) │
│                                          │
│  Input:  user request + RAG-retrieved    │
│          file structure summaries         │
│  Output: { file_key, insertion_path,     │
│            task_text, confidence }        │
└──────────────────────────────────────────┘
     │
     ▼  [User confirms]
     │
     ▼
┌──────────────────────────────────────────┐
│  PHASE 2: Generate + Validate Loop       │
│           (API call 2, up to 3×)         │
│                                          │
│  Input:  intent + target YAML section    │
│          + schema hint                   │
│  Output: JSON value → merge → Ansible    │
│  On fail: errors → next attempt          │
└──────────────────────────────────────────┘
     │
     ▼
  PASS: write file  |  FAIL: restore backup
```

**Why two phases?**

| Phase | Purpose | Needs |
|---|---|---|
| Phase 1 | Navigation — *where* to change | All file structures, can handle ambiguity |
| Phase 2 | Generation — *what* value to put | Exact schema, precise JSON output |

Combining them into one call would make the model simultaneously do routing + generation + schema compliance + JSON formatting — four different cognitive tasks. Two focused calls are more reliable.

### Self-healing loop detail

```
attempt 1: no error context
    → LLM generates JSON
    → merge into YAML
    → run ansible-playbook
    → PASS: done
    → FAIL: extract [ERROR]: lines

attempt 2: inject errors in <correction_notes>
    → LLM sees: "VALIDATION FAILED — you MUST fix:
                  [ERROR]: Invalid key 'prefer' under ntp_settings.servers"
    → LLM corrects itself
    → run ansible-playbook again

attempt 3: if still failing, inject errors again
    → last chance

If all 3 fail: restore backup, report failure
```

### CLI interface

```bash
# Pass request as argument
python3 avd_agent.py "Add NTP server 3.pool.ntp.org"

# Interactive prompt
python3 avd_agent.py

# Preview what file/path it would target (no changes)
python3 avd_agent.py --intent-only "Change spanning tree mode to MSTP"

# Full dry-run: validate but don't permanently write
python3 avd_agent.py --dry-run "Add VRF VRF30 to TENANT1"

# Skip confirmation prompt
python3 avd_agent.py -y "Change p2p_uplinks_mtu to 9214"

# Use a different model
python3 avd_agent.py --model openai/gpt-4o "Change spine BGP ASN to 65200"
```

---

## 9. Are We Using RAG? Yes.

### What RAG is

**Retrieval-Augmented Generation (RAG)** is a technique where, instead of giving the LLM everything, you first *retrieve* only the most relevant context and give it only that. This improves accuracy and reduces cost.

Classic RAG uses vector embeddings (neural network representations of text) for retrieval. Our implementation uses **TF-IDF** (Term Frequency-Inverse Document Frequency) — a mathematically well-founded retrieval approach that is:
- Entirely local (no embedding API calls)
- Zero extra latency
- 9/9 correct on all tested queries

### Our RAG pipeline

```
User: "Change spanning tree mode to MSTP"
         │
         ▼
┌─────────────────────────────────────────┐
│  _build_rag_corpus()                     │
│  For each of 5 files:                    │
│    doc = FILE_PURPOSES                   │
│          + _FILE_KEYWORDS (synonyms)     │
│          + all YAML keys + scalar values │
└─────────────────────────────────────────┘
         │  5 documents
         ▼
┌─────────────────────────────────────────┐
│  TfidfVectorizer (sklearn)               │
│  - Unigrams + bigrams                    │
│  - English stop words removed            │
│  - sublinear_tf (log dampening)          │
│  → 5 × N TF-IDF matrix                  │
└─────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  Query vectorized → cosine similarity   │
│  vs each document vector                │
│  → scores: [0.41, 0.12, 0.08, 0.05, 0.02] │
└─────────────────────────────────────────┘
         │
         ▼
  top_k=2 retrieved: ['l3leaves', 'endpoints']
         │
         ▼
  Phase 1 prompt includes:
    - Full compact structure for l3leaves ← RAG match
    - Full compact structure for endpoints ← RAG match
    - One-line summary for: fabric, spines, netsvcs
```

### Why TF-IDF instead of neural embeddings?

| | TF-IDF (ours) | Neural embeddings |
|---|---|---|
| API calls | 0 | 1 per query (or local model) |
| Latency | ~5ms | 50-200ms |
| Dependencies | scikit-learn | openai/sentence-transformers |
| Accuracy (5 files) | 9/9 | Likely similar for structured domain |
| Scales to 100+ files | Degrades | Stays robust |

For this 5-file repo, TF-IDF is sufficient and simpler. For a large enterprise AVD repo with 100+ group_vars files, neural embeddings would be the better choice.

### RAG accuracy results

| Query | Expected file | RAG top-1 |
|---|---|---|
| "Add NTP server 3.pool.ntp.org" | fabric | fabric ✓ |
| "Change spanning tree mode to MSTP" | l3leaves | l3leaves ✓ |
| "Change spine BGP ASN to 65200" | spines | spines ✓ |
| "Add a new tenant with VRF and VLAN" | netsvcs | netsvcs ✓ |
| "Add a new server connected to leaf ports" | endpoints | endpoints ✓ |
| "Change virtual router MAC address" | l3leaves | l3leaves ✓ |
| "Add DNS server 8.8.8.8" | fabric | fabric ✓ |
| "Set the timezone to UTC" | fabric | fabric ✓ |
| "Add SVI 30 to VRF10 in TENANT1" | netsvcs | netsvcs ✓ |

**9/9 — 100% top-1 accuracy.**

---

## 10. Efficiency Optimizations

The original batch evaluation sent ~110 KB of text per LLM call. The agent reduces this dramatically:

### Token savings per call

| Component | Original | Optimized | Saving |
|---|---|---|---|
| Phase 1 — file context | ~50 KB (full YAML of all 5 files) | ~2 KB (compact structure of RAG top-2 only) | **96%** |
| Phase 2 — schema | ~53 KB (full eos_designs.schema.yml) | ~0.5 KB (targeted schema_hint for the specific path) | **99%** |
| Phase 2 — YAML context | Full file (5-30 KB) | Target section only (0.2-2 KB) | **90%** |
| **Total** | **~110 KB** | **~5 KB** | **~95%** |

### How each optimization works

**1. RAG retrieval (Phase 1)**
Instead of sending all 5 files, send only the top-2 retrieved files' compact structures. The remaining 3 files are one-line entries only.

**2. `_yaml_compact()` — compact structure summaries**
For the retrieved files, instead of raw YAML, send a structural outline: key names, scalar values (truncated to 60 chars), list sizes, and first list item. ~10-20x smaller than raw YAML.

**3. `_schema_hint()` — targeted schema (Phase 2)**
Instead of the full 53 KB `eos_designs.schema.yml`, extract just the schema node for the specific `insertion_path` from the pyavd pickle. This gives the model exactly the valid keys/types for what it's generating.

**4. `_extract_target_section()` — relevant YAML only (Phase 2)**
Instead of sending the whole YAML file as context, extract only the top-level key being edited. E.g., for `insertion_path = ["ntp_settings", "servers"]`, send only the `ntp_settings:` block.

---

## 11. Comment Preservation (subtree_merge.py)

### The problem

YAML comments (`# like this`) are metadata — they are not part of the parsed data model. The original code used PyYAML:

```python
data = yaml.safe_load(f)   # parses data, DISCARDS all comments
yaml.dump(doc, ...)         # serializes data, no comments to write back
```

After a merge, the `# Define variables for all nodes of this type` comments in `spines.yml` would disappear permanently.

### The fix: ruamel.yaml round-trip mode

`ruamel.yaml` is a drop-in replacement for PyYAML that has a **round-trip parser** (`typ='rt'`) which stores comments as first-class nodes in the parsed tree (`CommentedMap`, `CommentedSeq`) and re-emits them on dump.

```python
from ruamel.yaml import YAML
ryaml = YAML()              # defaults to round-trip mode
ryaml.preserve_quotes = True
doc = ryaml.load(file)      # CommentedMap — keeps all comment metadata
# ... merge ...
ryaml.dump(doc, buf)        # re-emits comments exactly where they were
```

### Three-layer comment strategy

**Layer 1 — `---` document start marker**
ruamel.yaml drops the YAML document start marker on dump. Fixed by detecting `startswith("---")` in the raw file text and prepending it back after dump.

**Layer 2 — Recursive list merge (`_smart_merge_list`)**
When the LLM replaces a list (e.g., `ntp_settings.servers`), a plain Python list is assigned. This overwrites the `CommentedSeq`. Instead:
- Match items between old `CommentedSeq` and new list by `name`/`id` field.
- For matching items: update the original `CommentedMap` in-place (preserving comment attributes).
- For new items: append as plain Python (no comments, correct behavior).

**Layer 3 — Recursive dict merge (`_smart_update_field`)**
Extends layer 2 to nested dicts. When updating a field whose value is a `CommentedMap`, update it key-by-key rather than replacing it wholesale. Recurses indefinitely so comments at any nesting depth are preserved.

### Before vs. after

**Before** (PyYAML):
```yaml
# AFTER MERGE — ALL COMMENTS GONE
spine:
  defaults:
    bgp_as: 65200
```

**After** (ruamel.yaml + smart merge):
```yaml
---
type: spine

spine:
  # Define variables for all nodes of this type
  defaults:
    # Autonous System Number for BGP
    bgp_as: 65200
  # Define variables per node
  nodes:
    # The Node Name is used as "hostname"
  - name: dc1-spine1
```

**Result: 13/13 comments preserved** in `network_services.yml` (most comment-rich file), including comments inside deeply nested dicts.

---

## 12. Function-by-Function Reference — avd_agent.py

### Constants

| Name | Value | Purpose |
|---|---|---|
| `REPO_ROOT` | `Path(__file__).resolve().parent` | Absolute path to the repo root, derived at runtime |
| `FILE_MAP` | dict of 5 key → path strings | Maps short file keys to their repo-relative YAML paths |
| `FILE_PURPOSES` | dict of 5 key → description strings | Human-readable descriptions of what each file controls |
| `_FILE_KEYWORDS` | dict of 5 key → keyword strings | Domain synonym expansions for the RAG corpus |
| `OPENROUTER_URL` | `https://openrouter.ai/api/v1/chat/completions` | Unified API endpoint for all three frontier models |
| `DEFAULT_MODEL` | `anthropic/claude-sonnet-4.6` | Model used when `--model` flag is not specified |
| `MAX_RETRIES` | `3` | Maximum generation+validation attempts per request |
| `API_TIMEOUT` | `180` | Seconds before an LLM API call is timed out |
| `HTTP_RETRIES` | `3` | Retries on transient HTTP errors (429 rate limit, etc.) |
| `RUNS_DIR` | `agent_runs/` | Directory where all run artifacts are written |
| `AVD_SCHEMA_PICKLE` | Path to pyavd's binary schema | Machine-readable AVD schema used to generate schema hints |

### Regex patterns

| Name | Pattern | Purpose |
|---|---|---|
| `_THINK_STRIP` | `<thinking>.*?</thinking>` | Removes thinking blocks from LLM responses before JSON parsing |
| `_THINK_INNER` | `<thinking>(.*?)</thinking>` | Extracts the interior of a thinking block (last-resort JSON fallback) |
| `_MD_FENCE` | `` ```(?:json)?\s*\n?(.*?)\n?``` `` | Strips markdown code fences from LLM responses |

---

### `_load_api_key() → str`

Loads the OpenRouter API key from `.env` (via `python-dotenv`). Handles common formatting mistakes: surrounding quotes, `Bearer ` prefix, BOM character (`\ufeff`), extra whitespace. Tries both `OPENROUTER_API_KEY` and `OPEN_ROUTER_API_KEY` environment variable names.

---

### `_load_avd_schema() → dict`

Deserializes the pyavd schema pickle file at `AVD_SCHEMA_PICKLE`. Returns the full AVD schema as a nested Python dict. Returns `{}` if the file doesn't exist (safe — `_schema_hint` handles empty schema gracefully).

---

### `_walk_schema(schema, path) → dict | None`

Walks the AVD schema tree following `path` (list of string keys and integer indices). String segments navigate `node["keys"][segment]`. Integer segments navigate `node["items"]` (into list item schemas). Returns the schema node at that path, or `None` if the path doesn't exist in the schema.

---

### `_schema_hint(schema, insertion_path) → str`

Generates a compact, human-readable schema constraint block for the specific `insertion_path`. Shows:
- Node type (`str`, `dict`, `list`, etc.)
- `valid_values` if the schema restricts to an enum
- `required_keys` and `optional_keys` for dict nodes (depth ≤ 2)
- Item schema for list nodes

This replaces the full 53 KB schema in Phase 2 prompts. The model only needs to know what's valid at the node it's editing, not the entire AVD schema.

---

### `_yaml_compact(path, max_depth=3, max_list_items=1) → str`

Produces a ~10-20x smaller structural summary of a YAML file for use in Phase 1 prompts. Rules:
- Dict keys are always shown.
- Scalar values are shown truncated to 60 characters.
- Lists show only the first `max_list_items` item (to illustrate structure).
- At `max_depth`, dicts are shown as `{key1, key2, ...}` instead of recursing.

Example output for `fabric_variables.yml`:
```
ntp_settings:
  server_vrf: 'use_mgmt_interface_vrf'
  servers:
  -
    name: '0.pool.ntp.org'
    … [2 more item(s)]
```

---

### `_extract_target_section(context_yaml, top_key) → str`

Extracts only the top-level YAML section identified by `top_key` from a full YAML string. For `top_key = "ntp_settings"`, returns:
```yaml
ntp_settings:
  server_vrf: use_mgmt_interface_vrf
  servers:
  - name: 0.pool.ntp.org
  - name: 1.pool.ntp.org
```
The generation model only needs to see the section it's editing. Returns the full YAML string as fallback if extraction fails.

---

### `_build_rag_corpus() → dict[str, str]`

Builds a rich text document for each of the 5 file keys. Each document = `FILE_PURPOSES` description + `_FILE_KEYWORDS` synonym expansion + all YAML keys and scalar values (flattened recursively, depth ≤ 4, first 3 list items). The result is a corpus that TF-IDF vectorization can use to compute semantic similarity with user queries.

The corpus is built **from live file contents** — it updates automatically as the group_vars files change.

---

### `_rag_retrieve(query, top_k=2) → list[str]`

**The RAG retriever.** Returns the `top_k` most relevant file keys for the given query using TF-IDF cosine similarity.

Pipeline:
1. Build corpus via `_build_rag_corpus()`.
2. Fit `TfidfVectorizer` with unigrams + bigrams, English stop words, `sublinear_tf=True` (log dampening for common terms), 5000 max features.
3. Transform corpus + query into TF-IDF vectors.
4. Compute cosine similarity between query vector and each document vector.
5. Return the top-k file keys sorted by descending score.

Falls back to returning all 5 file keys if scikit-learn is unavailable.

---

### `_call_llm(session, api_key, model, system, user) → tuple[str|None, str|None]`

Async function that calls the OpenRouter API. Returns `(content, None)` on success or `(None, error_message)` on failure. Handles:
- HTTP 429 (rate limit): backs off with `5 * attempt` seconds.
- Other HTTP errors: returns the error body.
- `content: null` response (Gemini-specific): caught explicitly and returned as an error.
- Connection timeouts and exceptions.

---

### `_parse_json(text) → tuple[object, str]`

Robust JSON extractor that handles all the ways frontier models format their responses. Steps:
1. Strip `<thinking>...</thinking>` blocks entirely.
2. Strip markdown code fences (`` ```json ... ``` ``).
3. Try `json.loads()` directly on the cleaned text.
4. Try bracket scanning: find the first `{` or `[`, walk forward matching brackets, try `json.loads()` on the extracted snippet.
5. Last resort: try parsing the interior of a `<thinking>` block (some models accidentally put JSON inside their thinking).

Returns `(parsed_python_value, json_string_used)`. Raises `ValueError` if nothing works.

---

### `_ansible_env() → dict`

Builds the environment dict for running `ansible-playbook`. Removes conflicting `ANSIBLE_INVENTORY` and `ANSIBLE_INVENTORY_FILE` vars, prepends `.venv/bin` to `PATH` so the virtualenv's ansible is used, and sets `ANSIBLE_CONFIG` to the repo's `ansible.cfg`.

---

### `_run_ansible() → tuple[bool, str]`

Runs `ansible-playbook build.yml -i inventory.yml` as a subprocess. Returns `(True, output)` if return code is 0, `(False, output)` otherwise. Captures both stdout and stderr, returns the last 8000 characters (enough for all error messages without overwhelming memory). Times out after 300 seconds.

---

### `_extract_errors(output, limit=30) → list[str]`

Scans Ansible output for lines containing `[ERROR]:` or starting with `fatal:`. Deduplicates and returns up to 30 unique error lines. These are fed back to the LLM in `<correction_notes>` on retry attempts.

---

### `_build_intent_prompt(user_request) → str`

Assembles the Phase 1 prompt. Steps:
1. Calls `_rag_retrieve(user_request, top_k=2)` to get the 2 most relevant file keys.
2. For retrieved files: includes `[file_key]`, purpose, and full `_yaml_compact()` structure.
3. For non-retrieved files: includes only a one-line name + purpose.
4. Appends `_INTENT_OUTPUT_SPEC` which defines the exact JSON schema the model must return.

---

### `_resolve_intent(user_request, session, api_key, model, work_dir) → dict | None`

Runs Phase 1. Calls `_build_intent_prompt()`, sends to LLM via `_call_llm()`, parses the JSON response with `_parse_json()`, validates required fields (`file_key`, `context_file`, `insertion_path`, `task_text`), and writes `intent_prompt.txt`, `intent_raw.txt`, `intent.json` to `work_dir`. Returns the parsed intent dict or `None` on any error.

---

### `_build_gen_prompt(intent, context_yaml, schema_hint, errors, attempt) → str`

Assembles the Phase 2 generation prompt. Contains:
- `<schema_reference>`: the targeted `schema_hint` for the specific path.
- `<merge_target>`: the `context_file` path, `insertion_path`, and shape rules (scalar / dict / list).
- `<configuration_context>`: the extracted YAML section (not the whole file).
- `<task>`: the precise task description from Phase 1.
- `<correction_notes>`: only on attempt ≥ 2, contains the Ansible error lines that must be fixed.
- Terminal anchor: exact wording proven to make all three frontier models produce valid JSON.

---

### `_generate_and_validate(intent, session, api_key, model, schema, dry_run, work_dir) → tuple[bool, str]`

The Phase 2 generation + validation loop. For each of `MAX_RETRIES` attempts:

1. Build prompt via `_build_gen_prompt()`.
2. Call LLM via `_call_llm()`.
3. Parse JSON via `_parse_json()`.
4. Merge into YAML via `merge_yaml_file()` from `subtree_merge.py`.
5. Backup original file with `shutil.copy2()`.
6. Write merged content to target file.
7. Run `_run_ansible()`.
8. **On PASS**: leave the merged file in place (change is applied), break.
9. **On FAIL**: restore from backup via `shutil.copy2()`, extract errors, carry them into next attempt.

The `finally` block ensures the backup is always restored on failure even if an unexpected exception occurs.

---

### `_run_agent(user_request, args) → bool`

Top-level async orchestrator. Loads API key, creates a timestamped `work_dir` under `agent_runs/`, prints the header banner, runs Phase 1 (`_resolve_intent`), shows the resolved intent to the user, asks for confirmation (unless `-y`), then runs Phase 2 (`_generate_and_validate`). Prints the final result.

---

### `main()`

CLI entry point using `argparse`. Parses arguments (`request`, `--model`, `--dry-run`, `--intent-only`, `-y/--yes`, `-v/--verbose`). If no `request` argument, prompts the user interactively. Runs `_run_agent()` inside `asyncio.run()`.

---

## 13. Function-by-Function Reference — subtree_merge.py

### `_item_key(item) → Any`

Returns a hashable identity key for a list item. For dict items, tries `item.get("name")` first, then `item.get("id")`. Returns the item itself for scalars. Used by the smart merge to match items between the original `CommentedSeq` and the new list.

---

### `_smart_update_field(parent, key, new_val) → None`

The core comment-preserving update primitive. Sets `parent[key] = new_val`, but:
- If current value is a ruamel.yaml `CommentedSeq` and `new_val` is a list → calls `_smart_merge_list()`.
- If current value is a ruamel.yaml `CommentedMap` and `new_val` is a dict → updates key-by-key (in-place), recursing into nested structures.
- Otherwise → plain assignment (scalars, type mismatches).

This recursion is what ensures comments at **all nesting depths** are preserved, not just the top level.

---

### `_smart_merge_list(original_seq, new_list) → CommentedSeq`

Merges a plain Python list (`new_list`) into a ruamel.yaml `CommentedSeq` (`original_seq`), preserving comment metadata on matched items.

For each item in `new_list`:
- Find matching item in `original_seq` by `_item_key()`.
- If found: update the original `CommentedMap` via `_smart_update_field()` (preserving comments).
- If not found: append as a new item (no comments, correct behavior for new additions).

Copies sequence-level comment attributes from the original seq to the result.

---

### `insert_subtree(original_dict, insertion_path, generated_json) → Any`

Walks `original_dict` following `insertion_path` and sets the final key to `generated_json`. For list parents, the final key must be an integer index. For dict parents, uses `_smart_update_field()` so comment preservation happens automatically.

---

### `merge_yaml_file(repo_root, context_file, insertion_path, generated_json) → str`

Main entry point. Reads the file, loads with ruamel.yaml round-trip mode, calls `insert_subtree()`, dumps back to string, restores `---` marker if original had one. Falls back to PyYAML if ruamel.yaml is not installed.

---

## 14. Library Reference

| Library | Used in | Why |
|---|---|---|
| `aiohttp` | `avd_agent.py` | Async HTTP client for non-blocking LLM API calls. Faster than `requests` for I/O-bound work. |
| `asyncio` | `avd_agent.py` | Python's async event loop. Required by `aiohttp`. The agent's LLM calls are `async def`. |
| `argparse` | `avd_agent.py` | Standard library CLI argument parser. |
| `json` | `avd_agent.py` | Standard library JSON parser/serializer. |
| `pickle` | `avd_agent.py` | Deserializes the pyavd binary schema file (`.pkl`). |
| `re` | `avd_agent.py` | Regular expressions for stripping `<thinking>` blocks and markdown fences. |
| `shutil` | `avd_agent.py` | `shutil.copy2()` for file backup/restore with metadata preserved. |
| `subprocess` | `avd_agent.py` | Runs `ansible-playbook build.yml` as a child process and captures output. |
| `pathlib.Path` | `avd_agent.py` | Object-oriented file paths — cleaner than `os.path`. |
| `python-dotenv` | `avd_agent.py` | Loads `OPENROUTER_API_KEY` from `.env` file. |
| `scikit-learn` | `avd_agent.py` | `TfidfVectorizer` + `cosine_similarity` for the RAG retriever. |
| `numpy` | `avd_agent.py` | `np.argsort()` to rank TF-IDF similarity scores. |
| `ruamel.yaml` | `subtree_merge.py` | Round-trip YAML parser that preserves comments, `---` marker, and formatting. |
| `yaml` (PyYAML) | `avd_agent.py`, `subtree_merge.py` | Standard YAML parser for non-roundtrip use (prompt building, schema hints). Fallback in subtree_merge if ruamel unavailable. |
| `pyavd` | Schema file | Provides `eos_designs.schema.pickle` — the machine-readable AVD schema used by `_schema_hint()`. |
| `ansible` / `ansible-playbook` | Subprocess | The actual AVD build/validation tool. Not imported — invoked as a shell command. |

---

## 15. agent_runs/ Folder Structure

Every run of `avd_agent.py` creates a timestamped directory:

```
agent_runs/
└── 20260415T143022Z/              ← UTC timestamp of the run
    ├── intent_prompt.txt          ← full Phase 1 prompt (what was sent to the LLM)
    ├── intent_raw.txt             ← raw LLM response for intent resolution
    ├── intent.json                ← parsed intent: file_key, insertion_path, task_text
    ├── attempt_1/
    │   ├── prompt.txt             ← Phase 2 generation prompt (no correction notes)
    │   ├── raw_response.txt       ← raw LLM response (may include <thinking> block)
    │   ├── parsed.json            ← JSON value extracted from LLM response
    │   ├── config.yaml            ← full merged YAML file sent to Ansible
    │   └── ansible_output.txt     ← stdout + stderr from ansible-playbook build.yml
    ├── attempt_2/                 ← only present if attempt 1 failed
    │   ├── prompt.txt             ← same structure, but now has <correction_notes>
    │   ├── raw_response.txt
    │   ├── parsed.json
    │   ├── config.yaml
    │   └── ansible_output.txt
    └── attempt_3/                 ← only present if attempt 2 also failed
        └── ...
```

**What to look at when debugging:**
- `intent.json` → did the agent correctly identify the file and path?
- `attempt_N/prompt.txt` → what was the model told to do?
- `attempt_N/parsed.json` → what did the model produce?
- `attempt_N/ansible_output.txt` → why did the build fail?
- `attempt_2/prompt.txt` → did the correction notes include the right errors?

---

## 16. Known Limitations

| Limitation | Impact | Potential fix |
|---|---|---|
| Semantic validation gap | Ansible passing ≠ user intent fully satisfied. E.g., adding NTP servers passes even if ordering was wrong. | Post-build LLM check: re-read the merged YAML and confirm intent was met. |
| New items lose their comments | Comments on newly added list items are not added (they don't exist in the original to copy). | Only affects new items; existing items' comments are preserved. |
| TF-IDF RAG may not scale to 100+ files | With very large repos, bigram TF-IDF loses semantic precision. | Replace with neural embeddings (e.g., `sentence-transformers`) for large repos. |
| No multi-file edits | A single request can only edit one group_vars file. | Intent resolution would need to return multiple targets. |
| Integer list indices are brittle | `insertion_path = ["tenants", 0, "vrfs"]` assumes TENANT1 is always index 0. | Phase 1 could use name-based lookup and resolve indices dynamically. |
| `ansible-playbook` must be in the venv | The agent requires the venv to be set up with AVD dependencies. | The `_ansible_env()` function handles this, but the venv must exist. |

---

*Generated by avd_agent.py project — Arista AVD LLM Configuration Agent*
