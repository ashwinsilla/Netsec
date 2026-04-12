# Do Frontier LLMs Still Need Constrained Decoding to Produce Correct Structured Output?

## An Empirical Evaluation Using Arista AVD Network Configurations

**Course:** 18-662 — Principles and Engineering Application of AI  
**Team Members:**  
- Milo Mendoza (alvarom)  
- Ashwin Silla (asilla)  

**Date:** March 2026

---

## Abstract

This report presents our progress on evaluating whether state-of-the-art large language models (LLMs) can reliably produce schema-compliant structured output without relying on constrained decoding mechanisms. Our project initially targeted fine-tuning an LLM for automated network configuration generation using Arista Validated Designs (AVD). During early baseline experiments, we observed that the core challenge was not the models' domain knowledge per se, but rather whether enforcement mechanisms (API-level constrained decoding or prompt engineering) are still necessary for correct structured output. We pivoted to investigate this question directly, using AVD schema compliance as an illustrative case study—the underlying question of whether constrained decoding remains necessary applies broadly to any domain requiring schema-conformant JSON generation. We conducted experiments across four frontier models (GPT-5.3, GPT-5.4, Gemini 3.1 Pro, Claude Sonnet 4.6) under three output enforcement regimes: free-form baseline, API-level JSON mode, and prompt-only JSON enforcement. Our preliminary results show that while all models achieve 100% JSON syntax validity under both enforcement methods, semantic accuracy—conformance to the correct schema field names and nesting—remains inconsistent at 69–75%, with no enforcement method consistently outperforming the other. A parallel literature review of 16 recent papers reveals an active industry shift toward decoupled reasoning architectures and reinforcement learning from parser feedback [14][17][20], suggesting that inference-time constrained decoding may eventually become unnecessary but is not yet obsolete. The remaining six weeks of the project will focus on systematic, statistically grounded evaluation at scale.

---

## 1. Introduction

Infrastructure-as-Code (IaC) frameworks such as Arista Validated Designs (AVD) enable repeatable network deployments through YAML-based configuration repositories. These repositories must conform to strict, vendor-specific schemas—the AVD `eos_designs` schema alone spans over 1,300 lines of nested type definitions, constraints, and conditional defaults [7]. Generating these configurations manually requires deep familiarity with both the schema and the underlying network design patterns. Prior benchmarks have shown that general-purpose LLMs struggle to produce valid low-level network configurations and frequently hallucinate commands or parameters [1][2].

Our project began with a straightforward hypothesis: a language model fine-tuned on AVD repositories would outperform general-purpose models at generating schema-compliant YAML. However, while constructing baselines to test this hypothesis, we encountered a more fundamental question. When we fed the full `eos_designs` schema as context and asked frontier models to produce AVD configurations, the models demonstrated substantial domain understanding—they knew the right fields, correct nesting patterns, and appropriate defaults. The dominant failure mode was not ignorance of the domain, but formatting compliance: models would wrap JSON in markdown fences, include explanatory prose, use incorrect top-level keys, or generate structurally valid but semantically incorrect output.

This observation led us to pivot. The more pressing and generalizable research question is not whether models can learn AVD-specific knowledge, but whether current frontier models need constrained decoding—the inference-time mechanism that forces token-by-token adherence to a grammar [14]—to produce correct structured output. If frontier models can already natively produce correct structured output with high reliability, the engineering overhead of constrained decoding engines becomes unnecessary.

**It is important to note** that while we use Arista AVD as the evaluation domain for this study, the research question is domain-agnostic. AVD configuration generation serves as a demanding case study because it involves a large, deeply nested schema with strict field-name and type requirements. The findings on constrained decoding effectiveness should generalize to any task requiring LLMs to produce schema-conformant JSON—including API tool calling, database record generation, and structured data extraction. Our goal is not to build a product for network configuration generation but to empirically evaluate the current necessity of constrained decoding mechanisms across frontier models.

**Revised hypothesis:** We hypothesize that frontier LLMs (early 2026 generation) can produce syntactically valid JSON at near-100% rates when given explicit formatting instructions, but that semantic accuracy—using the correct schema-defined field names and structure—remains unreliable regardless of enforcement method, indicating that constrained decoding addresses the wrong layer of the problem.

---

## 2. Related Work

Our literature review spans 16 papers organized into four thematic phases, representing the rapid evolution of thinking around constrained decoding from mid-2025 through early 2026.

### 2.1 Constrained Decoding Engines and Their Bottlenecks

