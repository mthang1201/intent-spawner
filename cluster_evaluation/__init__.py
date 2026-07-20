"""Kubernetes-backed evaluation helpers.

This package is intentionally separate from ``experiments``.  The latter
preserves the historical local synthetic matrix; this package creates pods and
must only be used against a disposable cluster.
"""

