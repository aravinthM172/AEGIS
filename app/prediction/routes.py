from fastapi import APIRouter, Query

from app.prediction import service

router = APIRouter(prefix="/api", tags=["Prediction"])


@router.get("/predictions")
def predictions(window_s: float = Query(8.0, ge=3, le=60)):
    """Live early warning: are recent anomalies similar to a known failure signature? Needs live traffic."""
    return service.live_prediction(window_s=window_s)


@router.get("/predictions/backtest")
def backtest():
    """Measured accuracy: leave-one-out replay of every stored experiment. The only source of accuracy claims."""
    return service.run_backtest()
