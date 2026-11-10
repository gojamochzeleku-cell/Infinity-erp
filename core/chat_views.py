from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from .models import ChatRoom, ChatMessage, Employee


def _chat_context(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or not emp
        or not emp.is_active
        or not company
        or not membership
        or not membership.is_active
        or membership.employee_id != emp.id
        or membership.company_id != company.id
    ):
        return None, None, set()

    roles = set(membership.roles.values_list('role', flat=True))
    return emp, company, roles


def _room_for_member(pk, company, emp):
    return get_object_or_404(
        ChatRoom.objects.filter(
            pk=pk,
            company=company,
            participants=emp,
        ).distinct()
    )


@login_required
def chat_dashboard(request):
    emp, company, roles = _chat_context(request)
    if not company:
        return redirect('select_company')

    rooms = ChatRoom.objects.filter(
        company=company,
        participants=emp,
    ).order_by('-created_at')

    others = Employee.objects.filter(
        is_active=True,
        company_memberships__company=company,
        company_memberships__is_active=True,
    ).exclude(id=emp.id).distinct()

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        is_group = request.POST.get('is_group') == 'on'
        participant_ids = request.POST.getlist('participants')

        if not name:
            return redirect('chat_dashboard')

        valid_participants = list(
            others.filter(id__in=participant_ids)
        )

        # Reject the entire request if any submitted participant is not
        # an active member of the selected company.
        if len({str(p.id) for p in valid_participants}) != len(set(participant_ids)):
            return JsonResponse(
                {'error': 'One or more participants are not active members of this company.'},
                status=400,
            )

        room = ChatRoom.objects.create(
            company=company,
            name=name,
            is_group=is_group,
        )
        room.participants.add(emp, *valid_participants)
        return redirect('chat_room', pk=room.id)

    return render(request, 'chat_dashboard.html', {
        'rooms': rooms,
        'others': others,
    })


@login_required
def chat_room(request, pk):
    emp, company, roles = _chat_context(request)
    if not company:
        return redirect('select_company')

    room = _room_for_member(pk, company, emp)
    return render(request, 'chat_room.html', {'room': room})


@login_required
def send_message(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    emp, company, roles = _chat_context(request)
    if not company:
        return JsonResponse({'error': 'Select an active company.'}, status=403)

    room = _room_for_member(pk, company, emp)

    text = request.POST.get('text', '').strip()
    audio = request.FILES.get('audio')

    if not text and not audio:
        return JsonResponse({'error': 'Empty message'}, status=400)

    msg = ChatMessage.objects.create(
        room=room,
        sender=emp,
        text=text,
        audio_file=audio,
    )
    return JsonResponse({'status': 'success', 'message_id': msg.id})


@login_required
def get_messages(request, pk):
    emp, company, roles = _chat_context(request)
    if not company:
        return JsonResponse({'error': 'Select an active company.'}, status=403)

    room = _room_for_member(pk, company, emp)

    try:
        last_id = max(0, int(request.GET.get('last_id', 0)))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid last_id'}, status=400)

    messages = ChatMessage.objects.filter(
        room=room,
        id__gt=last_id,
    ).select_related('sender')

    data = []
    for m in messages:
        data.append({
            'id': m.id,
            'sender': m.sender.full_name,
            'sender_id': m.sender.id,
            'text': m.text,
            'audio_url': m.audio_file.url if m.audio_file else None,
            'time': timezone.localtime(m.created_at).strftime('%I:%M %p'),
        })

    return JsonResponse({'messages': data})


@login_required
def delete_message(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    emp, company, roles = _chat_context(request)
    if not company:
        return JsonResponse({'error': 'Select an active company.'}, status=403)

    msg = get_object_or_404(
        ChatMessage.objects.select_related('room'),
        pk=pk,
        room__company=company,
        room__participants=emp,
    )

    if msg.sender_id == emp.id or roles.intersection({'ADMIN', 'CEO', 'GM'}):
        msg.delete()
        return JsonResponse({'status': 'success'})

    return JsonResponse({'error': 'Unauthorized'}, status=403)
