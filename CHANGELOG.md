# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-25

### Added
- Transaction validation API with health, liveness, readiness and metrics endpoints.
- Business rules: amount limits and precision, currency, Luhn card check, expiry,
  merchant ID, blocked countries, duplicate detection, manual-review flag.
- Structured JSON logging with request IDs and card-number masking.
- Unit and integration tests (100 % coverage, 90 % gate).
- Multi-stage distroless container image running as non-root.
- Kubernetes manifests for EKS: probes, HPA, PDB, NetworkPolicy, ALB Ingress.
- Jenkins pipeline with secret, SAST, dependency, IaC and image scanning gates,
  automated deployment, smoke test and automatic rollback.
- Prometheus alerts (unit-tested), Grafana dashboard, Loki logging, e-mail/Slack routing.
- AI-assisted root-cause analysis for failed pipeline runs.
