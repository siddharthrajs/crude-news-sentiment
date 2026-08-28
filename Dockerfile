FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Torch ships by default: the default SCORER_MODE=sentiment needs FinBERT, and
# without it every headline goes unscored. Build with --build-arg INSTALL_ML=0
# for a ~2GB smaller image -- only useful for the event scorer, which can fall
# back to rules alone.
ARG INSTALL_ML=1

COPY pyproject.toml README.md ./
COPY src ./src
RUN if [ "$INSTALL_ML" = "1" ]; then \
        pip install --no-cache-dir ".[ml]" \
            --extra-index-url https://download.pytorch.org/whl/cpu; \
    else \
        pip install --no-cache-dir . ; \
    fi && \
    useradd --create-home --uid 10001 appuser && \
    mkdir -p /app/data/hf && chown -R appuser /app/data
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).status==200 else 1)"

CMD ["python", "-m", "cns"]
