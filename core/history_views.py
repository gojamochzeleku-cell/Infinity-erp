from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.db.models import Q
from .models import TaskHistory

@login_required
def task_history(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('select_company')

    query = request.GET.get('q', '').strip()
    history = TaskHistory.objects.filter(company=company)

    if query:
        history = history.filter(
            Q(task_title__icontains=query) | 
            Q(task_code__icontains=query) | 
            Q(completed_by__user__username__icontains=query)
        )

    return render(request, 'task_history.html', {'history': history[:200], 'query': query})
