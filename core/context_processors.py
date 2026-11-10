from .models import Notification


def infinity_context(request):
    data = {
        'unread_count': 0,
        'recent_notifications': [],
        'current_company': None,
        'membership_roles': set(),
    }

    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)
    data['current_company'] = company

    if (
        request.user.is_authenticated
        and request.user.is_active
        and company is not None
        and membership is not None
        and membership.is_active
        and membership.company_id == company.id
        and membership.employee.user_id == request.user.id
        and membership.employee.is_active
    ):
        data['membership_roles'] = set(
            membership.roles.values_list('role', flat=True)
        )

        notifications = Notification.objects.filter(
            employee=membership.employee,
            is_read=False,
            task__company=company,
        ).order_by('-id')

        data['unread_count'] = notifications.count()
        data['recent_notifications'] = list(notifications[:5])

    return data
