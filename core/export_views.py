import csv
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from .models import AuditLog

@login_required
def export_audit_log(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company or emp.role not in ('ADMIN', 'CEO', 'IT'):
        return redirect('dashboard')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="Audit_Log_{company.short_name}.csv"'

    writer = csv.writer(response)
    writer.writerow(['Date', 'User', 'Action', 'Details', 'Object Type', 'Object ID'])

    logs = AuditLog.objects.filter(company=company).order_by('-created_at')
    for log in logs:
        writer.writerow([
            log.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            log.actor.full_name if log.actor else 'System',
            log.action, log.detail, log.object_type, log.object_id
        ])
    return response
