# Cloud plan (Phase 13) — NOT executed

**Status: blocked by design.** This machine has no AWS CLI, no credentials and no Terraform, and the project rule
is to introduce cloud only after the local system works and to **never assume AWS is free**. Nothing here has been
created, applied or tested. Prices and free-tier terms change and vary by account age and region: **verify them in the
AWS console / pricing pages before creating anything.** Treat every line below as a plan, not a fact.

## What would be worth doing, and why

| Component | Purpose in FaultScope | Cost/free-tier note (verify) |
|---|---|---|
| **S3** | Store experiment reports, signatures exports and telemetry archives so history survives the laptop | Free tier historically covers a small amount of storage and requests for a limited period; storage beyond it is billed |
| **ECR** or Docker Hub | Host the four images the manifests reference | Private ECR storage is billed per GB-month (small); public registries avoid this |
| **EKS** | Run `k8s/` on managed Kubernetes | **Not free-tier eligible.** The EKS control plane has an hourly charge in addition to worker nodes; a demo cluster left running costs real money every hour |
| **EC2 (single instance) + kind/k3s** | The cheapest way to show the same cluster remotely | A small instance can fall in a free-tier allowance for new accounts; an instance left running beyond it is billed |
| **CloudWatch** | Optional: ship logs/metrics | Free allowances exist but ingestion above them is billed; FaultScope already stores its own telemetry, so this adds little |
| **IAM** | Least-privilege roles for anything above | No charge, but a mistake here is a security problem, not a billing one |

## Recommendation

1. Do **not** use EKS for this project: it is the one component that is clearly not free and is not needed to
   demonstrate anything the local `kind` cluster does not already show.
2. If a remote demo is wanted, use **one small EC2 instance** running Docker Compose or k3s, with a budget alert
   and an auto-stop, and tear it down after the demo.
3. Use **S3** only for report/signature archives, with a lifecycle rule.
4. Before any of this: create a **billing budget with alerts**, use a dedicated IAM user (never root), and keep
   `FAULTSCOPE_API_KEYS`, `FAULT_AGENT_TOKEN` and the Docker-socket-holding fault-agent **off any public network**:
   the agent has root-equivalent access to its host by design.

## Security notes that apply before exposing anything

- The fault-agent mounts the Docker socket. It must never be reachable from the internet.
- Replace the dev keys (`dev-operator-key`, `dev-admin-key`, `dev-token-change-me`) and rotate them.
- The control plane's read endpoints are unauthenticated; put it behind an authenticating proxy if it is exposed.

## Decision needed from the owner

To proceed, provide an AWS account with a budget alert, choose the EC2-only option above, and confirm the maximum
monthly spend you accept. Until then, Phase 13 stays a plan.
