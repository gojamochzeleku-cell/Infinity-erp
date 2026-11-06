import os
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.conf import settings
from .models import ITTicket, Employee, Task
from .chain import create_task

@login_required
def it_dashboard(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company or emp.role not in ('IT', 'ADMIN'):
        return redirect('dashboard')

    tickets = ITTicket.objects.filter(company=company).order_by('-created_at')
    
    # Check System Health
    gemini_key = os.environ.get('GEMINI_API_KEY', '') or getattr(settings, 'GEMINI_API_KEY', '')
    telegram_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    
    health = {
        'gemini': 'OK' if gemini_key and gemini_key != 'PASTE_YOUR_GEMINI_API_KEY_HERE' else 'MISSING',
        'telegram': 'OK' if telegram_token else 'MISSING',
        'db': 'OK'
    }

    return render(request, 'it_dashboard.html', {'tickets': tickets, 'health': health})

@login_required
def it_user_management(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or emp.role not in ('IT', 'ADMIN'):
        return redirect('dashboard')
        
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        password = request.POST.get('password', '')
        role = request.POST.get('role', 'STAFF')
        phone = request.POST.get('phone', '')
        telegram_id = request.POST.get('telegram_id', '')
        
        if User.objects.filter(username=username).exists():
            messages.error(request, f'Username {username} already exists!')
        else:
            user = User.objects.create_user(username=username, email=email, password=password, first_name=first_name, last_name=last_name)
            Employee.objects.create(
                user=user, company=company, role=role, phone=phone,
                telegram_id=telegram_id, is_group_level=True, is_active=True
            )
            messages.success(request, f'User {username} created successfully!')
            return redirect('it_user_management')

    users = Employee.objects.all().order_by('user__username')
    from core.models import ROLE_CHOICES
    return render(request, 'it_users.html', {'users': users, 'roles': ROLE_CHOICES})

@login_required
def it_delete_user(request, pk):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role not in ('IT', 'ADMIN'):
        return redirect('dashboard')
        
    target_emp = get_object_or_404(Employee, pk=pk)
    username = target_emp.user.username
    
    # Prevent deleting yourself
    if target_emp.user == request.user:
        messages.error(request, "You cannot delete your own account!")
        return redirect('it_user_management')
        
    # Delete the User (cascades to Employee)
    target_emp.user.delete()
    messages.success(request, f'User {username} has been permanently deleted.')
    return redirect('it_user_management')

@login_required
def it_reset_password(request, pk):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role not in ('IT', 'ADMIN'):
        return redirect('dashboard')
        
    target_emp = get_object_or_404(Employee, pk=pk)
    if request.method == 'POST':
        new_password = request.POST.get('password')
        if new_password:
            target_emp.user.set_password(new_password)
            target_emp.user.save()
            messages.success(request, f'Password for {target_emp.full_name} reset successfully.')
        return redirect('it_user_management')

    return render(request, 'it_reset_password.html', {'target_emp': target_emp})

@login_required
def it_toggle_user(request, pk):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role not in ('IT', 'ADMIN'):
        return redirect('dashboard')
        
    target_emp = get_object_or_404(Employee, pk=pk)
    target_emp.is_active = not target_emp.is_active
    target_emp.user.is_active = target_emp.is_active
    target_emp.save()
    target_emp.user.save()
    
    status = "Activated" if target_emp.is_active else "Deactivated"
    messages.success(request, f'{target_emp.full_name} has been {status}.')
    return redirect('it_user_management')

@login_required
def it_submit_ticket(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company: return redirect('dashboard')

    if request.method == 'POST':
        subject = request.POST.get('subject')
        desc = request.POST.get('description')
        
        ticket = ITTicket.objects.create(company=company, employee=emp, subject=subject, description=desc)
        
        # Create task for IT
        create_task(company, 'IT_TICKET', 'IT', f'IT Support: {subject}', f'From: {emp.full_name}\n\n{desc}', {'ticket_id': ticket.id}, '')
        
        messages.success(request, 'IT Ticket submitted successfully. The IT Coordinator has been notified.')
        return redirect('dashboard')

    return render(request, 'it_ticket_form.html')

@login_required
def it_resolve_ticket(request, pk):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role not in ('IT', 'ADMIN'):
        return redirect('dashboard')
        
    ticket = get_object_or_404(ITTicket, pk=pk)
    ticket.status = 'RESOLVED'
    ticket.save()
    return redirect('it_dashboard')


@login_required
def it_edit_user(request, pk):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role not in ('IT', 'ADMIN'):
        return redirect('dashboard')
        
    target_emp = get_object_or_404(Employee, pk=pk)
    
    if request.method == 'POST':
        # Update User model fields
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        
        target_emp.user.first_name = first_name
        target_emp.user.last_name = last_name
        target_emp.user.email = email
        target_emp.user.save()
        
        # Update Employee model fields
        target_emp.role = request.POST.get('role', 'STAFF')
        target_emp.phone = request.POST.get('phone', '')
        target_emp.telegram_id = request.POST.get('telegram_id', '')
        target_emp.save()
        
        messages.success(request, f'User {target_emp.user.username} updated successfully!')
        return redirect('it_user_management')

    from core.models import ROLE_CHOICES
    return render(request, 'it_edit_user.html', {'target_emp': target_emp, 'roles': ROLE_CHOICES})
