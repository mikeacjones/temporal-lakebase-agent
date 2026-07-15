# EKS runtime chart

This chart keeps the web/API and worker lifecycles separate:

- a normal Kubernetes `Deployment` and `Service` run the FastAPI server and built React UI;
- a Temporal Worker Controller `WorkerDeployment` creates a Kubernetes Deployment per Worker Deployment Version;
- `WorkerResourceTemplate` objects create a version-aware HPA and PDB for every active worker version.

Prerequisites are Temporal Server/Cloud 1.29.1 or newer, cert-manager, and Temporal Worker Controller 1.7.0 or newer (chart/CRD version 0.26.0 in the adjacent EKS reference). Install the controller with `controller-values.yaml` so HPA and PDB templates are allowed.

The chart references an existing Kubernetes Secret and never contains secret values. Its default name is `temporal-lakebase-agentworkflow-secrets`, with keys documented under `secrets.keys` in `values.yaml`. Override image repositories/tags and configuration in a private values file, then validate without deploying:

```bash
helm lint deploy/helm/temporal-lakebase-agentworkflow
helm template loan-agent deploy/helm/temporal-lakebase-agentworkflow \
  --namespace tf-demo-zsvab
```

The checked-in `values.eks.yaml` contains the current immutable ECR image coordinates and no secrets. Deploy or upgrade this environment with:

```bash
helm upgrade --install lakebase-agentworkflow \
  deploy/helm/temporal-lakebase-agentworkflow \
  --namespace tf-demo-zsvab \
  --values deploy/helm/temporal-lakebase-agentworkflow/values.eks.yaml \
  --rollback-on-failure \
  --wait \
  --timeout 15m
```

The service intentionally remains private inside the cluster. Open a temporary loopback-only demo connection with:

```bash
kubectl port-forward \
  service/lakebase-agentworkflow-web \
  18001:80 \
  --address 127.0.0.1 \
  --namespace tf-demo-zsvab
```

Then open `http://127.0.0.1:18001`. Add a separately reviewed authenticated ingress or load balancer before exposing the UI beyond the local port forward.

The controller owns `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_DEPLOYMENT_NAME`, and `TEMPORAL_WORKER_BUILD_ID` in worker pods. The Python worker consumes the deployment/build pair to enable pinned Worker Deployment Versioning. Local runs omit those variables and remain unversioned.
