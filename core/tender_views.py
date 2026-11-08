import threading
from django.db import connection
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from .gemini import extract_tender_from_pdf
from .models import Tender
from .services import log_action
from .chain import create_task
from dateutil import parser as date_parser

def _emp(request):
    return getattr(request.user, 'employee', None)

def _co(request):
    return getattr(request, 'company', None)

@login_required
def tender_list(request):
    from django.db.models import Q
    emp, company = _emp(request), _co(request)
    if emp is None or company is None:
        return redirect('select_company')
        
    query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '')
    org_filter = request.GET.get('org_type', '')

    tenders = Tender.objects.filter(company=company).order_by('-created_at')

    if query:
        tenders = tenders.filter(
            Q(bid_title__icontains=query) | 
            Q(bid_reference_number__icontains=query) | 
            Q(purchaser_name__icontains=query)
        )
    if status_filter:
        tenders = tenders.filter(status=status_filter)
    if org_filter:
        tenders = tenders.filter(organization_type=org_filter)

    return render(request, 'tender_list.html', {
        'tenders': tenders, 
        'query': query,
        'status_filter': status_filter,
        'org_filter': org_filter,
        'status_choices': Tender.STATUS_CHOICES,
        'org_choices': Tender.ORG_TYPE_CHOICES
    })


def run_ai_extraction(tender_id):
    try:
        tender = Tender.objects.get(id=tender_id)
        result = extract_tender_from_pdf(tender.bid_information_sheet)
        
        if 'error' in result:
            tender.ai_status = 'FAILED'
            tender.ai_error = result['error']
        else:
            _apply(tender, result['data'])
            tender.ai_extracted = True
            tender.ai_status = 'DONE'
        tender.save()
    except Exception as e:
        tender = Tender.objects.get(id=tender_id)
        tender.ai_status = 'FAILED'
        tender.ai_error = str(e)
        tender.save()
    finally:
        connection.close() # Crucial for background threads in Django

@login_required
def tender_register(request):
    emp, company = _emp(request), _co(request)
    if emp is None or company is None:
        return redirect('select_company')
    initial, errors = {}, []

    if request.method == 'POST':
        action = request.POST.get('action', 'save_draft')
        form_data = _collect(request)
        pdf_file = request.FILES.get('bid_information_sheet')

        if action == 'extract':
            if pdf_file is None:
                errors.append('Choose the bid information sheet PDF first.')
            else:
                temp = Tender(company=company, registered_by=emp, status='DRAFT', **{k: v for k, v in form_data.items() if v not in (None, '')})
                temp.bid_information_sheet = pdf_file
                temp.save()
                
                # Run synchronously so the user waits for it to finish
                result = extract_tender_from_pdf(pdf_file)
                
                if 'error' in result:
                    messages.error(request, 'AI failed: %s. Fill the form manually.' % result['error'])
                else:
                    _apply(temp, result['data'])
                    temp.ai_extracted = True
                    messages.success(request, 'AI extracted the fields. Review and submit.')
                
                return redirect('tender_edit', pk=temp.id)

        elif action in ('save_draft', 'submit'):
            form_data['status'] = 'REGISTERED' if action == 'submit' else 'DRAFT'
            form_data['ai_status'] = 'PENDING'
            try:
                tender = Tender(company=company, registered_by=emp, **form_data)
                if pdf_file:
                    tender.bid_information_sheet = pdf_file
                tender.save()
                log_action(emp, 'TENDER_' + action.upper(), tender.bid_title, 'Tender', tender.id, company)
                if action == 'submit':
                    _to_bid_manager(request, tender)
                    messages.success(request, 'Tender registered and sent to Bid Manager.')
                else:
                    messages.success(request, 'Tender saved as draft.')
                return redirect('tender_detail', pk=tender.id)
            except Exception as e:
                errors.append('Could not save: %s' % e)
                initial = form_data

    return render(request, 'tender_form.html', {'initial': initial, 'errors': errors, 'is_new': True})

