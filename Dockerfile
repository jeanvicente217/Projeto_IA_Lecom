FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# usuário sem privilégios: o container roda com read_only e só escreve no volume data/
RUN mkdir -p data/chroma data/exports data/raw \
    && useradd --create-home --uid 10001 n1 \
    && chown -R n1:n1 /srv
USER n1

VOLUME ["/srv/data"]
EXPOSE 8000

# proxy-headers: o Nginx na frente envia X-Forwarded-For, usado no bloqueio por IP
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
