FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    unzip \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user early
RUN useradd --create-home --shell /bin/bash novbot

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download kaf-cli for Linux amd64
ARG KAF_CLI_VERSION=v1.3.15
RUN curl -fsSL "https://github.com/ystyle/kaf-cli/releases/download/${KAF_CLI_VERSION}/kaf-cli_${KAF_CLI_VERSION}_linux_amd64.zip" \
    -o /tmp/kaf-cli.zip \
    && unzip -o /tmp/kaf-cli.zip -d /tmp/kaf-cli/ \
    && cp /tmp/kaf-cli/kaf-cli /usr/local/bin/kaf-cli \
    && chmod +x /usr/local/bin/kaf-cli \
    && rm -rf /tmp/kaf-cli.zip /tmp/kaf-cli/

# Copy application
COPY bot/ /app/bot/

# Create data directory and set ownership
RUN mkdir -p /app/data && chown -R novbot:novbot /app

# Switch to non-root
USER novbot

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD kaf-cli --help > /dev/null 2>&1 && python -c "from bot.main import main" || exit 1

CMD ["python", "-m", "bot.main"]
