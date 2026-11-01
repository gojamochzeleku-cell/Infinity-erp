from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

@login_required
def tv_settings(request):
    emp = getattr(request.user, 'employee', None)
    if not emp or emp.role not in ('ADMIN', 'CEO', 'GM', 'IT'):
        return redirect('dashboard')
        
    # We will store the speed in the session for simplicity
    if request.method == 'POST':
        speed = request.POST.get('speed', '20')
        request.session['tv_speed'] = speed
        return redirect('tv_settings')
        
    current_speed = request.session.get('tv_speed', '20')
    return render(request, 'tv_settings.html', {'speed': current_speed})
