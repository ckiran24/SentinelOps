"""Structured logs and optional local OTel exporter; persisted traces always work."""

import json
import logging
from datetime import UTC, datetime

from .config import get_settings

_TELEMETRY_STARTED = False


class JsonFormatter(logging.Formatter):
    def format(self, record):
        document = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            document["exception"] = self.formatException(record.exc_info)
        return json.dumps(document, ensure_ascii=False)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def configure_telemetry(service_name: str):
    global _TELEMETRY_STARTED
    if _TELEMETRY_STARTED or not get_settings().otel_console_export:
        return
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _TELEMETRY_STARTED = True
