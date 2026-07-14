FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AW_DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/

RUN mkdir -p /data
VOLUME ["/data"]

# Bound to loopback only — never exposed on the network. All service calls
# happen server-side. Uses host networking to reach the local services.
EXPOSE 8888
CMD ["uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8888"]
