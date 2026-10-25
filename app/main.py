"""FastAPI application: routes, middleware and error handling."""

from __future__ import annotations

import hmac
import logging
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import __version__
from app.config import Settings, load_settings
from app.idempotency import DuplicateStore
from app.logging_config import request_id_var
from app.luhn import mask_pan
from app.metrics import Metrics
from app.models import (
    Decision,
    ErrorDetail,
    ErrorResponse,
    TransactionRequest,
    ValidationResponse,
)
from app.validation import TransactionValidator, normalise_card_number

logger = logging.getLogger("finsecure.api")

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
QUIET_PATHS = frozenset({"/metrics", "/healthz", "/readyz", "/health"})


class UnauthorizedError(Exception):
    """Raised when an API key is required but missing or wrong."""


def create_app(
    settings: Settings | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    settings = settings or load_settings()
    metrics = Metrics(__version__)
    validator = TransactionValidator(
        settings, DuplicateStore(settings.duplicate_window_seconds), clock
    )
    state = {"started": False, "draining": False}

    def is_ready() -> bool:
        return settings.is_valid and state["started"] and not state["draining"]

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.config_errors:
            logger.error(
                "invalid configuration - instance will report NOT READY",
                extra={"fields": {"config_errors": list(settings.config_errors)}},
            )
        state["started"] = True
        metrics.ready.set(1 if is_ready() else 0)
        logger.info(
            "service started",
            extra={
                "fields": {
                    "environment": settings.environment,
                    "auth_enabled": settings.auth_enabled,
                    "max_transaction_amount": str(settings.max_transaction_amount),
                    "allowed_currencies": sorted(settings.allowed_currencies),
                    "chaos_error_rate": settings.chaos_error_rate,
                }
            },
        )
        yield
        # Graceful shutdown: fail readiness first so the Service stops routing here.
        state["draining"] = True
        metrics.ready.set(0)
        logger.info("service shutting down")

    app = FastAPI(
        title="FinSecure Pay - Transaction Validation API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_api_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_api_docs else None,
    )

    # ------------------------------------------------------------------ middleware
    @app.middleware("http")
    async def observability(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if REQUEST_ID_PATTERN.match(incoming) else str(uuid.uuid4())
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        metrics.in_flight.inc()
        status = 500
        try:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("unhandled error while processing request")
                response = JSONResponse(
                    {"status": "ERROR", "message": "internal error", "request_id": request_id},
                    status_code=500,
                )
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            metrics.in_flight.dec()
            metrics.http_requests.labels(request.method, route_path, str(status)).inc()
            metrics.http_latency.labels(request.method, route_path).observe(elapsed)
            log = logger.debug if request.url.path in QUIET_PATHS else logger.info
            log(
                "request completed",
                extra={
                    "fields": {
                        "method": request.method,
                        "path": request.url.path,
                        "route": route_path,
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 2),
                        "client_ip": request.client.host if request.client else None,
                    }
                },
            )
            request_id_var.reset(token)

    # ------------------------------------------------------------------ errors
    @app.exception_handler(RequestValidationError)
    async def handle_bad_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        metrics.decisions.labels("INVALID").inc()
        errors = [
            ErrorDetail(
                field=".".join(str(p) for p in err.get("loc", ()) if p != "body") or "body",
                message=err.get("msg", "invalid value"),
            )
            for err in exc.errors()
        ]
        logger.info(
            "request rejected by schema validation",
            extra={"fields": {"errors": [e.model_dump() for e in errors]}},
        )
        body = ErrorResponse(errors=errors, request_id=request_id_var.get())
        return JSONResponse(body.model_dump(), status_code=400)

    @app.exception_handler(UnauthorizedError)
    async def handle_unauthorized(_: Request, __: UnauthorizedError) -> JSONResponse:
        logger.warning("rejected request with missing or invalid API key")
        return JSONResponse(
            {"status": "UNAUTHORIZED", "request_id": request_id_var.get()},
            status_code=401,
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # ------------------------------------------------------------------ security
    def require_api_key(request: Request) -> None:
        if not settings.auth_enabled:
            return
        supplied = request.headers.get("x-api-key", "")
        if not any(hmac.compare_digest(supplied, key) for key in settings.api_keys):
            raise UnauthorizedError()

    # ------------------------------------------------------------------ health
    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"service": "finsecure-pay", "version": __version__, "docs": "/docs"}

    @app.get("/health", tags=["health"])
    def health() -> dict:
        """Overall health summary (used for container and smoke tests)."""
        return {
            "status": "UP" if is_ready() else "DEGRADED",
            "service": "finsecure-pay",
            "version": __version__,
            "environment": settings.environment,
        }

    @app.get("/healthz", tags=["health"])
    def liveness() -> dict:
        """Liveness: the process is running and the event loop is responsive."""
        return {"status": "UP"}

    @app.get("/readyz", tags=["health"])
    def readiness() -> JSONResponse:
        """Readiness: configuration is valid and the instance is not draining."""
        ready = is_ready()
        metrics.ready.set(1 if ready else 0)
        body = {
            "status": "READY" if ready else "NOT_READY",
            "checks": {
                "config": "ok" if settings.is_valid else "invalid",
                "lifecycle": "draining" if state["draining"] else "running",
            },
        }
        if settings.config_errors:
            body["errors"] = list(settings.config_errors)
        return JSONResponse(body, status_code=200 if ready else 503)

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> Response:
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    # ------------------------------------------------------------------ business
    def validate_transaction(txn: TransactionRequest) -> JSONResponse:
        if settings.chaos_error_rate and secrets.randbelow(10_000) < int(
            settings.chaos_error_rate * 10_000
        ):
            raise RuntimeError("chaos: simulated downstream failure")

        result = validator.validate(txn)
        card_last4 = mask_pan(normalise_card_number(txn.card_number))
        metrics.decisions.labels(result.decision.value).inc()
        for reason in result.reasons:
            metrics.decline_reasons.labels(reason).inc()
        if result.requires_review:
            metrics.review_flags.inc()

        logger.info(
            "transaction validated",
            extra={
                "fields": {
                    "transaction_id": str(txn.transaction_id),
                    "decision": result.decision.value,
                    "reasons": result.reasons,
                    "risk_level": result.risk_level.value,
                    "requires_review": result.requires_review,
                    "amount": str(txn.amount),
                    "currency": txn.currency.upper(),
                    "merchant_id": txn.merchant_id,
                    "card_last4": card_last4,
                }
            },
        )
        body = ValidationResponse(
            transaction_id=txn.transaction_id,
            decision=result.decision,
            reasons=result.reasons,
            risk_level=result.risk_level,
            requires_review=result.requires_review,
            card_last4=card_last4,
            request_id=request_id_var.get(),
        )
        status = 200 if result.decision is Decision.APPROVED else 422
        return JSONResponse(body.model_dump(mode="json"), status_code=status)

    responses = {
        200: {"model": ValidationResponse, "description": "Transaction approved"},
        400: {"model": ErrorResponse, "description": "Malformed request"},
        401: {"description": "Missing or invalid API key"},
        422: {"model": ValidationResponse, "description": "Transaction declined"},
    }

    @app.post(
        "/api/v1/transactions/validate",
        tags=["transactions"],
        responses=responses,
        dependencies=[Depends(require_api_key)],
    )
    def validate_v1(txn: TransactionRequest) -> JSONResponse:
        """Validate a card transaction against FinSecure Pay's business rules."""
        return validate_transaction(txn)

    @app.post(
        "/validate",
        tags=["transactions"],
        responses=responses,
        dependencies=[Depends(require_api_key)],
    )
    def validate_alias(txn: TransactionRequest) -> JSONResponse:
        """Alias of /api/v1/transactions/validate (path named in the brief)."""
        return validate_transaction(txn)

    return app
