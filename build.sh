#!/usr/bin/env bash
set -o errexit

echo "Installing requirements..."
pip install -r requirements.txt

echo "Collecting static files..."
python manage.py collectstatic --no-input

echo "Generating any missing migrations..."
python manage.py makemigrations core

echo "Running database migrations..."
python manage.py migrate

echo "Seeding initial companies and users..."
python manage.py seed_infinity