Constrained decoding uses a state machine (typically a pushdown automaton) to mask invalid tokens during autoregressive generation. The constrained distribution is defined as $P(s \mid c) \propto P_{\text{LM}}(s) \cdot c(s)$, where $c(s) \in \{0, 1\}$ represents the binary constraint mask. While this guarantees syntactic compliance, several studies have documented significant engineering costs:

- **Pre3** [9] (July 2025) showed a ~37.5% latency penalty at larger batch sizes due to the inherent difficulty of parallelizing state validation across GPUs.
- **WGRAMMAR** [10] (July 2025) documented massive Time-to-First-Token overheads and proposed decomposing constraints into pre-compiled static rules.
- **XGrammar 2** [12] (January 2026) shifted the industry toward Earley-parser-based algorithms with "TagDispatch" semantics, achieving up to 6× speedup over standard pushdown automata.
- **Beyond Prompts** [13] (January 2026) identified "boundary mismatch" problems where tokenizer chunking conflicts with character-level grammar rules, forcing the engine to halt, unpack tokens, and rebuild masks.

A critical architectural limitation is that closed SOTA providers (OpenAI, Anthropic, Google) only support constrained decoding for JSON, not YAML, because JSON is a context-free grammar (stateless, cheap to evaluate) while YAML is context-sensitive (indentation-dependent), creating unacceptable latency.

### 2.2 The Reasoning Tax

A second wave of research demonstrated that constrained decoding degrades model intelligence—the "quality-validity tradeoff." These papers are particularly central to our hypothesis:

- **Navigating the Impact of Structured Output** [14] (September 2025) used causal discovery to show that forced schemas act as a "collider," pushing models down non-canonical tokenization paths that measurably reduce factual accuracy and reasoning capacity. This paper directly informs our observation that constrained decoding does not improve semantic accuracy.
- **CRANE** [17] (late 2025/early 2026) provided mathematical proof that overly restrictive grammars reduce the computational model's complexity class, preventing the intermediate calculations required for chain-of-thought reasoning. This is the theoretical foundation for our finding that JSON mode can degrade accuracy relative to prompt-only enforcement.
- **BoostCD** [16] (late 2025) acknowledged semantic errors from rigid constraints and proposed a boosting method using a secondary model to arbitrate between constrained and unconstrained outputs.

### 2.3 Decoupled Architectures

To preserve reasoning while guaranteeing syntax, the industry adopted "reason first, constrain second" approaches:

- OpenAI's o1/o3 series uses hidden chain-of-thought scratchpads, activating constrained decoding only for the final visible output.
- Anthropic's Claude 3.7 separates generation into unconstrained `<thinking>` blocks and constrained `<tool_use>` blocks.
- Google advocates pipeline decoupling—using a heavy reasoning model followed by a lightweight constrained parser.

### 2.4 Native Structure via Reinforcement Learning

The most recent frontier eliminates inference-time constraints entirely by training models to natively respect syntax. This body of work is critical because it explains *why* frontier models in our experiments may have already internalized JSON syntax rules:

- **Training LLMs for Strict JSON Schema Adherence via RL** [20] (February 2025) achieved a 98.7% native valid output rate using standard JSON parsers as the RL reward signal, without any inference-time constraints. This result directly predicts our finding that frontier models achieve 100% syntax validity under prompt-only enforcement.
- **Close the Loop** [23] (December 2025) used GRPO in a multi-agent self-evolution loop, boosting tool-use accuracy by 258%.
- **SimpleTool** [25] (February 2026) injected special tokens representing API boilerplate during fine-tuning, allowing models to natively output compressed tool calls without an external state machine.

These four phases frame our experimental question: given the trajectory of the field, how close are current frontier models to making constrained decoding unnecessary?

---

## 3. Problem Formulation

### 3.1 Definitions

We define three layers at which LLM structured output can fail:

1. **Syntax validity**: Is the output parseable as valid JSON (passes `json.loads()`)?
2. **Semantic accuracy**: Does the output use the correct schema-defined field names and nesting structure (e.g., `loopback_ipv4_address` vs. `loopback_ipv4_pool` vs. `loopback_interfaces`)?
3. **Extraneous content**: Does the output contain text outside the JSON object (markdown fences, prose, explanations)?

Constrained decoding addresses layer 1 (and partially layer 3) through grammar masking. It does not address layer 2—a model can produce perfectly valid JSON that uses entirely wrong field names.

### 3.2 Enforcement Methods

