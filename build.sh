#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")"

echo "Installing Python dependencies..."
python -m pip install -r requirements.txt

echo "Collecting static files..."
python manage.py collectstatic --no-input

echo "Render build completed successfully."
echo "Database migrations and account creation are separate deployment steps."
