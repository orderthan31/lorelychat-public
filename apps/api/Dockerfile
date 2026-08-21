FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 10001 lorechat \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin lorechat

WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY seeds ./seeds
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /data /uploads /logs /secrets \
    && chown -R 10001:10001 /app /data /uploads /logs /secrets

USER 10001:10001
EXPOSE 8123
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8123", "--no-access-log"]