We compare three enforcement regimes:

| Method | Mechanism | Layer 1 Guarantee | Layer 2 Guarantee |
|---|---|---|---|
| **Baseline** (free-form) | No enforcement; standard completion | None | None |
| **JSON mode** (API-enforced) | `response_format: {"type": "json_object"}` activates server-side constrained decoding | Yes (provider guarantee) | None |
| **Prompt-only** | Multi-layer prompt engineering instructing raw JSON output; validated via `json.loads()` | Empirical only | None |

### 3.3 Evaluation Domain

We use Arista AVD network configuration generation as the evaluation domain for this study. AVD was chosen because it provides a well-defined, publicly documented schema [7] with strict field-name and type requirements, making it straightforward to evaluate semantic accuracy against an unambiguous ground truth. The underlying question—whether constrained decoding improves structured output quality—is not specific to networking; applying the same methodology to other JSON generation tasks (e.g., API tool calling, database schemas, structured data extraction) is a natural extension of this work.

Four canonical configuration tasks are tested:

1. Configure BGP router-id using Loopback0 with IP 10.10.10.1/32 on a leaf switch
2. Configure VXLAN source interface to Loopback1
3. Configure VLAN 30 with VNI 1030 and override EVPN route-target to 65000:1030
4. Configure BGP peering with neighbor 10.0.0.2, remote-as 65100

Ground truth for each task is derived from the official AVD `eos_designs` schema (1,340 lines) [7], which is injected as context in all experiments.

---

## 4. Approach and Methodology

### 4.1 Infrastructure

All API calls are routed through OpenRouter (`openrouter.ai/api/v1/chat/completions`), providing a unified OpenAI-compatible endpoint across providers. The AVD `eos_designs.schema.yml` is loaded at startup and injected into every system prompt as a `<eos_designs_schema>` block, giving all models the same reference material.

### 4.2 Models

We tested four frontier models from three major providers:

| Model | Provider | Release Era |
|---|---|---|
| GPT-5.3 Chat | OpenAI | Early 2026 |
| GPT-5.4 | OpenAI | Early 2026 |
| Gemini 3.1 Pro Preview | Google | Early 2026 |
| Claude Sonnet 4.6 | Anthropic | Early 2026 |

Initial validation was performed using GPT-4o-mini (late 2024 era) to verify infrastructure correctness before running the full model suite.

### 4.3 Sampling Parameters

No explicit sampling parameters are set in any script. OpenRouter applies its global default of `temperature=1.0`. A notable cross-provider difference: Anthropic's temperature range caps at 1.0 (vs. 0–2 for OpenAI and Google), meaning Claude operates at maximum temperature relative to the others. We acknowledge this as a confound; future work will standardize temperature at 0.0 for deterministic evaluation.

### 4.4 Scripts

Three Python scripts implement the three enforcement methods:

- **`baseline.py`** → `output.txt`: Free-form YAML generation, no output format enforcement.
- **`json_enforced.py`** → `output_json_enforced.txt`: API-level JSON mode via `response_format: {"type": "json_object"}`. Prompts reworded to request JSON instead of YAML.
- **`prompt_enforced_only.py`** → `output_prompt_enforced.txt`: No API-level enforcement. JSON-only output enforced through three layers of prompt pressure: (1) role framing, (2) explicit `json.loads()` warning, (3) hard constraint with closing reminder. Each response is validated via `json.loads()` and flagged as valid/invalid.

### 4.5 Evaluation Metrics

Each model response is evaluated on three binary dimensions:

- **Syntax correctness**: Is the response valid JSON (or valid YAML for baseline)?
- **Accuracy**: Does the output use the correct AVD schema field names, types, and nesting as defined in `eos_designs.schema.yml`?
- **No extra text**: Is the response free of markdown fences, prose, or any content outside the structured object?

---

## 5. Preliminary Numerical Results

### 5.1 Manual Chat-Interface Experiments (Pre-Programmatic)

Before building the automated pipeline, we tested the same four tasks manually through the chat interfaces of ChatGPT, Gemini, and Claude—first without the schema, then with the schema provided as context.

**Table 1 — Manual tests, no schema context**

| Task | Gemini | ChatGPT | Claude |
|---|---|---|---|
| 1 (BGP router-id) | ✗ | ✗ | ✗ |
| 2 (VXLAN source) | ✗ | ✗ | ✓ |
| 3 (VLAN 30 / VNI) | ✗ | ✗ | ✓ |
| 4 (BGP peering) | ✗ | ✗ | ✗ |

