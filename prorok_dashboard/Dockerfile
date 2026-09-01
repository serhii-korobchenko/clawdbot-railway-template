FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY prorok_dashboard/requirements.txt /app/prorok_dashboard/requirements.txt
RUN pip install --no-cache-dir -r /app/prorok_dashboard/requirements.txt

COPY prorok_dashboard /app/prorok_dashboard

CMD ["sh", "-c", "exec python -m uvicorn prorok_dashboard.app.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --no-access-log"]
