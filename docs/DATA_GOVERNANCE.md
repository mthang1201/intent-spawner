# Data Governance

## Scope

This artifact uses synthetic benchmark scenarios to evaluate an intent- and
context-aware profile recommendation prototype. It is not a real-user study and
does not collect data from deployed users.

## Data Collected

- Benchmark workload manifest fields: workload IDs, categories, synthetic
  intent text, dataset-size hints, code-context hints, deterministic seeds,
  expected acceptable profiles, policy constraints, and synthetic-data license
  statements.
- Raw experiment records: method, workload ID, repeat index, seed,
  environment ID, recommendation result, applied profile, policy warnings,
  resource requests/limits, local runtime, local peak RSS in bytes, exit status,
  timeout status, cleanup status, and relative supporting log paths.
- Experiment planning metadata: matrix JSONL, environment JSON, Git commit,
  Python version, platform label, Helm version, Kubernetes context label, and
  whether the working tree was dirty at run time.
- Supporting local workload logs: stdout JSONL emitted by deterministic
  synthetic workloads and stderr text files.
- Sanitized Kubernetes-backed records: hashed pod names, generic experiment
  and namespace labels, allowlisted annotations/environment variables, pod
  phase and termination status, scheduling events, applied requests/limits,
  cgroup-v2 measurements, Metrics Server snapshots when present, and cleanup
  status.
- Scoped cluster environment metadata: generic disposable context label, node
  capacity/allocatable quantities, architecture, Kubernetes/runtime versions,
  kernel/OS version, evaluated commit, image ID, and Metrics Server image.
- Capacity records: generic run IDs, profile resources, phase/outcome,
  FailedScheduling reasons, Pending duration, cgroup peaks, and aggregate
  concurrency samples. The historical capacity generator source is missing;
  this is a provenance limitation, not a privacy exception.
- Sanitized Kubernetes fixture data in tests. The fixture schema keeps selected
  pod status, event, and metric fields needed for parser tests.

## Data Explicitly Not Collected

- Raw notebook contents from users.
- Real datasets or user-uploaded files.
- Secrets, tokens, passwords, API keys, cookies, or credentials.
- Real user names, email addresses, account IDs, home-directory paths, or
  free-form user identifiers.
- Full Kubernetes pod specs, full annotations, full labels, broad environment
  variables, or cluster-wide metadata dumps.
- Browser telemetry, keystroke logs, or interaction transcripts.

## Raw Notebook Contents

Raw notebook contents are not stored. The proposed recommender may receive
operator-provided code-context text in the demo UI, but the experiment harness
stores only a derived `context_signal_summary` such as detected term classes,
hint counts, and whether a dataset-size signal was used.

The JupyterHub demo evaluates raw intent and code context in memory. It copies
only the selected profile, a matched-signal explanation, and normalized dataset
size into the spawned pod. It does not copy raw intent or raw code context into
pod environment variables, annotations, or recommender log messages.

## Datasets

Datasets are not stored. Benchmark workloads generate synthetic data from
deterministic seeds in memory or temporary files. Temporary CSV files are
deleted before the workload exits.

## Usernames And Identifiers

The artifact uses workload IDs, run IDs, method labels, repeat indices, and
operator-supplied environment IDs. Environment IDs must not contain personal
names, emails, device names, or account identifiers. Raw records should use
relative paths only. The preserved raw snapshots were sanitized to remove local
home-directory paths from environment metadata.

The Kubernetes evaluation used the deliberately generic disposable identifiers
`intent-spawner-eval`, `minikube-intent-spawner-eval`, and
`z2jh-context-demo`. They are experiment pseudonyms, not names copied from a
shared cluster or person. Pod names are deterministic hashes of synthetic run
IDs. The excluded local pilot retains machine identifiers and must not be
published or used in derived analysis.

## Retention Expectations

The committed preserved raw snapshots are retained with the thesis artifact so
the derived tables and figures can be reproduced. New local runs under
`experiments/raw/` are ignored by default and should be retained only as long as
needed for validation, debugging, or a documented experiment rerun.

## Access Assumptions

This repository is intended for thesis supervisors, reviewers, and maintainers
who need to inspect reproducibility. It is not designed as a repository for
sensitive operational telemetry. If a fork contains real cluster evidence, the
fork owner is responsible for access control, review, and redaction before
sharing.

The demo uses JupyterHub's `DummyAuthenticator`, which accepts any non-empty
password. It is intentionally insecure and must remain on an isolated local
cluster behind local port forwarding. Operational usernames and generated pod
names processed by JupyterHub/Kubernetes are not research evidence and must be
removed or pseudonymized before any logs or objects are shared.

## Sanitization Rules

- Store raw result paths relative to the repository.
- Store derived context features, not raw notebook code.
- Allowlist Kubernetes annotations and environment variables before storage.
- Remove usernames, home paths, emails, hostnames that identify a person, and
  secrets from environment reports.
- Keep only the minimum pod/event/metric fields needed for the stated analysis.
- Preserve metric absence explicitly instead of inventing values.
- Do not edit raw records after publication except for documented
  privacy-preserving sanitization.

## Benchmark Data Licenses

All benchmark data is synthetic and generated by repository code. No external
dataset license applies to the generated data. Third-party software remains
governed by its own package and tool licenses. This is a provenance statement,
not a project license grant. The repository has no project software license
file, so redistribution rights remain unresolved until the author selects one.

## Synthetic-Data Declaration

The benchmark workloads do not download, read, or transform external datasets.
Each workload uses deterministic seeds declared in `benchmarks/workloads.yaml`.
Synthetic intent and code-context hints are authored examples, not extracted
from real notebooks or users.

## Privacy Risks

The main privacy risks are accidental collection of real notebook code,
accidental inclusion of usernames or local paths in environment metadata, and
overbroad Kubernetes evidence collection. The current artifact mitigates those
risks by storing derived context summaries, relative paths, sanitized
environment metadata, and narrow Kubernetes fixtures.

## Future Real-User Study Requirements

A real-user study would require ethics/IRB or institutional review where
applicable, informed consent, a data minimization plan, a retention and deletion
schedule, access controls, participant pseudonymization, secret scanning,
reviewable redaction tooling, opt-out/withdrawal handling, dataset license
review, and a clear separation between operational logs and research records.