**Table 2 — Manual tests, with schema context**

| Task | Gemini | ChatGPT | Claude |
|---|---|---|---|
| 1 (BGP router-id) | ✓ | ✗ | ✓ |
| 2 (VXLAN source) | ✗ | ✓ | ✗ |
| 3 (VLAN 30 / VNI) | ✓ | ✓ | ✓ |
| 4 (BGP peering) | ✓ | ✓ | ✓ |

**Key observation:** Providing the schema as context increased average accuracy from 2/12 (17%) to 8/12 (67%), confirming that schema context is a necessary but not sufficient condition for correct output.

### 5.2 Programmatic Baseline (Free-Form YAML)

**Table 3 — Baseline accuracy (schema injected, no format enforcement)**

| Task | GPT-5.3 | GPT-5.4 | Gemini 3.1 | Claude 4.6 |
|---|---|---|---|---|
| 1 (BGP router-id) | ✗ | ✗ | ✓ | ✗ |
| 2 (VXLAN source) | ✗ | ✓ | ✗ | ✗ |
| 3 (VLAN 30 / VNI) | ✓ | ✓ | ✓ | ✓ |
| 4 (BGP peering) | ✓ | ✓ | ✓ | ✗ |

Accuracy: 8/16 (50%). All models produced verbose responses with explanatory prose, tables, and multiple code blocks—useful for a human reader but unusable for machine consumption.

### 5.3 Prompt-Enforced JSON

**Table 4 — Prompt-enforced JSON (no API-level enforcement)**

| Task | GPT-5.3 | GPT-5.4 | Gemini 3.1 | Claude 4.6 | Metric |
|---|---|---|---|---|---|
| 1 | ✓ | ✓ | ✓ | ✓ | Syntax |
|   | ✓ | ✓ | ✓ | ✗ | Accuracy |
|   | ✓ | ✓ | ✓ | ✓ | No extra text |
| 2 | ✓ | ✓ | ✓ | ✓ | Syntax |
|   | ✗ | ✓ | ✗ | ✗ | Accuracy |
|   | ✓ | ✓ | ✓ | ✓ | No extra text |
| 3 | ✓ | ✓ | ✓ | ✓ | Syntax |
|   | ✓ | ✓ | ✓ | ✓ | Accuracy |
|   | ✓ | ✓ | ✓ | ✓ | No extra text |
| 4 | ✓ | ✓ | ✓ | ✓ | Syntax |
|   | ✓ | ✗ | ✓ | ✓ | Accuracy |
|   | ✓ | ✓ | ✓ | ✓ | No extra text |

- **Syntax validity:** 16/16 (100%). All four models produced parseable JSON on every prompt using prompt engineering alone.
- **Semantic accuracy:** 12/16 (75%).
- **No extra text:** 16/16 (100%).

### 5.4 API-Enforced JSON Mode

**Table 5 — API-enforced JSON mode (`response_format: json_object`)**

| Task | GPT-5.3 | GPT-5.4 | Gemini 3.1 | Claude 4.6 | Metric |
|---|---|---|---|---|---|
| 1 | ✓ | ✓ | ✓ | ✓ | Syntax |
|   | ✗ | ✓ | ✓ | ✗ | Accuracy |
|   | ✗ | ✓ | ✓ | ✗ | No extra text |
| 2 | ✓ | ✓ | ✓ | ✓ | Syntax |
|   | ✗ | ✓ | ✗ | ✗ | Accuracy |
|   | ✓ | ✓ | ✓ | ✓ | No extra text |
| 3 | ✓ | ✓ | ✓ | ✓ | Syntax |
|   | ✓ | ✓ | ✓ | ✓ | Accuracy |
|   | ✓ | ✓ | ✓ | ✓ | No extra text |
| 4 | ✓ | ✓ | ✓ | ✓ | Syntax |
|   | ✓ | ✗ | ✓ | ✓ | Accuracy |
|   | ✓ | ✓ | ✓ | ✓ | No extra text |

- **Syntax validity:** 16/16 (100%).
- **Semantic accuracy:** 11/16 (69%).
- **No extra text:** 14/16 (88%). Claude Sonnet 4.6 wrapped two responses in markdown ```` ```json ``` ```` fences despite being in JSON mode, causing the raw response to fail `json.loads()`.

### 5.5 GPT-4o-mini Validation (Older Model, Late 2024)

