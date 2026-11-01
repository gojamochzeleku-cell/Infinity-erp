from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from datetime import date, timedelta
from django.db.models import Q
from .models import Task, LegalCase, Correspondence, Document, Tender

@login_required
def deadline_center(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('select_company')
    
    today = date.today()
    next_week = today + timedelta(days=7)
    
    deadlines = []

    # 1. Tasks
    tasks = Task.objects.filter(company=company, due_date__isnull=False).exclude(status='COMPLETED')
    for t in tasks:
        status = 'OVERDUE' if t.due_date < today else ('TODAY' if t.due_date == today else 'UPCOMING')
        deadlines.append({
            'title': t.title, 'type': 'Task', 'date': t.due_date, 
            'status': status, 'link': f"/task/{t.id}/", 'owner': t.assigned_to.full_name
        })

    # 2. Legal Cases
    cases = LegalCase.objects.filter(company=company, next_hearing_date__isnull=False, status='ACTIVE')
    for c in cases:
        status = 'OVERDUE' if c.next_hearing_date < today else ('TODAY' if c.next_hearing_date == today else 'UPCOMING')
        deadlines.append({
            'title': c.title, 'type': 'Court Hearing', 'date': c.next_hearing_date, 
            'status': status, 'link': "/legal/", 'owner': c.assigned_lawyer.full_name if c.assigned_lawyer else 'Unassigned'
        })

    # 3. Mail (Outgoing reply deadlines)
    letters = Correspondence.objects.filter(company=company, reply_deadline__isnull=False).exclude(status='REPLIED') if hasattr(Correspondence, 'status') else Correspondence.objects.filter(company=company, reply_deadline__isnull=False)
    for l in letters:
        status = 'OVERDUE' if l.reply_deadline < today else ('TODAY' if l.reply_deadline == today else 'UPCOMING')
        deadlines.append({
            'title': f"Reply needed: {l.subject}", 'type': 'Mail', 'date': l.reply_deadline, 
            'status': status, 'link': "/mail/", 'owner': l.assigned_to.full_name if l.assigned_to else 'Office'
        })

    # 4. Documents (Expiries)
    docs = Document.objects.filter(company=company, expiry_date__isnull=False, is_archived=False)
    for d in docs:
        status = 'OVERDUE' if d.expiry_date < today else ('TODAY' if d.expiry_date == today else 'UPCOMING')
        deadlines.append({
            'title': f"{d.title} (Expires)", 'type': 'Document', 'date': d.expiry_date, 
            'status': status, 'link': "/documents/", 'owner': d.uploaded_by.full_name if d.uploaded_by else 'System'
        })

    # 5. Tenders (CPOs / Bonds)
    tenders = Tender.objects.filter(company=company).exclude(status__in=['WON', 'LOST', 'COMPLETED'])
    for t in tenders:
        if t.cpo_expiry:
            status = 'OVERDUE' if t.cpo_expiry < today else ('TODAY' if t.cpo_expiry == today else 'UPCOMING')
            deadlines.append({
                'title': f"CPO Expiry: {t.bid_title[:20]}...", 'type': 'CPO / Bond', 'date': t.cpo_expiry, 
                'status': status, 'link': f"/tenders/{t.id}/", 'owner': 'Finance Manager'
            })

    # Sort: Overdue first, then by date
    deadlines.sort(key=lambda x: (x['date'], x['status'] != 'OVERDUE'))

    # Filter for this week + overdue
    relevant_deadlines = [d for d in deadlines if d['date'] <= next_week]

    return render(request, 'deadline_center.html', {'deadlines': relevant_deadlines, 'today': today})
