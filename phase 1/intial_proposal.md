Automated Network Deployment via Fine-Tuned Large Language Models for Arista AVD
Team Members:
1. Milo Mendoza – Andrew ID: alvarom
2. Ashwin Silla – Andrew ID: asilla



Automated Network Deployment via Fine-Tuned Large Language Models for Arista AVD

1. Area of Research: Motivation

Modern enterprise and data center networks increasingly rely on Infrastructure as Code (IaC) to ensure repeatable and scalable deployments. Frameworks such as Arista Validated Designs (AVD) enable structured network automation through standardized YAML-based repositories. However, creation of these repositories remains largely manual and requires deep familiarity with vendor-specific schemas. Recent research has demonstrated that general-purpose large language models are unable to reliably generate correct network configurations without domain adaptation [1]. This motivates the need for an AI-driven system capable of translating natural language requirements into syntactically correct and schema-compliant AVD configurations.

2. Problem Identification

2.1 State of the Art

Benchmarks such as NetConfEval have shown that existing large language models struggle to produce valid low-level network configurations and frequently hallucinate commands or parameters [1]. Similarly, NetLLMBench confirms that strict JSON and YAML schema requirements pose major challenges for LLM-based automation tasks [2]. Current industry implementations of generative AI in networking are focused primarily on troubleshooting, log analysis, and documentation retrieval rather than initial configuration generation [6].

2.2 Gaps

A major gap exists in Day-0 automation, particularly in the generation of multi-file configuration repositories that must adhere to vendor-specific schemas. Studies in the Infrastructure-as-Code domain demonstrate that LLMs have difficulty maintaining cross-file dependencies and strict variable correctness [3]. Recent work also shows that domain-specific fine-tuning is essential for generating deployable IaC artifacts [4]. At present, no publicly available model is trained specifically on Arista AVD variables and design structures, which are defined in official AVD documentation [7].

3. Hypothesis

We hypothesize that a language model fine-tuned on Arista AVD repositories will outperform general-purpose models in generating syntactically valid and schema-compliant YAML configurations. Furthermore, for large-scale networks, a multi-step agentic approach will be required to decompose the problem into manageable tasks [5]. An alternative hypothesis is that Retrieval-Augmented Generation using AVD documentation may provide comparable performance without fine-tuning.

4. Approach and Methods

Phase 1 will involve curating a dataset of validated Arista AVD configurations and fine-tuning a base LLM on this corpus. The correctness of generated YAML files will be validated against official AVD schema definitions and variable references [7]. Automated validation techniques similar to established YAML and Ansible schema checking methods will be employed to ensure key correctness [8].

Phase 2 will evaluate model outputs using metrics such as syntax validity, schema compliance, and repository structure correctness. Evaluation will be guided by methodologies introduced in NetConfEval and NetLLMBench [1][2].

Phase 3 will introduce an agentic framework for large-scale deployments. This framework will decompose massive network designs into smaller subtasks, following principles outlined in autonomous networking research [5].

5. Expected Results

The fine-tuned model is expected to generate small-scale AVD configurations with significantly higher correctness compared to baseline LLMs. For larger deployments, the agentic system is expected to enable reliable multi-file repository generation that would be infeasible using a single prompt.

6. Impact

This research could significantly reduce the manual effort required for network deployments and improve reliability of Day-0 automation. By demonstrating that LLMs can generate vendor-compliant IaC artifacts, the project lays groundwork for autonomous network orchestration systems capable of adapting to different vendor platforms.

References

[1] C. Wang et al., “NetConfEval: Can LLMs Facilitate Network Configuration?” Proceedings of ACM CoNEXT, 2024.

[2] Technical University of Munich, “NetLLMBench: Benchmarking Large Language Models for Network Configuration Tasks,” 2024.

[3] S. Ugare et al., “Multi-IaC-Eval: Benchmarking Cloud Infrastructure as Code Across Multiple Formats,” arXiv preprint arXiv:2509.05303, 2025.

[4] P. Jana et al., “TerraFormer: Automated Infrastructure-as-Code with LLMs Fine-Tuned via Policy-Guided Verifier Feedback,” arXiv preprint arXiv:2601.08734, 2026.

[5] Ericsson Research, “Agentic AI: Pathway to Autonomous Networks Level 5,” Ericsson Technology Review, 2025.

[6] Cisco Systems, “AI Assistant for Networking: Capabilities and Use Cases,” Cisco Meraki Documentation, 2026.

[7] Arista Networks, “Arista Validated Designs (AVD) Documentation,” Available: https://avd.arista.com.

[8] Red Hat Ansible Documentation, “Validating YAML and Ansible Variables Using Schema Definitions,” 2024.