For reference, GPT-4o-mini was tested under all three regimes. It produced significantly worse results: hallucinated fields (`aaa_settings`, `uplink_interfaces`), bloated responses with dozens of irrelevant keys, and zero correct outputs across all four prompts on the baseline. This early experiment served as infrastructure validation and confirmed that older models exhibit markedly worse schema compliance.

### 5.6 Comparative Summary

| Metric | Baseline | Prompt-Enforced | JSON Mode |
|---|---|---|---|
| Syntax validity | N/A (YAML) | 100% (16/16) | 100% (16/16) |
| Semantic accuracy | 50% (8/16) | **75% (12/16)** | 69% (11/16) |
| No extra text | 0% (0/16) | **100% (16/16)** | 88% (14/16) |

The prompt-enforced method slightly outperformed API-level JSON mode on both semantic accuracy and extraneous content elimination. This is a preliminary finding on a small sample (n=16 per method) and requires validation at scale.

---

## 6. Discussion

### 6.1 Syntax is a Solved Problem at the Frontier

The most striking result is that **all four frontier models achieved 100% syntax validity under both enforcement methods**. Even under prompt-only enforcement—with no server-side grammar masking—every response was parseable by `json.loads()`. This suggests that for early-2026 frontier models, the token-level grammar enforcement that constrained decoding provides may be addressing a problem that the models have already solved through training. This aligns directly with [20], where models trained with JSON parser feedback as an RL reward signal achieved 98.7%+ native valid output rates without any inference-time constraints. It is reasonable to infer that the frontier models we tested have undergone similar RL-based training, internalizing JSON syntax rules into their weights.

### 6.2 Constrained Decoding Does Not Improve Semantic Accuracy

The more important finding is that API-level JSON mode did not improve—and may have slightly degraded—semantic accuracy compared to prompt-only enforcement (69% vs. 75%). This is consistent with the "reasoning tax" documented in [14] and [17]: constraining the model's token space pushes it down non-canonical tokenization paths, sacrificing semantic correctness for syntactic compliance. CRANE [17] proved mathematically that overly restrictive grammars reduce the model's effective complexity class, which offers a theoretical explanation for why constrained decoding could hurt content quality even while guaranteeing syntactic form.

However, our sample size is too small (n=16) to draw statistically significant conclusions. The difference could be within the noise margin of a single non-deterministic run at temperature 1.0.

### 6.3 Claude's JSON Mode Non-Compliance

An unexpected finding was that Claude Sonnet 4.6 wrapped two JSON-mode responses in markdown fences (`` ```json ... ``` ``), despite the API requesting `json_object` format. This occurred because OpenRouter passes `response_format` to Anthropic on a best-effort basis, and Anthropic's API does not natively support the `json_object` format parameter in the same way as OpenAI. This is an infrastructure artifact rather than a model capability limitation, but it illustrates the practical fragility of cross-provider constrained decoding.

### 6.4 Limitations

- **Sample size:** Four prompts across four models is insufficient for statistical significance. The results are directional only.
- **Single run:** Each prompt-model pair was evaluated once. At temperature 1.0, substantial run-to-run variance is expected.
- **Temperature confound:** Anthropic's temperature range (0–1) differs from OpenAI/Google (0–2), introducing an uncontrolled variable.
- **Single evaluation domain:** Our current experiments use only AVD schema compliance as the evaluation target. While we selected AVD because it provides an unambiguous ground truth for semantic accuracy, confirming that these findings generalize to other JSON generation domains (tool calling, data extraction, etc.) is a planned next step.

---

## 7. Conclusions and Next Steps

### 7.1 Conclusions So Far

1. **Frontier models (early 2026) can produce syntactically valid JSON at 100% rates using prompt engineering alone**, without constrained decoding. This challenges the conventional assumption that grammar-level enforcement is necessary.
2. **Semantic accuracy remains the bottleneck**, irrespective of enforcement method. Both constrained decoding and prompt engineering achieve approximately 70–75% accuracy on AVD schema compliance.
3. **Constrained decoding does not improve semantic accuracy** in our preliminary experiments. The mechanism solves a problem (syntax validity) that frontier models appear to have already internalized through training, while leaving the harder problem (semantic correctness) unresolved.

### 7.2 Planned Work (Remaining 6 Weeks)

The preliminary results are promising but not yet conclusive. The remaining project period will focus on:

