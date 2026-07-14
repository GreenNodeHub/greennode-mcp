"""Extract the kubeconfig YAML from the VKS kubeconfig endpoint response.

The endpoint used to return the bare YAML; it now wraps it in a JSON envelope
``{kubeConfig, status, expirationAt, expirationDays, renewalWarning}`` — and a
cluster that is not ACTIVE yet has no ``kubeConfig`` field at all. Feeding the
envelope to the kubernetes client library fails with "Expected key
current-context in kube-config".
"""

from __future__ import annotations

import json


def extract_kubeconfig(raw: str) -> str:
    """Return kubectl-ready YAML from either response shape.

    Raises ValueError with an actionable message when the cluster has no
    kubeconfig yet (not ACTIVE).
    """
    text = raw.strip()
    if not text.startswith("{"):
        return raw  # legacy shape: the bare YAML itself

    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        return raw  # not the JSON envelope after all — treat as YAML

    kubeconfig = envelope.get("kubeConfig") if isinstance(envelope, dict) else None
    if not kubeconfig:
        status = envelope.get("status", "unknown") if isinstance(envelope, dict) else "unknown"
        raise ValueError(
            f"Kubeconfig is not available (status: '{status}'). A new cluster has "
            "no kubeconfig until one is generated: call "
            "generate_kubeconfig(cluster_id) first (requires --allow-write; "
            "generation is asynchronous), then retry this tool until it returns "
            "YAML. Also make sure the cluster itself is ACTIVE (get_cluster). "
            "Full flow: get_creation_guide(resource='kubeconfig')."
        )
    return kubeconfig
