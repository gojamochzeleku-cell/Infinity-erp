import hashlib
import hmac
import json
import os
from urllib.parse import parse_qsl
from django.contrib.auth import login
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import redirect
from .models import Employee

@csrf_exempt
def telegram_auth(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        # 1. Get the initData sent from the Telegram WebApp (Frontend)
        data = json.loads(request.body)
        init_data = data.get('initData', '')
        
        if not init_data:
            return JsonResponse({'error': 'No initData provided'}, status=400)

        # 2. Validate the hash (Security)
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        if not bot_token:
            return JsonResponse({'error': 'Bot token not configured on server'}, status=500)

        parsed_data = dict(parse_qsl(init_data))
        received_hash = parsed_data.pop('hash', None)
        
        if not received_hash:
            return JsonResponse({'error': 'Hash missing'}, status=400)

        # Create data_check_string
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        
        # Calculate secret key
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        # Calculate expected hash
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if calculated_hash != received_hash:
            return JsonResponse({'error': 'Invalid authentication data'}, status=403)

        # 3. Hash is valid. Get the user info.
        user_data = json.loads(parsed_data.get('user', '{}'))
        tg_id = str(user_data.get('id'))
        
        # 4. Find the Employee in our database
        emp = Employee.objects.filter(telegram_id=tg_id).first()
        
        if not emp:
            return JsonResponse({'error': 'Your Telegram account is not linked to any ERP employee profile.'}, status=404)

        # 5. Log them in!
        login(request, emp.user)
        request.session['company_id'] = 1 # Default to first company, or let them choose
        
        return JsonResponse({'status': 'success', 'redirect': '/select-company/'})

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
