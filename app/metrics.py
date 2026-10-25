"""Prometheus metrics exposed on GET /metrics.

RED metrics (Rate, Errors, Duration) per route plus business metrics on
validation decisions. Process CPU/memory metrics come from prometheus_client's
default collectors; container-level metrics come from cAdvisor/kube-state-metrics.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class Metrics:
    """Holds all metrics on a dedicated registry (keeps tests isolated)."""

    def __init__(self, version: str) -> None:
        self.registry = CollectorRegistry()
        ProcessCollector(registry=self.registry)
        PlatformCollector(registry=self.registry)
        GCCollector(registry=self.registry)

        self.http_requests = Counter(
            "http_requests_total",
            "HTTP requests by method, route template and status code",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.http_latency = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency by method and route template",
            ["method", "route"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.in_flight = Gauge(
            "http_requests_in_flight",
            "HTTP requests currently being processed",
            registry=self.registry,
        )
        self.decisions = Counter(
            "finsecure_transactions_validated_total",
            "Validation decisions (APPROVED / DECLINED / INVALID)",
            ["decision"],
            registry=self.registry,
        )
        self.decline_reasons = Counter(
            "finsecure_transaction_decline_reasons_total",
            "Reason codes for declined transactions",
            ["reason"],
            registry=self.registry,
        )
        self.review_flags = Counter(
            "finsecure_transactions_review_total",
            "Approved transactions flagged for manual review",
            registry=self.registry,
        )
        self.ready = Gauge(
            "finsecure_ready",
            "1 when the instance reports ready on /readyz",
            registry=self.registry,
        )
        self.info = Gauge(
            "finsecure_build_info",
            "Build information",
            ["version"],
            registry=self.registry,
        )
        self.info.labels(version=version).set(1)
