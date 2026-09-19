"""Entry point for the monitored copy of the Job Tracker: the original app plus FaultScope hooks.

Order matters: the fault hook is registered first (inner) and the telemetry middleware last
(outermost), so injected failures are recorded like any other response.
"""
from main import app  # the Job Tracker's own FastAPI app, unchanged

from app.fault_hook import install as install_fault_hook
from app.telemetry_middleware import install as install_telemetry

install_fault_hook(app)
install_telemetry(app)
