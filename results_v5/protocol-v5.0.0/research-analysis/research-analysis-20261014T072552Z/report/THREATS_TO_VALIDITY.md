# Protocol-v5 Threats to Validity

Generated only from recorded registry or experiment metadata.

## TV-001 — REQUIRED_EVIDENCE_UNAVAILABLE

- Category: `statistical_conclusion`
- Severity: `blocking`
- Affected claims: `H3, H4`
- Source: `derived/evidence-selection.json/requirements/2`
- Observed metadata: `{"candidate_count":0,"eligible_candidate_count":0,"reason_codes":["NO_ELIGIBLE_CONFIRMATORY_EVIDENCE"]}`

The required confirmatory evidence was unavailable or unselected, so the linked claim cannot be decided.

## TV-002 — REQUIRED_EVIDENCE_UNAVAILABLE

- Category: `statistical_conclusion`
- Severity: `blocking`
- Affected claims: `H8`
- Source: `derived/evidence-selection.json/requirements/6`
- Observed metadata: `{"candidate_count":0,"eligible_candidate_count":0,"reason_codes":["NO_ELIGIBLE_CONFIRMATORY_EVIDENCE","P3_NOT_RETAINED_OR_NOT_PRESENT"]}`

The required confirmatory evidence was unavailable or unselected, so the linked claim cannot be decided.

## TV-003 — NON_CONFIRMATORY_EVIDENCE_PRESENT

- Category: `benchmark_contamination`
- Severity: `boundary`
- Affected claims: `H7F`
- Source: `derived/evidence-inventory.json/candidates`
- Observed metadata: `{"count":1,"manifests":["/Users/mthang1201/Documents/datn/intent-spawner/results_v5/protocol-v5.0.0/E5/run-e5-functional-orbstack/manifest.json"],"stages":["development"]}`

Development, historical, or unknown-stage packages are inventoried but excluded from confirmatory claim decisions.

## TV-004 — NON_CONFIRMATORY_EVIDENCE_PRESENT

- Category: `benchmark_contamination`
- Severity: `boundary`
- Affected claims: `H7`
- Source: `derived/evidence-inventory.json/candidates`
- Observed metadata: `{"count":1,"manifests":["/Users/mthang1201/Documents/datn/intent-spawner/results_v5/protocol-v5.0.0/E5/run-e5-storage-orbstack/derived/storage_metrics.json"],"stages":["development"]}`

Development, historical, or unknown-stage packages are inventoried but excluded from confirmatory claim decisions.

## TV-005 — NON_CONFIRMATORY_EVIDENCE_PRESENT

- Category: `benchmark_contamination`
- Severity: `boundary`
- Affected claims: `H2`
- Source: `derived/evidence-inventory.json/candidates`
- Observed metadata: `{"count":2,"manifests":["/Users/mthang1201/Documents/datn/intent-spawner/results_v5/protocol-v5.0.0/E1/run-e1-llm-gemini-3.5-flash-lite/derived/statistical_analysis/analysis-manifest.json","/Users/mthang1201/Documents/datn/intent-spawner/results_v5/protocol-v5.0.0/E1/run-e1-local/derived/statistical_analysis/analysis-manifest.json"],"stages":["development"]}`

Development, historical, or unknown-stage packages are inventoried but excluded from confirmatory claim decisions.

## TV-006 — NON_CONFIRMATORY_EVIDENCE_PRESENT

- Category: `benchmark_contamination`
- Severity: `boundary`
- Affected claims: `H1`
- Source: `derived/evidence-inventory.json/candidates`
- Observed metadata: `{"count":2,"manifests":["/Users/mthang1201/Documents/datn/intent-spawner/results_v5/protocol-v5.0.0/E1/run-e1-llm-gemini-3.5-flash-lite/derived/statistical_analysis/analysis-manifest.json","/Users/mthang1201/Documents/datn/intent-spawner/results_v5/protocol-v5.0.0/E1/run-e1-local/derived/statistical_analysis/analysis-manifest.json"],"stages":["development"]}`

Development, historical, or unknown-stage packages are inventoried but excluded from confirmatory claim decisions.

## TV-007 — NON_CONFIRMATORY_EVIDENCE_PRESENT

- Category: `benchmark_contamination`
- Severity: `boundary`
- Affected claims: `H5, H6`
- Source: `derived/evidence-inventory.json/candidates`
- Observed metadata: `{"count":2,"manifests":["/Users/mthang1201/Documents/datn/intent-spawner/results_v5/protocol-v5.0.0/E4/run-e4-observed-llm-gemini-3.5-flash-lite-analysis/manifest.json","/Users/mthang1201/Documents/datn/intent-spawner/results_v5/protocol-v5.0.0/E4/run-e4-observed-local-analysis/manifest.json"],"stages":["development"]}`

