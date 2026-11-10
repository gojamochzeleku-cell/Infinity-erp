from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


# Legacy system-wide SaaS administration is retired.
# Company administration is handled through company memberships.

@login_required
def saas_dashboard(request):
    return redirect('dashboard')


@login_required
def saas_add_company(request):
    return redirect('dashboard')


@login_required
def saas_delete_company(request, pk):
    return redirect('dashboard')


@login_required
def saas_manage_employees(request):
    return redirect('dashboard')


@login_required
def saas_clear_test_data(request):
    return redirect('dashboard')
