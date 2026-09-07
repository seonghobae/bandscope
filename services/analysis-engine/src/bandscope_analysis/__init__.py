"""BandScope analysis engine package."""

import logging
from importlib import import_module

from .health import build_health_report

_STEM_SAFE_FAILURE_LOG_MESSAGES = frozenset(
    {
        "Stem separation failed because the source file was missing.",
        "Stem separation unavailable because Demucs or torch is not installed.",
        "Stem separation rejected invalid audio source data.",
        "Stem separation failed with a runtime error.",
        "Stem separation failed unexpectedly.",
        "Stem separation failed before analysis job completion.",
    }
)


class _ApiDiagnosticPrivacyFilter(logging.Filter):
    """Redact traceback payloads only for known stem safe-failure diagnostics."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Preserve unrelated diagnostics while redacting owned safe-failure tracebacks."""
        if record.getMessage() in _STEM_SAFE_FAILURE_LOG_MESSAGES:
            record.exc_info = None
            record.exc_text = None
        return True


_api_logger = logging.getLogger("bandscope_analysis.api")
_api_logger.addFilter(_ApiDiagnosticPrivacyFilter())
_api_module = import_module(".api", __name__)
get_analysis_status = _api_module.get_analysis_status

__all__ = ["build_health_report", "get_analysis_status"]
