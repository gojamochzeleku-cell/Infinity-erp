from django.db.models import Q
from .models import Notification

def infinity_context(request):
    data = {'unread_count': 0, 'recent_notifications': [], 'current_company': None}
    
    if request.user.is_authenticated:
        emp = getattr(request.user, 'employee', None)
        if emp is not None:
            company = getattr(request, 'company', None)
            if company:
                qs = Notification.objects.filter(employee=emp, is_read=False).filter(Q(task__company=company) | Q(task__isnull=True))
                data['unread_count'] = qs.count()
                data['recent_notifications'] = list(qs[:5])
            else:
                data['unread_count'] = 0
                data['recent_notifications'] = []
                
    data['current_company'] = getattr(request, 'company', None)
    return data
