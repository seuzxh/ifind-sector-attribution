"""Read-only HTTP adapter for the frozen opening-theme dashboard."""

from datetime import datetime
import logging
import re
from threading import Lock

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from api import deps
from opening_strength.dashboard_service import OpeningDashboardService
from opening_strength.quote_provider import OpeningDashboardError, OpeningQuoteProvider


router = APIRouter()
logger = logging.getLogger(__name__)
_service: OpeningDashboardService | None = None
_service_lock = Lock()
_DATE = re.compile(r"\A[0-9]{8}\Z")
_TIME = re.compile(r"\A[0-9]{2}:[0-9]{2}\Z")
_ERROR_STATUS = {
    "SNAPSHOT_NOT_FOUND": 404,
    "QUOTE_DATA_UNAVAILABLE": 503,
    "QUOTE_PROVIDER_FAILED": 503,
}


def get_dashboard_service() -> OpeningDashboardService:
    """Create one service on demand; quote access owns fetcher initialization."""
    global _service
    with _service_lock:
        if _service is None:
            _service = OpeningDashboardService(deps.db, OpeningQuoteProvider())
        return _service


def _error(status: int, code: str, message: str, retryable: bool) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "error": {"code": code, "message": message, "retryable": retryable},
    })


def invalid_request_response() -> JSONResponse:
    return _error(422, "INVALID_REQUEST", "日期须为 YYYYMMDD，时点须为不早于 09:30 的 HH:MM", False)


@router.get("/api/opening-strength/dashboard")
def get_opening_dashboard(trade_date: str = Query(...), snapshot_time: str | None = None):
    """Validate the requested date/minute and serialize the domain dashboard."""
    try:
        if _DATE.fullmatch(trade_date) is None:
            raise ValueError
        datetime.strptime(trade_date, "%Y%m%d")
        if snapshot_time is not None:
            if _TIME.fullmatch(snapshot_time) is None:
                raise ValueError
            datetime.strptime(snapshot_time, "%H:%M")
            if snapshot_time < "09:30":
                raise ValueError
    except ValueError:
        return invalid_request_response()

    try:
        dashboard = get_dashboard_service().build(trade_date, snapshot_time)
        return JSONResponse(content=dashboard.to_dict())
    except OpeningDashboardError as error:
        status = _ERROR_STATUS.get(error.code)
        if status is not None:
            return _error(status, error.code, error.message, error.retryable)
    except Exception:
        # Keep upstream exception messages, paths and credentials out of the API
        # and its error summary; the domain service records known run IDs.
        pass
    logger.warning("Opening dashboard unavailable trade_date=%s code=QUOTE_PROVIDER_FAILED", trade_date)
    return _error(503, "QUOTE_PROVIDER_FAILED", "行情获取暂时失败，请稍后重试", True)
