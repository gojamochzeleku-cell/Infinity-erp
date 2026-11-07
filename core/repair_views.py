from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from .models import Tender, Task
from .chain import trigger_ai_tasks
from .services import log_action

@login_required
def repair_chain(request):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role not in ('ADMIN', 'CEO', 'IT'):
        return redirect('dashboard')

    if request.method == 'POST':
        fixed_count = 0
        tenders = Tender.objects.filter(purchased_bid_document__isnull=False).exclude(status__in=['WON', 'LOST', 'COMPLETED', 'DRAFT'])
        
        for t in tenders:
            # Check if the NEW T09 task is missing
            has_t09 = Task.objects.filter(payload__tender_id=t.id, code='T09').exists()
            if not has_t09:
                # Clean up old legacy tasks just in case
                Task.objects.filter(payload__tender_id=t.id, code__in=['T09A', 'T09B', 'T09C', 'T09D']).delete()
                
                # Mark T08 as complete if it's stuck
                t08 = Task.objects.filter(payload__tender_id=t.id, code='T08').first()
                if t08 and t08.status != 'COMPLETED':
                    t08.status = 'COMPLETED'
                    t08.save()
                
                # Trigger the new T09 Master Task
                trigger_ai_tasks(t)
                fixed_count += 1
        
        log_action(emp, 'REPAIR_CHAIN', f'Repaired {fixed_count} tenders missing T09 tasks.', 'System', '', getattr(request, 'company', None))
        
        if fixed_count > 0:
            messages.success(request, f"Successfully repaired {fixed_count} tender(s) and generated the missing AI tasks!")
        else:
            messages.info(request, "System check complete. No missing T09 tasks found for active tenders.")
            
        return redirect('it_dashboard')

    return redirect('it_dashboard')
