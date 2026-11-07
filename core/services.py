import os
import requests
from django.conf import settings
from .models import Notification, AuditLog

def send_telegram_message(chat_id, text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={'chat_id': chat_id, 'text': text}, timeout=5)
        return True
    except Exception:
        return False

def notify(employee, title, body='', task=None, link=''):
    if employee is None:
        return None
        
    # 1. Create the in-app notification (Bell)
    notif = Notification.objects.create(
        employee=employee, title=title, body=body, task=task, link=link)
        
    # 2. Send to Telegram if they have a Chat ID
    if employee.telegram_chat_id:
        text = f"🔔 *{title}*\n\n{body}"
        if link:
            text += f"\n\nOpen in ERP: http://127.0.0.1:8000{link}"
        sent = send_telegram_message(employee.telegram_chat_id, text)
        if sent:
            notif.sent_telegram = True
            notif.save()
            
    # 3. Send Email (Placeholder for future)
    # send_email(employee.user.email, title, body)
            
    return notif

def log_action(actor, action, detail='', object_type='', object_id='', company=None):
    return AuditLog.objects.create(
        actor=actor, action=action, detail=detail,
        object_type=object_type, object_id=str(object_id or ''), company=company)


from .models import TaskHistory

def log_task_history(task, action, note, completed_by):
    TaskHistory.objects.create(
        company=task.company,
        task_code=task.code,
        task_title=task.title,
        assigned_to=task.assigned_to,
        completed_by=completed_by,
        action_taken=action,
        note=note
    )
