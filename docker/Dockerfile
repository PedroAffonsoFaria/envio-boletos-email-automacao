FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

RUN apt-get update && apt-get install -y curl bash && rm -rf /var/lib/apt/lists/*

# Dependências Python (contexto de build = raiz do repo)
COPY docker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Browser do Playwright (chromium basta)
RUN playwright install chromium

# Código (src/ vira raiz do app → imports flat: wba, email_boleto)
COPY src/wba.py .
COPY src/email_boleto.py .
COPY src/enviar_boletos_email.py .

# Diretório de downloads (ephemeral em Cloud Run)
ENV DOWNLOAD_DIR=/tmp/boletos_email
RUN mkdir -p /tmp/boletos_email

# Cloud Run JOB (batch agendado) — roda o script e termina.
CMD ["python", "enviar_boletos_email.py"]
