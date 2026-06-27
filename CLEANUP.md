# Cleanup

All demo resources are isolated in namespace:

```text
z2jh-context-demo
```

Remove the demo:

```bash
bash scripts/uninstall.sh
```

The uninstall script runs only:

```bash
kubectl delete namespace z2jh-context-demo --ignore-not-found
```

It does not delete:

- The Kubernetes cluster.
- The kubeconfig context.
- Namespaces outside `z2jh-context-demo`.
- Cluster-wide resources.

If a port-forward is running, stop it with `Ctrl+C` in that terminal.