Development, historical, or unknown-stage packages are inventoried but excluded from confirmatory claim decisions.

## TV-008 — JOINT_ACCEPT_COMPOSITE_CONSTRUCT

- Category: `construct`
- Severity: `boundary`
- Affected claims: `H1`
- Source: `/Users/mthang1201/Documents/datn/intent-spawner/benchmarks_v5/protocol-v5-claim-registry-v1.2.yaml/claims/0/metrics`
- Observed metadata: `joint_accept_at_1`

JointAccept@1 operationalizes acceptability jointly across the frozen profile, image, and constraint criteria rather than every possible deployment objective.

## TV-009 — REVIEWED_EQUIVALENCE_CONSTRUCT

- Category: `construct`
- Severity: `boundary`
- Affected claims: `H2`
- Source: `derived/evidence-inventory.json/candidates/2/metadata/variant_n`
- Observed metadata: `83`

Surface-form robustness generalizes only to variants recorded as reviewed-equivalent in the frozen benchmark.

## TV-010 — NO_SUCCESS_NONINFERIORITY_MARGIN

- Category: `construct`
- Severity: `boundary`
- Affected claims: `H5, H6`
- Source: `derived/evidence-inventory.json/candidates/6/metadata/success_noninferiority_margin`
- Observed metadata: `N/A`

The frozen E4 contract explicitly records no formal success noninferiority margin; preservation is limited to the declared Pareto rule.

## TV-011 — DIRTY_GIT_EVIDENCE_ENVIRONMENT

- Category: `internal`
- Severity: `disclosure`
- Affected claims: `H7F`
- Source: `derived/evidence-inventory.json/candidates/0/provenance/git_dirty`
- Observed metadata: `True`

The observed package records a dirty working tree; semantic identities passed separately, but unrecorded local changes remain a limitation.

## TV-012 — IMAGE_PLATFORM_DEPENDENCE

- Category: `image_platform_dependence`
- Severity: `high`
- Affected claims: `H7F`
- Source: `derived/evidence-inventory.json/candidates/0/metadata/environment`
- Observed metadata: `{"environment_id":"e5-docker-darwin","execution_mode":"docker","git_info":{"git_dirty":true,"git_revision":"f7a4426472043d4652bede07556656800605ad95"},"platform":"macOS-15.3.2-arm64-arm-64bit-Mach-O","python_version":"3.14.6 (main, Jun 10 2026, 10:03:53) [Clang 17.0.0 (clang-1700.6.4.2)]","runtime_detected":"docker","source_recommendation_run_id":"run-e1-offline","source_recommendation_run_sha256":"832ab0e3500e9e368b7d3336f89a520a7165ab121aa0f79e6f0709412cd9a153"}`

Image correctness or storage results apply to the recorded runtime, operating system, architecture, and immutable image digests.

## TV-013 — IMAGE_PLATFORM_DEPENDENCE

- Category: `image_platform_dependence`
- Severity: `high`
- Affected claims: `H7`
- Source: `derived/evidence-inventory.json/candidates/1/metadata/environment`
- Observed metadata: `{"architecture":"arm64","environment_id":"e5-storage-darwin-arm64","operating_system":"linux","runtime":"docker"}`

Image correctness or storage results apply to the recorded runtime, operating system, architecture, and immutable image digests.

## TV-014 — GIT_REVISION_DIFFERENCE

- Category: `internal`
- Severity: `disclosure`
- Affected claims: `H1, H2, H5, H6, H7, H7F`
- Source: `derived/provenance-consistency.json/disclosures/0`
- Observed metadata: `{"image_functional":"f7a4426472043d4652bede07556656800605ad95","image_storage":"f7a4426472043d4652bede07556656800605ad95","natural_language_robustness":"9f513666ea51d3bd9113be33d9d7707dd5fe6636","offline_recommendation":"9f513666ea51d3bd9113be33d9d7707dd5fe6636","resource_efficiency":"9f513666ea51d3bd9113be33d9d7707dd5fe6636"}`

The selected experiments record different non-semantic execution identities; the difference is disclosed and does not override semantic freeze checks.

## TV-015 — ENVIRONMENT_IDENTITY_DIFFERENCE