1. **Systematic sample sizing.** We will define a statistically appropriate number of prompts per task, targeting sufficient power to detect meaningful accuracy differences between enforcement methods. We plan to expand from 4 tasks to at minimum 20–30 AVD configuration scenarios covering diverse schema regions.

2. **Multi-run evaluation.** Each prompt-model-method combination will be run multiple times (≥5) at temperature 0.0 for deterministic comparison, and at temperature 1.0 for variance characterization.

3. **Model timeline analysis.** We will test models spanning from GPT-4o-mini (late 2024) through the latest GPT-5.x releases to trace the historical evolution of native JSON compliance. Our GPT-4o-mini baseline already shows markedly worse performance, providing one data point; adding intermediate models (GPT-4o, GPT-4.5, GPT-5.0) will help identify the approximate generational boundary at which prompt-only enforcement became sufficient for syntax validity.

4. **Quantitative metrics.** Beyond binary correct/incorrect, we will implement field-level precision and recall against the AVD schema, measuring partial credit for responses that use mostly-correct structure with isolated field errors.

5. **Cross-domain validation.** To confirm that our findings are not artifacts of the AVD domain, we plan to replicate the core experiment using a second structured output task (e.g., OpenAPI tool-call schemas or database record generation).

6. **Trend characterization.** Using the model timeline data, we will characterize whether semantic accuracy is improving across model generations at a rate that suggests convergence toward full native compliance. We will report the observed trend without speculative extrapolation—our goal is to provide empirical evidence on the current trajectory, not to predict specific timelines for obsolescence of constrained decoding.

---

## References

[1] C. Wang et al., "NetConfEval: Can LLMs Facilitate Network Configuration?" *Proceedings of ACM CoNEXT*, 2024.

[2] Technical University of Munich, "NetLLMBench: Benchmarking Large Language Models for Network Configuration Tasks," 2024.

[3] S. Ugare et al., "Multi-IaC-Eval: Benchmarking Cloud Infrastructure as Code Across Multiple Formats," *arXiv:2509.05303*, 2025.

[4] P. Jana et al., "TerraFormer: Automated Infrastructure-as-Code with LLMs Fine-Tuned via Policy-Guided Verifier Feedback," *arXiv:2601.08734*, 2026.

[5] Ericsson Research, "Agentic AI: Pathway to Autonomous Networks Level 5," *Ericsson Technology Review*, 2025.

[6] Cisco Systems, "AI Assistant for Networking: Capabilities and Use Cases," *Cisco Meraki Documentation*, 2026.

[7] Arista Networks, "Arista Validated Designs (AVD) Documentation." Available: https://avd.arista.com.

[8] Red Hat Ansible Documentation, "Validating YAML and Ansible Variables Using Schema Definitions," 2024.

[9] "Pre3: Enabling Deterministic Pushdown Automata for Faster Structured LLM Generation," July 2025.

[10] "WGRAMMAR: Leverage Prior Knowledge to Accelerate Structured Decoding," July 2025.

[11] "Correctness-Guaranteed Code Generation via Constrained Decoding," August 2025.

[12] "XGrammar 2: Dynamic and Efficient Structured Generation Engine for Agentic LLMs," January 2026.

[13] "Beyond Prompts: Space–Time Decoupling Control-Plane Jailbreaks in LLM Structured Output," January/March 2026.

[14] "Navigating the Impact of Structured Output Format on Large Language Models through the Compass of Causal Inference," September 2025.

[15] "Improving Tool Calling Accuracy for Large Language Models," November 2025.

[16] "Combining Constrained and Unconstrained Decoding via Boosting: BoostCD," Late 2025.

[17] "CRANE: Reasoning with constrained LLM generation," Late 2025 / Early 2026.

[18] "Thinking Before Constraining: A Unified Decoding Framework for LLMs," January 2026.

[19] "Draft-Conditioned Constrained Decoding (DCCD) for Structured Generation," February 2026.

[20] "Training LLMs for Strict JSON Schema Adherence via Reinforcement Learning and Structured Reasoning," February 2025.

[21] "Reasoning through Exploration," August 2025.

[22] "PARL-MT: Learning to Call Functions in Multi-Turn Conversation with Progress Awareness," September 2025.

[23] "Close the Loop: Synthesizing Infinite Tool-Use Data via Multi-Agent Role-Playing," December 2025.

[24] "Exploring Weaknesses in Function Call Models via Reinforcement Learning: An Adversarial Data Augmentation Approach," January 2026.

[25] "SimpleTool: Parallel Decoding for Real-Time LLM Function Calling," February 2026.
