import csv
from datetime import date, timedelta
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.db.models import Q
from .models import Tender, CashFlowEntry, InventoryItem, Task, LegalCase, LeaveRequest, AuditLog, Employee

# --- 1. CSV EXPORTS ---
@login_required
def export_tenders_csv(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('dashboard')
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="tenders_{company.short_name}_{date.today()}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Serial Number', 'Bid Title', 'Purchaser', 'Status', 'Final Price (ETB)', 'Submission Date', 'Loss Reason'])
    
    tenders = Tender.objects.filter(company=company).order_by('-created_at')
    for t in tenders:
        writer.writerow([t.serial_number, t.bid_title, t.purchaser_name, t.get_status_display(), t.final_price, t.bid_submission_date, t.loss_reason])
    return response

@login_required
def export_cashflow_csv(request):
    company = getattr(request, 'company', None)
    if not company or request.user.employee.role not in ('FIN', 'CEO', 'ADMIN'):
        return redirect('dashboard')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="cashflow_{company.short_name}_{date.today()}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Date', 'Type', 'Category', 'Amount (ETB)', 'Description', 'Linked Tender'])
    
    entries = CashFlowEntry.objects.filter(company=company).order_by('-date')
    for e in entries:
        writer.writerow([e.date, e.flow_type, e.get_category_display(), e.amount, e.description, e.related_tender.serial_number if e.related_tender else ''])
    return response

# --- 2. CORPORATE CALENDAR ---
@login_required
def corporate_calendar(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('select_company')
    
    today = date.today()
    end_date = today + timedelta(days=30)
    
    events = []

    # Tender Submissions & Openings
    for t in Tender.objects.filter(company=company).exclude(status__in=['WON', 'LOST', 'COMPLETED']):
        if t.bid_submission_date and today <= t.bid_submission_date <= end_date:
            events.append({'date': t.bid_submission_date, 'title': f"BID SUBMISSION: {t.serial_number}", 'type': 'TENDER'})
        if t.bid_opening_date and today <= t.bid_opening_date <= end_date:
            events.append({'date': t.bid_opening_date, 'title': f"BID OPENING: {t.serial_number}", 'type': 'TENDER'})

    # Court Hearings
    for c in LegalCase.objects.filter(company=company, status='ACTIVE', next_hearing_date__range=[today, end_date]):
        events.append({'date': c.next_hearing_date, 'title': f"COURT HEARING: {c.title}", 'type': 'LEGAL'})

    # Task Deadlines
    for t in Task.objects.filter(company=company, due_date__range=[today, end_date]).exclude(status='COMPLETED'):
        events.append({'date': t.due_date, 'title': f"TASK DUE: {t.title}", 'type': 'TASK'})

    # Employee Leaves
    for l in LeaveRequest.objects.filter(company=company, status='APPROVED', start_date__range=[today, end_date]):
        events.append({'date': l.start_date, 'title': f"LEAVE STARTS: {l.employee.full_name}", 'type': 'HR'})

    # Sort by date
    events.sort(key=lambda x: x['date'])
    
    # Group by date
    grouped_events = {}
    for e in events:
        if e['date'] not in grouped_events:
            grouped_events[e['date']] = []
        grouped_events[e['date']].append(e)

    return render(request, 'corporate_calendar.html', {'grouped_events': grouped_events, 'today': today})

# --- 3. AUDIT TRAIL EXPLORER ---
@login_required
def audit_trail(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    
    if not emp or not company or emp.role not in ('ADMIN', 'CEO', 'GM', 'IT'):
        return redirect('dashboard')

    query = request.GET.get('q', '')
    logs = AuditLog.objects.filter(company=company).order_by('-created_at')

    if query:
        logs = logs.filter(Q(action__icontains=query) | Q(detail__icontains=query) | Q(actor__user__username__icontains=query))

    return render(request, 'audit_trail.html', {'logs': logs[:200], 'query': query})
