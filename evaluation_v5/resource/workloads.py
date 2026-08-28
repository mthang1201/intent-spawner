"""Bounded deterministic synthetic workload families for E4.

These operations model distinct data flows.  They do not allocate padding to a
requested cgroup target, download input, persist generated data, or consult a
recommendation backend.
"""

from __future__ import annotations

from array import array
from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import json
import math
import queue
import threading
import zlib
from typing import Any, Callable, Mapping


MASK64 = (1 << 64) - 1


def _next(value: int) -> int:
    value ^= (value << 13) & MASK64
    value ^= value >> 7
    value ^= (value << 17) & MASK64
    return value & MASK64


def _canonical_marker(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _streaming_statistics(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    state, total, squares = seed, 0, 0
    count = p["count"]
    for _ in range(count):
        state = _next(state)
        value = state & 0xFFFF
        total = (total + value) & MASK64
        squares = (squares + value * value) & MASK64
    return {"count": count, "sum": total, "sum_squares": squares, "tail": state}


def _relational_transform(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    rows: list[tuple[int, int, int]] = []
    state = seed
    for index in range(p["rows"]):
        state = _next(state)
        if state % p["filter_mod"]:
            rows.append((index, state & 0xFFFF, (state >> 16) & 0xFFFF))
    checksum = sum((a * 3 + b * 5 + c * 7) for a, b, c in rows) & MASK64
    return {"input_rows": p["rows"], "retained_rows": len(rows), "checksum": checksum}


def _hash_join(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    state = seed
    right: dict[int, int] = {}
    for key in range(p["right_rows"]):
        state = _next(state)
        right[key] = state & 0xFFFF
    matched = 0
    checksum = 0
    for index in range(p["left_rows"]):
        state = _next(state)
        key = state % (p["right_rows"] * 2)
        if key in right:
            matched += 1
            checksum = (checksum + right[key] * (index + 1)) & MASK64
    return {"left_rows": p["left_rows"], "right_rows": len(right), "matched": matched, "checksum": checksum}


def _sort_group(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    state = seed
    rows: list[tuple[int, int]] = []
    for _ in range(p["rows"]):
        state = _next(state)
        rows.append((state % p["groups"], (state >> 16) & 0xFFFF))
    rows.sort()
    totals = [0] * p["groups"]
    for group, value in rows:
        totals[group] = (totals[group] + value) & MASK64
    return {"rows": len(rows), "groups": len(totals), "leader": max(range(len(totals)), key=totals.__getitem__), "checksum": sum(totals) & MASK64}


def _categorical_encoding(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    state = seed
    encoded = array("I")
    totals = [0] * p["categories"]
    for row in range(p["rows"]):
        for feature in range(p["features"]):
            state = _next(state)
            category = state % p["categories"]
            encoded.append(category)
            totals[category] += (row + feature) & 0xFF
    return {"cells": len(encoded), "categories": p["categories"], "checksum": sum((i + 1) * value for i, value in enumerate(totals)) & MASK64}


def _linear_model_fit(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    weights = [0] * p["features"]
    checksum = seed
    for epoch in range(p["epochs"]):
        for row in range(p["rows"]):
            label = ((row * 17 + seed) % 11) < 5
            score = sum(weights[f] * (((row + 1) * (f + 3) + seed) % 101) for f in range(p["features"]))
            error = int(label) - int(score >= 0)
            for feature in range(p["features"]):
                weights[feature] += error * (((row + 1) * (feature + 3) + epoch) % 17 - 8)
            checksum = (checksum * 33 + score + error) & MASK64
    return {"rows": p["rows"], "features": p["features"], "epochs": p["epochs"], "weight_sum": sum(weights), "checksum": checksum}


def _dense_matrix(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    n = p["dimension"]
    left = array("I", (((r * 31 + c * 17 + seed) % 257) for r in range(n) for c in range(n)))
    right = array("I", (((r * 13 + c * 29 + seed) % 263) for r in range(n) for c in range(n)))
    checksum = 0
    for row in range(n):
        for col in range(n):
            cell = 0
            for inner in range(n):
                cell += left[row * n + inner] * right[inner * n + col]
            checksum = (checksum + cell * (row + col + 1)) & MASK64
    return {"dimension": n, "checksum": checksum}


def _sparse_graph(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    graph: list[list[int]] = [[] for _ in range(p["nodes"])]
    state = seed
    for node in range(p["nodes"]):
        for _ in range(p["edges_per_node"]):
            state = _next(state)
            graph[node].append(state % p["nodes"])
    frontier = deque([seed % p["nodes"]])
    seen = {frontier[0]}
    checksum = 0
    while frontier and len(seen) < p["visit_limit"]:
        node = frontier.popleft()
        checksum = (checksum * 131 + node) & MASK64
        for child in graph[node]:
            if child not in seen:
                seen.add(child)
                frontier.append(child)
    return {"nodes": p["nodes"], "visited": len(seen), "checksum": checksum}


def _text_tfidf(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    document_frequency = [0] * p["vocabulary"]
    total_terms = 0
    checksum = 0
    state = seed
    for document in range(p["documents"]):
        terms: set[int] = set()
        for _ in range(p["terms_per_document"]):
            state = _next(state)
            terms.add(state % p["vocabulary"])
            total_terms += 1
        for term in terms:
            document_frequency[term] += 1
            checksum = (checksum + (document + 1) * (term + 1)) & MASK64
    return {"documents": p["documents"], "total_terms": total_terms, "nonzero_terms": sum(value > 0 for value in document_frequency), "checksum": checksum}


def _json_normalization(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    documents = [
        json.dumps({"id": i, "group": (i + seed) % 97, "values": [i % 11, (i * 3) % 17]}, separators=(",", ":"))
        for i in range(p["documents"])
    ]
    normalized = [json.loads(item) for item in documents]
    checksum = sum(item["id"] * 7 + item["group"] * 11 + sum(item["values"]) for item in normalized) & MASK64
    return {"documents": len(normalized), "checksum": checksum}


def _image_convolution(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    width, height = p["width"], p["height"]
    pixels = bytearray(((index * 17 + seed) & 0xFF) for index in range(width * height))
    checksum = 0
    for row in range(1, height - 1):
        offset = row * width
        for col in range(1, width - 1):
            index = offset + col
            value = pixels[index - width] + pixels[index - 1] + pixels[index] + pixels[index + 1] + pixels[index + width]
            checksum = (checksum + value * (index + 1)) & MASK64
    return {"width": width, "height": height, "checksum": checksum}


def _compression_roundtrip(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    block = bytes(((index * 31 + seed) & 0xFF) for index in range(p["block_bytes"]))
    compressed = zlib.compress(block, level=p["level"])
    restored = zlib.decompress(compressed)
    return {"input_bytes": len(block), "compressed_bytes": len(compressed), "roundtrip_sha256": hashlib.sha256(restored).hexdigest()}


def _rolling_timeseries(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    values = array("I", (((index * 37 + seed) % 100_003) for index in range(p["points"])))
    window = p["window"]
    rolling = sum(values[:window])
    checksum = rolling
    for index in range(window, len(values)):
        rolling += values[index] - values[index - window]
        checksum = (checksum * 33 + rolling) & MASK64
    return {"points": len(values), "window": window, "checksum": checksum}


def _monte_carlo(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    state, inside = seed, 0
    for _ in range(p["samples"]):
        state = _next(state)
        x = state & 0xFFFF
        state = _next(state)
        y = state & 0xFFFF
        inside += x * x + y * y <= 0xFFFF * 0xFFFF
    return {"samples": p["samples"], "inside": inside, "tail": state}


def _content_dedup(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    hashes: set[bytes] = set()
    checksum = 0
    for index in range(p["items"]):
        logical = (index * 17 + seed) % p["unique_space"]
        digest = hashlib.blake2b(str(logical).encode("ascii"), digest_size=16).digest()
        hashes.add(digest)
        checksum = (checksum + digest[0] * (index + 1)) & MASK64
    return {"items": p["items"], "unique": len(hashes), "checksum": checksum}


def _producer_consumer(seed: int, p: Mapping[str, int]) -> dict[str, Any]:
    channel: queue.Queue[int | None] = queue.Queue(maxsize=p["queue_size"])
    output: list[int] = []

    def consumer() -> None:
        checksum = 0
        count = 0
        while True:
            item = channel.get()
            if item is None:
                break
            checksum = (checksum * 131 + item) & MASK64
            count += 1
        output.extend((count, checksum))

    thread = threading.Thread(target=consumer)
    thread.start()
    state = seed
    for _ in range(p["items"]):
        state = _next(state)
        channel.put(state & 0xFFFFFFFF)
    channel.put(None)
    thread.join()
    return {"items": output[0], "checksum": output[1], "tail": state}


OPERATIONS: dict[str, Callable[[int, Mapping[str, int]], dict[str, Any]]] = {
    "streaming_statistics": _streaming_statistics,
    "relational_transform": _relational_transform,
    "hash_join": _hash_join,
    "sort_group_reduction": _sort_group,
    "categorical_encoding": _categorical_encoding,
    "iterative_linear_fit": _linear_model_fit,
    "dense_matrix_computation": _dense_matrix,
    "sparse_graph_traversal": _sparse_graph,
    "text_tfidf": _text_tfidf,
    "json_normalization": _json_normalization,
    "image_convolution": _image_convolution,
    "compression_roundtrip": _compression_roundtrip,
    "rolling_timeseries": _rolling_timeseries,
    "monte_carlo": _monte_carlo,
    "content_deduplication": _content_dedup,
    "bounded_producer_consumer": _producer_consumer,
}


PARAMETER_LIMITS: dict[str, dict[str, tuple[int, int]]] = {
    "streaming_statistics": {"count": (1, 2_000_000)},
    "relational_transform": {"rows": (1, 500_000), "filter_mod": (2, 100)},
    "hash_join": {"left_rows": (1, 500_000), "right_rows": (1, 250_000)},
    "sort_group_reduction": {"rows": (1, 500_000), "groups": (2, 4096)},
    "categorical_encoding": {"rows": (1, 250_000), "features": (1, 64), "categories": (2, 4096)},
    "iterative_linear_fit": {"rows": (1, 100_000), "features": (1, 64), "epochs": (1, 10)},
    "dense_matrix_computation": {"dimension": (2, 192)},
    "sparse_graph_traversal": {"nodes": (2, 200_000), "edges_per_node": (1, 16), "visit_limit": (1, 200_000)},
    "text_tfidf": {"documents": (1, 100_000), "terms_per_document": (1, 128), "vocabulary": (2, 65_536)},
    "json_normalization": {"documents": (1, 200_000)},
    "image_convolution": {"width": (3, 4096), "height": (3, 4096)},
    "compression_roundtrip": {"block_bytes": (1, 64 * 1024 * 1024), "level": (0, 9)},
    "rolling_timeseries": {"points": (2, 5_000_000), "window": (1, 65_536)},
    "monte_carlo": {"samples": (1, 5_000_000)},
    "content_deduplication": {"items": (1, 1_000_000), "unique_space": (1, 1_000_000)},
    "bounded_producer_consumer": {"items": (1, 1_000_000), "queue_size": (1, 4096)},
}


@dataclass(frozen=True, slots=True)
class WorkloadResult:
    family_id: str
    operation: str
    deterministic_seed: int
    marker_payload: Mapping[str, Any]
    marker_sha256: str
    correctness_invariants_ok: bool
    correctness_details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "operation": self.operation,
            "deterministic_seed": self.deterministic_seed,
            "marker_payload": dict(self.marker_payload),
            "marker_sha256": self.marker_sha256,
            "correctness_invariants_ok": self.correctness_invariants_ok,
            "correctness_details": dict(self.correctness_details),
            "synthetic_data": True,
            "data_persisted": False,
        }


def validate_parameters(operation: str, parameters: Mapping[str, Any]) -> dict[str, int]:
    limits = PARAMETER_LIMITS.get(operation)
    if limits is None:
        raise ValueError(f"unsupported resource workload operation {operation!r}")
    if set(parameters) != set(limits):
        raise ValueError(f"{operation}: parameters must be exactly {sorted(limits)}")
    normalized: dict[str, int] = {}
    for name, (lower, upper) in limits.items():
        value = parameters[name]
        if not isinstance(value, int) or isinstance(value, bool) or not lower <= value <= upper:
            raise ValueError(f"{operation}.{name} must be an integer in [{lower}, {upper}]")
        normalized[name] = value
    if operation == "sparse_graph_traversal" and normalized["visit_limit"] > normalized["nodes"]:
        raise ValueError("sparse_graph_traversal.visit_limit cannot exceed nodes")
    if operation == "rolling_timeseries" and normalized["window"] > normalized["points"]:
        raise ValueError("rolling_timeseries.window cannot exceed points")
    return normalized


def execute_workload(workload: Mapping[str, Any]) -> WorkloadResult:
    operation = str(workload["operation"])
    parameters = validate_parameters(operation, workload["parameters"])
    seed = int(workload["deterministic_seed"])
    marker_payload = OPERATIONS[operation](seed, parameters)
    expected = dict(workload["correctness_oracle"]["expected_invariants"])
    invariant_ok = marker_payload == expected
    return WorkloadResult(
        family_id=str(workload["family_id"]),
        operation=operation,
        deterministic_seed=seed,
        marker_payload=marker_payload,
        marker_sha256=_canonical_marker(marker_payload),
        correctness_invariants_ok=invariant_ok,
        correctness_details={
            "checker_version": workload["correctness_oracle"]["checker_version"],
            "expected_invariants": expected,
            "observed_invariants": marker_payload,
            "all_invariants_match": invariant_ok,
        },
    )


def verify_workload_result(
    workload: Mapping[str, Any], marker_payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Check a supplied result against a separately frozen expected invariant."""

    expected = dict(workload["correctness_oracle"]["expected_invariants"])
    observed = dict(marker_payload)
    return {
        "checker_version": workload["correctness_oracle"]["checker_version"],
        "expected_invariants": expected,
        "observed_invariants": observed,
        "all_invariants_match": observed == expected,
    }
