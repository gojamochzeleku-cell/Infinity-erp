#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")"

echo "Installing Python dependencies..."
python -m pip install -r requirements.txt

echo "Collecting static files..."
python manage.py collectstatic --no-input

echo "Applying database migrations..."
python manage.py migrate --noinput

echo "Render build and database migrations completed successfully."
