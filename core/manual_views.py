from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

@login_required
def role_manual(request):
    emp = getattr(request.user, 'employee', None)
    if not emp:
        return redirect('dashboard')
    return render(request, 'role_manual.html', {'role': emp.role, 'role_name': emp.get_role_display()})