@login_required
def tender_edit(request, pk):
    emp, company = _emp(request), _co(request)
    if emp is None or company is None:
        return redirect('select_company')
    tender = get_object_or_404(Tender, pk=pk, company=company)
    errors = []

    if request.method == 'POST':
        action = request.POST.get('action', 'save_draft')
        pdf_file = request.FILES.get('bid_information_sheet')
        
        if action == 'extract':
            if pdf_file:
                tender.bid_information_sheet = pdf_file
            if not tender.bid_information_sheet:
                messages.error(request, 'Upload a PDF first.')
                return redirect('tender_edit', pk=tender.id)
            
            tender.ai_status = 'PROCESSING'
            tender.ai_error = ''
            tender.save()
            
            # Start background thread
            threading.Thread(target=run_ai_extraction, args=(tender.id,)).start()
            
            messages.info(request, 'AI is analyzing the document. Please wait...')
            return redirect('tender_edit', pk=tender.id)

        form_data = _collect(request)
        for field, value in form_data.items():
            setattr(tender, field, value)
        if pdf_file:
            tender.bid_information_sheet = pdf_file
        if action == 'submit' and tender.status == 'DRAFT':
            tender.status = 'REGISTERED'
            tender.save()
            _to_bid_manager(request, tender)
            log_action(emp, 'TENDER_REGISTERED', tender.bid_title, 'Tender', tender.id, company)
            messages.success(request, 'Sent to Bid Manager.')
            return redirect('tender_detail', pk=tender.id)
        tender.save()
        messages.success(request, 'Tender updated.')
        return redirect('tender_detail', pk=tender.id)

    return render(request, 'tender_form.html', {'tender': tender, 'initial': _initial(tender), 'errors': errors, 'is_new': False})

@login_required
def tender_detail(request, pk):
    emp, company = _emp(request), _co(request)
    if emp is None or company is None:
        return redirect('select_company')
    tender = get_object_or_404(Tender, pk=pk, company=company)
    return render(request, 'tender_detail.html', {'tender': tender})

@login_required
def tender_submit(request, pk):
    emp, company = _emp(request), _co(request)
    if emp is None or company is None:
        return redirect('select_company')
    tender = get_object_or_404(Tender, pk=pk, company=company)
    if request.method == 'POST' and tender.status == 'DRAFT':
        tender.status = 'REGISTERED'
        tender.save()
        _to_bid_manager(request, tender)
        log_action(emp, 'TENDER_REGISTERED', tender.bid_title, 'Tender', tender.id, company)
        messages.success(request, 'Sent to Bid Manager.')
    return redirect('tender_detail', pk=pk)

def _to_bid_manager(request, tender):
    task = create_task(
        company=tender.company, code='T02', role='BIDM',
        title='Feasibility review: %s' % tender.bid_title,
        description='Review feasibility and press GO or NO-GO.',
        payload={'tender_id': tender.id, 'serial': tender.serial_number, 'reference': tender.bid_reference_number},
        action_url=reverse('tender_detail', args=[tender.id]),
    )
    if task is None:
        messages.warning(request, 'No Bid Manager in this company. Add one in /admin/.')

def _safe_date(val):
    if not val:
        return None
    try:
        return date_parser.parse(val).date()
    except Exception:
        return None

def _collect(request):
    def v(n):
        x = request.POST.get(n, '').strip()
        return x or None

    def num(n):
        x = request.POST.get(n, '').strip().replace(',', '').replace('%', '')
        return x if x else 0

    return {
        'bid_reference_number': v('bid_reference_number') or '',
        'bid_title': v('bid_title') or '',
        'bid_type': request.POST.get('bid_type') or 'DOCUMENT',
        'purchaser_name': v('purchaser_name') or '',
        'organization_type': request.POST.get('organization_type') or 'GOVERNMENT',
        'published_date': _safe_date(v('published_date')),
        'bidder_location': v('bidder_location') or '',
        'contact_phone': v('contact_phone') or '',
        'bid_document_cost_amount': num('bid_document_cost_amount'),
        'bid_document_cost_payment_mode': request.POST.get('bid_document_cost_payment_mode') or 'CASH',
        'account_holder_name': v('account_holder_name') or '',
        'bank_name': v('bank_name') or '',
        'bank_account_number': v('bank_account_number') or '',
        'cpo_amount': num('cpo_amount'),
        'cpo_type': request.POST.get('cpo_type') or 'AMOUNT',
        'bid_submission_date': _safe_date(v('bid_submission_date')),
        'bid_opening_date': _safe_date(v('bid_opening_date')),
    }

