import hashlib
import hmac
import json
import os
import time
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

        parsed_pairs = parse_qsl(init_data, keep_blank_values=True)
        if len(dict(parsed_pairs)) != len(parsed_pairs):
            return JsonResponse({'error': 'Duplicate authentication fields'}, status=400)
        parsed_data = dict(parsed_pairs)
        received_hash = parsed_data.pop('hash', None)
        
        if not received_hash:
            return JsonResponse({'error': 'Hash missing'}, status=400)

        # Create data_check_string
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        
        # Calculate secret key
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        # Calculate expected hash
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return JsonResponse({'error': 'Invalid authentication data'}, status=403)

        # 3. Hash is valid. Get the user info.
        auth_date = int(parsed_data.get('auth_date', '0'))
        current_time = int(time.time())
        if auth_date <= 0 or auth_date > current_time + 60 or current_time - auth_date > 86400:
            return JsonResponse({'error': 'Authentication data expired or invalid'}, status=403)

        user_data = json.loads(parsed_data.get('user', '{}'))
        telegram_user_id = user_data.get('id')
        if not telegram_user_id:
            return JsonResponse({'error': 'Telegram user ID missing'}, status=400)
        tg_id = str(telegram_user_id)
        
        # 4. Find the Employee in our database
        emp = Employee.objects.filter(telegram_id=tg_id).first()
        
        if not emp or not emp.is_active or not emp.user.is_active:
            return JsonResponse({'error': 'No active ERP employee profile is linked to this Telegram account.'}, status=404)

        # 5. Log them in!
        login(request, emp.user)
        # Never choose a company automatically. Require validated membership selection.
        request.session.pop('company_id', None)
        
        return JsonResponse({'status': 'success', 'redirect': '/select-company/'})

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


import requests
from django.http import HttpResponse
from django.conf import settings

@csrf_exempt
def telegram_webhook(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            message = data.get('message', {})
            chat_id = message.get('chat', {}).get('id')
            text = message.get('text', '')
            
            bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
            if not bot_token:
                return HttpResponse("Bot token missing", status=500)

            # If the user types /start
            if text.startswith('/start'):
                # Determine the Render URL
                base_url = os.environ.get('RENDER_EXTERNAL_URL', 'https://infinity-erp.onrender.com')
                
                reply_text = "Welcome to *Infinity Investment Institute*! 🏢\n\nManage your tenders, track tasks, and approve requests directly from here."
                
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "🚀 Open Infinity ERP",
                                "web_app": {"url": base_url}
                            }
                        ]
                    ]
                }
                
                # Send the message back to the user
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                payload = {
                    "chat_id": chat_id,
                    "text": reply_text,
                    "parse_mode": "Markdown",
                    "reply_markup": reply_markup
                }
                requests.post(url, json=payload)

            return HttpResponse("OK")
        except Exception as e:
            return HttpResponse(f"Error: {e}", status=500)
    return HttpResponse("Webhook is active", status=200)