- Category: `internal`
- Severity: `disclosure`
- Affected claims: `H1, H2, H5, H6, H7, H7F`
- Source: `derived/provenance-consistency.json/disclosures/1`
- Observed metadata: `{"image_functional":{"environment_id":"e5-docker-darwin","execution_mode":"docker","git_info":{"git_dirty":true,"git_revision":"f7a4426472043d4652bede07556656800605ad95"},"platform":"macOS-15.3.2-arm64-arm-64bit-Mach-O","python_version":"3.14.6 (main, Jun 10 2026, 10:03:53) [Clang 17.0.0 (clang-1700.6.4.2)]","runtime_detected":"docker","source_recommendation_run_id":"run-e1-offline","source_recommendation_run_sha256":"832ab0e3500e9e368b7d3336f89a520a7165ab121aa0f79e6f0709412cd9a153"},"natural_language_robustness":{"platform":"darwin","platform_release":"24.3.0","python_version":"3.14.6"},"offline_recommendation":{"platform":"darwin","platform_release":"24.3.0","python_version":"3.14.6"},"resource_efficiency":{"adapter_monitor_grace_seconds":5,"architecture":"arm64","captured_at_utc":"2026-10-13T09:21:05.381316Z","cgroup_eligibility_probe":{"available_files":["cgroup.controllers","cpu.max","cpu.stat","memory.current","memory.events","memory.max","memory.peak"],"cgroup_version":"v2","cleanup_status":"succeeded","controllers":["cpuset","cpu","io","memory","pids"],"memory_event_keys":["low","high","max","oom","oom_kill","oom_group_kill","sock_throttled"],"probe_status":"succeeded"},"cgroup_requirement":"v2","cluster_measurement_status":"OBSERVED","collector_implementation":"cluster_evaluation.resource_efficiency_adapter_v5.KubernetesResourceEfficiencyAdapter","collector_origin":"REAL_KUBERNETES_COLLECTOR","collector_version":"protocol-v5-resource-efficiency-kubernetes-adapter-v1.0.0","container_image":"intent-spawner-resource-v5:latest","container_runtime":"docker://29.4.0","eligibility_policy_version":"protocol-v5-resource-cluster-eligibility-v1.1.0","eligibility_status":"ELIGIBLE","environment_id":"orbstack:z2jh-context-demo","kernel_version":"7.0.5-orbstack-00330-ge3df4e19b0a0-dirty","kubelet_version":"v1.33.9+orb1","kubernetes_version":{"clientVersion":{"buildDate":"2025-07-15T17:59:41Z","compiler":"gc","gitCommit":"80779bd6ff08b451e1c165a338a7b69351e9b0b8","gitTreeState":"clean","gitVersion":"v1.33.3","goVersion":"go1.24.5","major":"1","minor":"33","platform":"darwin/arm64"},"kustomizeVersion":"v5.6.0","serverVersion":{"buildDate":"2026-04-18T09:19:01Z","compiler":"gc","emulationMajor":"1","emulationMinor":"33","gitCommit":"9093adae538d4d93bfa398549c569fe860ffe049","gitTreeState":"clean","gitVersion":"v1.33.9+orb1","goVersion":"go1.24.13","major":"1","minCompatibilityMajor":"1","minCompatibilityMinor":"32","minor":"33","platform":"linux/arm64"}},"namespace":"z2jh-context-demo","namespace_safety_label":"z2jh-context-demo.local/disposable-experiment-v5=true","node_allocatable":{"cpu":"8","ephemeral-storage":"49024567258","memory":"8185712Ki","pods":"110"},"node_capacity":{"cpu":"8","ephemeral-storage":"50395320Ki","memory":"8185712Ki","pods":"110"},"node_name":"orbstack","node_uid":"574e43f1-b4af-45a4-94ae-385414e52560","operating_system":"linux","pod_lifecycle_grace_seconds":30,"read_only_preflight":{"eligibility_status":"ELIGIBLE","facts":{"api_access":{"create:pods":true,"delete:pods":true,"get:pods":true,"get:pods/log":true,"list:pods":true},"cgroup_probe":{},"current_context":"orbstack","expected_context":"orbstack","image_declared_state":{"build_context_dockerignore_path":"cluster_evaluation/Dockerfile.resource-v5.dockerignore","build_context_dockerignore_sha256":"1cedf3c42d1173e18b735eb92bebd3fbc80872ce6c8e6e31111a73ecdcc315ad","built":true,"digest_syntactically_pinned":true,"digest_verified":true,"dockerfile_path":"cluster_evaluation/Dockerfile.resource-v5","dockerfile_sha256":"a9ce83468dbdf19569a703cb9277a39f5d26308a1230e2b00ed88d614bc10ed4","image_reference":"intent-spawner-resource-v5@sha256:a47e5339acca724b7f8b60510f1117d4a251c19efa5478cdbcfe4d77b8b3bbc6","note":"Verified execution image for OrbStack Kubernetes cluster.","operationally_verified":true,"pre_pulled_on_eligible_node":true,"protocol_version":"5.0.0","reference_configured":true,"resolved_digest":"sha256:a47e5339acca724b7f8b60510f1117d4a251c19efa5478cdbcfe4d77b8b3bbc6","schema_version":"protocol-v5-resource-image-state-v1.0.0","status":"BUILT_AND_VERIFIED"},"image_reference":"intent-spawner-resource-v5:latest","image_reference_pinned":true,"kubernetes_version":{"clientVersion":{"buildDate":"2025-07-15T17:59:41Z","compiler":"gc","gitCommit":"80779bd6ff08b451e1c165a338a7b69351e9b0b8","gitTreeState":"clean","gitVersion":"v1.33.3","goVersion":"go1.24.5","major":"1","minor":"33","platform":"darwin/arm64"},"kustomizeVersion":"v5.6.0","serverVersion":{"buildDate":"2026-04-18T09:19:01Z","compiler":"gc","emulationMajor":"1","emulationMinor":"33","gitCommit":"9093adae538d4d93bfa398549c569fe860ffe049","gitTreeState":"clean","gitVersion":"v1.33.9+orb1","goVersion":"go1.24.13","major":"1","minCompatibilityMajor":"1","minCompatibilityMinor":"32","minor":"33","platform":"linux/arm64"}},"namespace_labels":{"kubernetes.io/metadata.name":"z2jh-context-demo","z2jh-context-demo.local/e4-evaluation-backend":"orbstack-kubernetes"},"namespace_name":"z2jh-context-demo","node_allocatable":{"cpu":"8","ephemeral-storage":"49024567258","memory":"8185712Ki","pods":"110"},"node_capacity":{"cpu":"8","ephemeral-storage":"50395320Ki","memory":"8185712Ki","pods":"110"},"node_info":{"architecture":"arm64","bootID":"1afd8f20-2019-4031-b366-7d9241539d9e","containerRuntimeVersion":"docker://29.4.0","kernelVersion":"7.0.5-orbstack-00330-ge3df4e19b0a0-dirty","kubeProxyVersion":"","kubeletVersion":"v1.33.9+orb1","machineID":"82ddc538b9e95ae7c1d309201ccb5f9d","operatingSystem":"linux","osImage":"OrbStack","swap":{"capacity":9455902720},"systemUUID":"82ddc538b9e95ae7c1d309201ccb5f9d"},"node_labels":{"beta.kubernetes.io/arch":"arm64","beta.kubernetes.io/instance-type":"k3s","beta.kubernetes.io/os":"linux","kubernetes.io/arch":"arm64","kubernetes.io/hostname":"orbstack","kubernetes.io/os":"linux","node-role.kubernetes.io/control-plane":"true","node-role.kubernetes.io/master":"true","node.kubernetes.io/instance-type":"k3s","z2jh-context-demo.local/dedicated-e4":"true","z2jh-context-demo.local/node-identity":"e4-node-v1"},"node_name":"orbstack","node_uid":"574e43f1-b4af-45a4-94ae-385414e52560"},"failure_codes":[],"schema_version":"protocol-v5-resource-cluster-preflight-v1.1.0"},"required_context":"orbstack","required_memory_event_keys":["oom","oom_kill"],"schema_version":"protocol-v5-resource-environment-v1.1.0","single_active_workload":true,"workload_timeout_basis":"measured_workload_runtime_seconds"}}`

The selected experiments record different non-semantic execution identities; the difference is disclosed and does not override semantic freeze checks.

## TV-016 — SEMANTIC_PROVENANCE_MISMATCH

- Category: `internal`
- Severity: `blocking`
- Affected claims: `H7, H7F`
- Source: `derived/provenance-consistency.json/semantic_comparisons/4`
- Observed metadata: `{"digest_namespace":"catalog_file_bytes","expected":"bd2211d5fd769b5addec3c41e8cbd48bb0d9cd1c958aa554ada9bf88c29509f3","freeze_digest_namespace":null,"observed":{"image_functional":"bd2211d5fd769b5addec3c41e8cbd48bb0d9cd1c958aa554ada9bf88c29509f3","image_storage":"f45b04efc2ea6f271d49c6806b58bfc0f30503cb68944930609f6e0f71882a71"},"semantic_key":"catalog.file_sha256"}`

Recorded semantic provenance is missing, contradictory, or uses an incompatible digest namespace; affected claims are blocked.
