import random
from datetime import timedelta
from django.shortcuts import render, redirect
from django.utils import timezone
from django.contrib.auth.models import User
from django.db.models import Q
from .models import Employee, PasswordResetCode
from .services import send_telegram_message

def forgot_password(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        
        # Find user by username or email
        user = User.objects.filter(Q(username__iexact=username) | Q(email__iexact=username)).first()
        
        if user and hasattr(user, 'employee'):
            emp = user.employee
            # Check if they have a Telegram ID to receive the message
            chat_id = emp.telegram_chat_id or emp.telegram_id
            
            if chat_id:
                # Generate 6-digit code
                code = str(random.randint(100000, 999999))
                PasswordResetCode.objects.create(employee=emp, code=code)
                
                # Send via Telegram
                msg = f"🔐 *Infinity ERP Password Reset*\n\nYour verification code is: *{code}*\n\nThis code expires in 10 minutes."
                send_telegram_message(chat_id, msg)
                
                # Store username in session to verify in the next step
                request.session['reset_username'] = user.username
                return redirect('verify_reset_code')
            else:
                return render(request, 'forgot_password.html', {'error': 'Your account does not have a Telegram Chat ID linked. Please contact the IT Coordinator.'})
        else:
            # Security best practice: don't reveal if user exists or not
            return render(request, 'forgot_password.html', {'error': 'If this account exists, a code has been sent to Telegram.'})

    return render(request, 'forgot_password.html')

def verify_reset_code(request):
    username = request.session.get('reset_username')
    if not username:
        return redirect('forgot_password')

    if request.method == 'POST':
        code = request.POST.get('code', '').strip()
        new_password = request.POST.get('new_password', '')
        
        user = User.objects.filter(username=username).first()
        if not user:
            return redirect('forgot_password')
            
        emp = user.employee
        
        # Find the latest unexpired code
        ten_minutes_ago = timezone.now() - timedelta(minutes=10)
        reset_entry = PasswordResetCode.objects.filter(
            employee=emp, code=code, created_at__gte=ten_minutes_ago
        ).order_by('-created_at').first()
        
        if reset_entry:
            if len(new_password) < 6:
                return render(request, 'verify_reset_code.html', {'error': 'Password must be at least 6 characters long.'})
                
            user.set_password(new_password)
            user.save()
            
            # Delete all reset codes for this user
            PasswordResetCode.objects.filter(employee=emp).delete()
            
            # Clear session
            del request.session['reset_username']
            
            return render(request, 'forgot_password.html', {'success': 'Password reset successfully! You can now log in.'})
        else:
            return render(request, 'verify_reset_code.html', {'error': 'Invalid or expired code. Please try again.'})

    return render(request, 'verify_reset_code.html')