def _apply(tender, data):
    """Applies the AI extracted data to the Tender and creates Lots."""
    
    # 1. Main Tender Fields
    if data.get('bid_reference_number'):
        tender.bid_reference_number = str(data['bid_reference_number'])[:150]
    if data.get('bid_title'):
        tender.bid_title = str(data['bid_title'])[:300]
    if data.get('purchaser_name'):
        tender.purchaser_name = str(data['purchaser_name'])[:250]
        
    for field, allowed in (
        ('bid_type', ['EGP', 'DOCUMENT']),
        ('organization_type', ['GOVERNMENT', 'NON_GOVERNMENT', 'PRIVATE']),
        ('bid_document_cost_payment_mode', ['CASH', 'DEPOSIT']),
    ):
        val = data.get(field)
        if val:
            val = str(val).strip().upper().replace(' ', '_')
            if val in allowed:
                setattr(tender, field, val)

    for field in ('bidder_location', 'contact_phone', 'account_holder_name', 'bank_name', 'bank_account_number'):
        if data.get(field):
            setattr(tender, field, str(data[field])[:200])
            
    for field in ('published_date', 'bid_submission_date', 'bid_opening_date'):
        if data.get(field):
            setattr(tender, field, _safe_date(str(data[field])))
            
    for field in ('bid_document_cost_amount', 'cpo_amount'):
        if data.get(field) not in (None, ''):
            try:
                setattr(tender, field, str(data[field]).replace(',', ''))
            except Exception:
                pass

    # 2. Lots Array
    if 'lots' in data and isinstance(data['lots'], list) and len(data['lots']) > 0:
        tender.lots.all().delete()
        from .models import TenderLot
        
        for lot_data in data['lots']:
            lot_name = lot_data.get('lot_name') or 'General'
            desc = lot_data.get('description') or ''
            cpo = lot_data.get('cpo_amount') or 0
            cpo_type = lot_data.get('cpo_type') or 'AMOUNT'
            sub_date = _safe_date(lot_data.get('submission_date'))
            open_date = _safe_date(lot_data.get('opening_date'))
            
            TenderLot.objects.create(
                tender=tender, 
                lot_name=str(lot_name)[:100], 
                description=str(desc)[:300], 
                cpo_amount=cpo,
                cpo_type=cpo_type,
                submission_date=sub_date, 
                opening_date=open_date
            )

def _initial(t):
    return {
        'bid_reference_number': t.bid_reference_number,
        'bid_title': t.bid_title,
        'bid_type': t.bid_type,
        'purchaser_name': t.purchaser_name,
        'organization_type': t.organization_type,
        'published_date': t.published_date.isoformat() if t.published_date else '',
        'bidder_location': t.bidder_location,
        'contact_phone': t.contact_phone,
        'bid_document_cost_amount': t.bid_document_cost_amount,
        'bid_document_cost_payment_mode': t.bid_document_cost_payment_mode,
        'account_holder_name': t.account_holder_name,
        'bank_name': t.bank_name,
        'bank_account_number': t.bank_account_number,
        'cpo_amount': t.cpo_amount,
        'cpo_type': t.cpo_type,
        'bid_submission_date': t.bid_submission_date.isoformat() if t.bid_submission_date else '',
        'bid_opening_date': t.bid_opening_date.isoformat() if t.bid_opening_date else '',
    }

@login_required
def reupload_bid_document(request, pk):
    company = getattr(request, 'company', None)
    tender = get_object_or_404(Tender, pk=pk, company=company)
    emp = getattr(request.user, 'employee', None)

    if not emp or emp.role not in ('OFFICE', 'ADMIN'):
        return redirect('dashboard')

    if request.method == 'POST':
        if 'purchased_bid_document' in request.FILES:
            tender.purchased_bid_document = request.FILES['purchased_bid_document']
            tender.bid_document_uploaded_at = timezone.now()
            tender.bid_document_purged = False
            tender.save()
            log_action(emp, 'RE_UPLOAD_DOC', f'Re-uploaded bid doc for {tender.serial_number}', 'Tender', tender.id, company)
            messages.success(request, 'Bid Document re-uploaded successfully! The Bid Manager can now run the AI analysis.')
            return redirect('tender_detail', pk=tender.id)

    return render(request, 'reupload_doc.html', {'tender': tender})


@login_required
def delete_tender(request, pk):
    company = getattr(request, 'company', None)
    tender = get_object_or_404(Tender, pk=pk, company=company)
    emp = getattr(request.user, 'employee', None)
    
    # Only allow deleting if the status is DRAFT
    if tender.status == 'DRAFT':
        tender.delete()
        messages.success(request, 'Draft tender deleted successfully.')
        return redirect('tender_list')
    else:
        messages.error(request, 'You can only delete DRAFT tenders.')
        return redirect('tender_detail', pk=tender.id)
