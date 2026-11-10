from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    Company,
    Employee,
    CompanyMembership,
    MembershipRole,
    MEMBERSHIP_ROLE_CHOICES,
)


PEOPLE = [
    ('admin', 'System Administrator', 'ADMIN', None),
    ('ceo', 'Chief Executive Officer', 'CEO', None),
    ('gm', 'General Manager', 'GM', None),
    ('vgm', 'Vice General Manager', 'VGM', None),
    ('it', 'IT Coordinator', 'IT', None),
    ('bidmanager', 'Bid Manager', 'BIDM', 'YAH'),
    ('bidmanager2', 'Bid Manager (Capital)', 'BIDM', 'CHI'),
    ('caseexec', 'Case Executive', 'CASE', 'YAH'),
    ('finance', 'Finance Manager', 'FIN', 'YAH'),
    ('hr', 'HR Manager', 'HR', 'YAH'),
    ('office', 'Office Assistant', 'OFFICE', 'YAH'),
    ('bidop', 'Bid Operator', 'BIDOP', 'YAH'),
    ('legal', 'Legal Counselor', 'LEGAL', 'YAH'),
    ('proc', 'Procurement & Import Officer', 'PROC', 'YAH'),
    ('logistics', 'Logistics & Delivery Officer', 'LOG', 'YAH'),
    ('staff', 'Staff', 'STAFF', 'YAH'),
]

SHARED_ACCOUNTS = {'admin', 'ceo', 'gm', 'vgm', 'it'}


class Command(BaseCommand):
    help = 'Create company accounts and company-specific memberships safely.'

    @transaction.atomic
    def handle(self, *args, **kwargs):
        yah, _ = Company.objects.get_or_create(
            short_name='YAH',
            defaults={'name': 'Yirga Agedom Haile Import'},
        )
        chi, _ = Company.objects.get_or_create(
            short_name='CHI',
            defaults={'name': 'Capital Hailu Import'},
        )

        valid_roles = {
            code for code, label in MEMBERSHIP_ROLE_CHOICES
        }

        created_users = 0
        created_employees = 0
        created_memberships = 0
        created_roles = 0

        for username, label, role, company_code in PEOPLE:
            if role not in valid_roles:
                raise RuntimeError(
                    'Invalid membership role for %s: %s'
                    % (username, role)
                )

            user, user_created = User.objects.get_or_create(
                username=username
            )

            if user_created:
                user.set_unusable_password()
                user.first_name = label
                created_users += 1

            # Enforce company-scoped access for both new and existing users.
            # Existing passwords and company memberships remain unchanged.
            user.is_staff = False
            user.is_superuser = False
            user.save()

            employee, employee_created = Employee.objects.get_or_create(
                user=user,
                defaults={
                    'company': (
                        yah if company_code == 'YAH'
                        else chi if company_code == 'CHI'
                        else None
                    ),
                    'role': role,
                    'is_group_level': False,
                },
            )

            if employee_created:
                created_employees += 1

            if username in SHARED_ACCOUNTS:
                assignments = [(yah, role), (chi, role)]
            elif employee.company_id:
                assignments = [(employee.company, employee.role)]
            else:
                raise RuntimeError(
                    'Employee %s has no company assignment.'
                    % username
                )

            for company, assigned_role in assignments:
                if assigned_role not in valid_roles:
                    raise RuntimeError(
                        'Invalid role %s for %s in %s'
                        % (assigned_role, username, company.short_name)
                    )

                membership, membership_created = (
                    CompanyMembership.objects.get_or_create(
                        employee=employee,
                        company=company,
                        defaults={
                            'is_active': employee.is_active,
                            'base_salary': employee.base_salary,
                            'contract_expiry': employee.contract_expiry,
                        },
                    )
                )

                if membership_created:
                    created_memberships += 1

                _, role_created = MembershipRole.objects.get_or_create(
                    membership=membership,
                    role=assigned_role,
                )

                if role_created:
                    created_roles += 1

        self.stdout.write(
            self.style.SUCCESS(
                'Seed completed. '
                'New users: %s; employees: %s; memberships: %s; roles: %s.'
                % (
                    created_users,
                    created_employees,
                    created_memberships,
                    created_roles,
                )
            )
        )
        self.stdout.write(
            'New accounts have unusable passwords. '
            'Existing passwords are preserved.'
        )
        self.stdout.write(
            'No Django superuser or staff account is created.'
        )
