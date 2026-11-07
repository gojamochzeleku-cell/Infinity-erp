#!/usr/bin/env bash
set -o errexit

echo "Installing requirements..."
pip install -r requirements.txt

echo "Collecting static files..."
python manage.py collectstatic --no-input

echo "Generating migrations..."
python manage.py makemigrations core --no-input

echo "Running database migrations..."
python manage.py migrate

echo "Seeding initial companies and users..."
python manage.py seed_infinity

echo "Ensuring SuperAdmin account exists..."
python manage.py shell -c "
from django.contrib.auth.models import User
from core.models import Employee
if not User.objects.filter(username='superadmin').exists():
    user = User.objects.create_user('superadmin', password='Infinity@123', is_staff=True, is_superuser=True)
    Employee.objects.create(user=user, role='SUPERADMIN', is_group_level=True, is_active=True)
    print('✅ SuperAdmin created on Render.')
else:
    print('SuperAdmin already exists.')
"
