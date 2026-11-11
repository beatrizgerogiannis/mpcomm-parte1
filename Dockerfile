FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# iproute2: provides "tc" (netem) to simulate loss/delay in later steps
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
