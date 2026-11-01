from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from core.models import Company, Employee

PEOPLE = [
    ('admin',      'System Administrator',          'ADMIN',  None,  True),
    ('ceo',        'Chief Executive Officer',       'CEO',    None,  True),
    ('gm',         'General Manager',               'GM',     None,  True),
    ('vgm',        'Vice General Manager',          'VGM',    None,  True),
    ('it',         'IT Coordinator',                'IT',     None,  True),
    ('bidmanager', 'Bid Manager',                   'BIDM',   'YAH', False),
    ('bidmanager2','Bid Manager (Capital)',         'BIDM',   'CHI', False),
    ('caseexec',   'Case Executive',                'CASE',   'YAH', False),
    ('finance',    'Finance Manager',               'FIN',    'YAH', False),
    ('hr',         'HR Manager',                    'HR',     'YAH', False),
    ('office',     'Office Assistant',              'OFFICE', 'YAH', False),
    ('bidop',      'Bid Operator',                  'BIDOP',  'YAH', False),
    ('legal',      'Legal Counselor',               'LEGAL',  'YAH', False),
    ('proc',       'Procurement & Import Officer',  'PROC',   'YAH', False),
    ('logistics',  'Logistics & Delivery Officer',  'LOG',    'YAH', False),
    ('staff',      'Staff',                         'STAFF',  'YAH', False),
]


class Command(BaseCommand):
    help = 'Create base companies and role accounts for Infinity Investment Institute'

    def handle(self, *args, **kwargs):
        yah, _ = Company.objects.get_or_create(
            name='Yirga Agedom Haile Import', defaults={'short_name': 'YAH'})
        chi, _ = Company.objects.get_or_create(
            name='Capital Hailu Import', defaults={'short_name': 'CHI'})
        if not yah.short_name:
            yah.short_name = 'YAH'
            yah.save()
        if not chi.short_name:
            chi.short_name = 'CHI'
            chi.save()

        created_count = 0
        for username, label, role, comp, group in PEOPLE:
            company = None
            if comp == 'YAH':
                company = yah
            elif comp == 'CHI':
                company = chi
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.set_password('Infinity@123')
                user.first_name = label
                user.is_staff = role in ('ADMIN', 'IT')
                if role == 'ADMIN':
                    user.is_superuser = True
                    user.is_staff = True
                user.save()
                created_count += 1
            emp, ecreated = Employee.objects.get_or_create(
                user=user,
                defaults={'company': company, 'role': role, 'is_group_level': group})
            if ecreated:
                self.stdout.write('  + %-14s %-7s %s' % (username, role, company))

        self.stdout.write(self.style.SUCCESS(
            'Seed complete. %s new user(s). Password for all: Infinity@123' % created_count))
