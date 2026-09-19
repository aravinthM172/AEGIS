FROM python:3.12-slim

# iproute2 provides `tc`, used (via a helper container joined to the target's network
# namespace) to add network latency with netem.
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent ./agent

EXPOSE 8095

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8095"]
