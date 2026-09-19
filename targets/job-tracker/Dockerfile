# FaultScope wrapper around the user's Job Tracker (built separately as job-tracker-base:local from
# the untouched Job-Tracker checkout). Adds only: the telemetry middleware, the HTTP-error fault
# hook and their one dependency. No Job Tracker source is modified.
# Build context: the Aegis repository root (see docker-compose.yml here).
FROM job-tracker-base:local

RUN pip install --no-cache-dir kafka-python-ng

COPY app/telemetry.py app/telemetry_middleware.py app/fault_hook.py /opt/faultscope/app/
COPY targets/job-tracker/faultscope_entry.py /opt/faultscope/faultscope_entry.py
RUN touch /opt/faultscope/app/__init__.py

ENV PYTHONPATH=/opt/faultscope
CMD ["uvicorn", "faultscope_entry:app", "--host", "0.0.0.0", "--port", "8000"]
