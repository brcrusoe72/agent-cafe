FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application
COPY . .

# Create non-root user
RUN useradd -m cafe && chown -R cafe:cafe /app
USER cafe

# Environment
ENV CAFE_HOST=0.0.0.0
ENV CAFE_PORT=8790

EXPOSE 8790

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8790/board')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8790"]
