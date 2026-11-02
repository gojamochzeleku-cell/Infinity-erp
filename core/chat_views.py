from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from .models import ChatRoom, ChatMessage, Employee

@login_required
def chat_dashboard(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company: return redirect('select_company')

    # Get rooms the employee is part of
    rooms = ChatRoom.objects.filter(company=company, participants=emp).order_by('-created_at')
    
    # Get all other employees for starting private chats
    others = Employee.objects.filter(is_active=True).exclude(id=emp.id)
    
    if request.method == 'POST':
        # Create a new private or group chat
        name = request.POST.get('name')
        is_group = request.POST.get('is_group') == 'on'
        participant_ids = request.POST.getlist('participants')
        
        room = ChatRoom.objects.create(company=company, name=name, is_group=is_group)
        room.participants.add(emp)
        for pid in participant_ids:
            p = Employee.objects.filter(id=pid).first()
            if p: room.participants.add(p)
            
        return redirect('chat_room', pk=room.id)

    return render(request, 'chat_dashboard.html', {'rooms': rooms, 'others': others})

@login_required
def chat_room(request, pk):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    room = get_object_or_404(ChatRoom, pk=pk, company=company)
    
    if emp not in room.participants.all():
        return redirect('chat_dashboard')

    return render(request, 'chat_room.html', {'room': room})

@login_required
def send_message(request, pk):
    if request.method != 'POST': return JsonResponse({'error': 'POST required'}, status=400)
    
    emp = getattr(request.user, 'employee', None)
    room = get_object_or_404(ChatRoom, pk=pk)
    
    text = request.POST.get('text', '').strip()
    audio = request.FILES.get('audio')
    
    if text or audio:
        msg = ChatMessage.objects.create(room=room, sender=emp, text=text, audio_file=audio)
        return JsonResponse({'status': 'success', 'message_id': msg.id})
    return JsonResponse({'error': 'Empty message'}, status=400)

@login_required
def get_messages(request, pk):
    # Polling API to get new messages
    last_id = int(request.GET.get('last_id', 0))
    messages = ChatMessage.objects.filter(room_id=pk, id__gt=last_id).select_related('sender')
    
    data = []
    for m in messages:
        data.append({
            'id': m.id,
            'sender': m.sender.full_name,
            'sender_id': m.sender.id,
            'text': m.text,
            'audio_url': m.audio_file.url if m.audio_file else None,
            'time': timezone.localtime(m.created_at).strftime('%I:%M %p')
        })
    return JsonResponse({'messages': data})
