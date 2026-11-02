from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q
from .models import Tender, Document, Employee, Task
from .services import notify

@login_required
def global_search(request):
    company = getattr(request, 'company', None)
    query = request.GET.get('q', '').strip()
    
    if not company or not query:
        return render(request, 'search_results.html', {'query': query})

    # Search Tenders
    tenders = Tender.objects.filter(company=company).filter(
        Q(bid_title__icontains=query) | Q(serial_number__icontains=query) | Q(purchaser_name__icontains=query)
    )[:10]

    # Search Documents
    documents = Document.objects.filter(company=company).filter(
        Q(title__icontains=query) | Q(doc_id__icontains=query)
    )[:10]

    # Search Employees
    employees = Employee.objects.filter(
        Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query) | Q(role__icontains=query)
    )[:10]

    return render(request, 'search_results.html', {
        'query': query, 'tenders': tenders, 'documents': documents, 'employees': employees
    })

@login_required
def reassign_task(request, pk):
    emp = getattr(request.user, 'employee', None)
    task = get_object_or_404(Task, pk=pk)
    
    # Only management can reassign
    if not emp or emp.role not in ('ADMIN', 'CEO', 'GM', 'VGM'):
        return redirect('dashboard')

    if request.method == 'POST':
        new_emp_id = request.POST.get('new_employee')
        new_emp = Employee.objects.filter(id=new_emp_id).first()
        
        if new_emp:
            old_emp = task.assigned_to
            task.assigned_to = new_emp
            task.save()
            
            # Notify new employee
            notify(new_emp, f'Task Reassigned to You: {task.title}', f'Reassigned by {emp.full_name}', task, '/dashboard/')
            
            # Log it
            from .services import log_action
            log_action(emp, 'TASK_REASSIGNED', f'Reassigned {task.code} from {old_emp.full_name} to {new_emp.full_name}', 'Task', task.id, task.company)
            
            return redirect('task_detail', pk=task.id)

    employees = Employee.objects.all()
    return render(request, 'reassign_task.html', {'task': task, 'employees': employees})
