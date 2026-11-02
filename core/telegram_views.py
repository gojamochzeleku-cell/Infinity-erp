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
