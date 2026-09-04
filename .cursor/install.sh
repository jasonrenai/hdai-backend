#!/usr/bin/env bash
# Idempotent environment bootstrap for the HD AI backend.
# Installs durable system dependencies (MongoDB + Python venv tooling), the
# Python virtualenv and packages, and a local .env for development.
set -euo pipefail

# Always operate from the repository root (this script lives in .cursor/).
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- System dependencies (durable; captured in the environment build snapshot) ---
if ! command -v mongod >/dev/null 2>&1; then
  echo ">> Installing MongoDB 8.0 + Python venv tooling..."
  sudo apt-get update -y
  sudo apt-get install -y --no-install-recommends gnupg curl ca-certificates
  curl -fsSL https://pgp.mongodb.com/server-8.0.asc \
    | sudo gpg -o /usr/share/keyrings/mongodb-server-8.0.gpg --dearmor --yes
  echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] https://repo.mongodb.org/apt/ubuntu noble/mongodb-org/8.0 multiverse" \
    | sudo tee /etc/apt/sources.list.d/mongodb-org-8.0.list
  sudo apt-get update -y
  sudo apt-get install -y mongodb-org python3-venv
fi

# --- Python virtualenv + dependencies (repo-dependent) ---
if [ ! -x .venv/bin/python ]; then
  echo ">> Creating Python virtualenv..."
  python3 -m venv .venv
fi
echo ">> Installing Python requirements..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# --- MongoDB data + log directories ---
sudo mkdir -p /data/db /var/log/mongodb
sudo chown -R "$(id -u):$(id -g)" /data/db /var/log/mongodb

# --- Local .env (created only if missing; never overwrites real config) ---
if [ ! -f .env ]; then
  echo ">> Writing development .env..."
  JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  cat > .env <<EOF
# Auto-generated local development configuration (gitignored).
MONGODB_CONNECTION_STRING=mongodb://localhost:27017
DB_NAME=hdai_dev
JWT_SECRET=${JWT_SECRET}

# Email sending is ON so the app delivers via Postmark. Provide the Postmark
# Server API token via the Cloud Agent "Secrets" panel as POSTMARK_SERVER_API_TOKEN
# (a real env-var secret takes precedence over this gitignored .env). Optionally
# override the verified sender addresses with EMAIL_FROM_HELLO / EMAIL_FROM_ALERTS /
# EMAIL_FROM_SUPPORT. Set EMAIL_SENDING_ENABLED=false to silence sends locally.
EMAIL_SENDING_ENABLED=true
# POSTMARK_SERVER_API_TOKEN=  # provided via Secrets; uncomment to override locally

# Azurite well-known dev credentials so the blob client constructs without real
# Azure (no network is required at construction; uploads are not exercised locally).
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;
AZURE_STORAGE_CONTAINER=dev-container

# Disable crons that reach out to external services (SERP/scrapers/LLM) on startup.
ENABLE_PENDING_GOOGLE_QUERY_CRON=false
ENABLE_PENDING_SCRAPERS_CRON=false
ENABLE_OPPORTUNITY_VERIFY_CRON=false
EOF
fi

echo ">> install.sh complete."
