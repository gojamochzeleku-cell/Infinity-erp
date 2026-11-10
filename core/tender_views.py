from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from dateutil import parser as date_parser

from .gemini import extract_tender_from_pdf
from .models import Tender, TenderLot
from .services import log_action
from .chain import create_task

def _emp(request):
    return getattr(request.user, 'employee', None)

def _co(request):
    return getattr(request, 'company', None)


def _membership_roles(request):
    emp = _emp(request)
    company = _co(request)
    membership = getattr(request, 'membership', None)

    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or not emp or not emp.is_active
        or not company or not company.is_active
        or not membership or not membership.is_active
        or membership.employee_id != emp.id
        or membership.company_id != company.id
    ):
        return None

    return set(membership.roles.values_list('role', flat=True))


def _company_access(request, allowed_roles=None):
    roles = _membership_roles(request)
    if roles is None:
        return False
    if allowed_roles is None:
        return True
    return bool(roles.intersection(allowed_roles))

@login_required
def tender_list(request):
    from django.db.models import Q
    emp, company = _emp(request), _co(request)
    if emp is None or company is None or not _company_access(request):
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
        'tenders': tenders, 'query': query,
        'status_filter': status_filter, 'org_filter': org_filter,
        'status_choices': Tender.STATUS_CHOICES,
        'org_choices': Tender.ORG_TYPE_CHOICES
    })

@login_required
def tender_register(request):
    emp, company = _emp(request), _co(request)
    if emp is None or company is None or not _company_access(request):
        return redirect('select_company')
    if not _company_access(request, {'ADMIN', 'CEO', 'GM', 'VGM', 'OFFICE', 'BIDM', 'PROC'}):
        return redirect('dashboard')
    initial, errors = {}, []

    if request.method == 'POST':
        action = request.POST.get('action', 'save_draft')
        form_data = _collect(request)
        pdf_file = request.FILES.get('bid_information_sheet')

        if action == 'extract':
            if pdf_file is None:
                errors.append('Choose the bid information sheet PDF first.')
            else:
                try:
                    # 1. Extract data synchronously (fast for < 1MB files)
                    result = extract_tender_from_pdf(pdf_file)
                    
                    if 'error' in result:
                        messages.error(request, "AI couldn't read the PDF. Please fill the form manually. (%s)" % result['error'])
                        initial = form_data
                        initial['has_pdf'] = True
                    else:
                        # AI Success! Pre-fill the initial dictionary
                        messages.success(request, 'AI successfully extracted the data. Review and submit.')
                        initial = _map_ai_to_initial(result['data'])
                        initial['has_pdf'] = True
                        
                        # If AI found lots, we can't easily render them in the pure HTML form without JS magic, 
                        # so we save the draft first, then redirect to the Edit page to show the lots.
                        if result['data'].get('lots'):
                            temp = Tender(company=company, registered_by=emp, status='DRAFT', **form_data)
                            temp.bid_information_sheet = pdf_file
                            temp.save()
                            _apply(temp, result['data'])
                            temp.ai_extracted = True
                            temp.save()
                            return redirect('tender_edit', pk=temp.id)

                except Exception as e:
                    errors.append('System Error: %s' % str(e))
                    initial = form_data

        elif action in ('save_draft', 'submit'):
            form_data['status'] = 'REGISTERED' if action == 'submit' else 'DRAFT'
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
    if emp is None or company is None or not _company_access(request):
        return redirect('select_company')
    if not _company_access(request, {'ADMIN', 'CEO', 'GM', 'VGM', 'OFFICE', 'BIDM', 'PROC'}):
        return redirect('dashboard')
    tender = get_object_or_404(Tender, pk=pk, company=company)
    errors = []

    if request.method == 'POST':
        action = request.POST.get('action', 'save_draft')
        pdf_file = request.FILES.get('bid_information_sheet')
        
        if action == 'extract':
            if pdf_file:
                tender.bid_information_sheet = pdf_file
                tender.save()
            if not tender.bid_information_sheet:
                messages.error(request, 'Upload a PDF first.')
                return redirect('tender_edit', pk=tender.id)
            
            result = extract_tender_from_pdf(tender.bid_information_sheet)
            if 'error' in result:
                messages.error(request, 'AI failed: %s' % result['error'])
            else:
                _apply(tender, result['data'])
                tender.ai_extracted = True
                tender.save()
                messages.success(request, 'AI extracted fields. Review and submit.')
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

    return render(request, 'tender_form.html', {
        'tender': tender, 'initial': _initial(tender), 'errors': errors, 'is_new': False
    })

@login_required
def tender_detail(request, pk):
    emp, company = _emp(request), _co(request)
    if emp is None or company is None or not _company_access(request):
        return redirect('select_company')
    tender = get_object_or_404(Tender, pk=pk, company=company)
    return render(request, 'tender_detail.html', {'tender': tender})

@login_required
def tender_submit(request, pk):
    emp, company = _emp(request), _co(request)
    if emp is None or company is None or not _company_access(request):
        return redirect('select_company')
    if request.method != 'POST':
        return redirect('tender_detail', pk=pk)
    if not _company_access(request, {'ADMIN', 'CEO', 'GM', 'VGM', 'OFFICE', 'BIDM'}):
        return redirect('dashboard')
    tender = get_object_or_404(Tender, pk=pk, company=company)
    if request.method == 'POST' and tender.status == 'DRAFT':
        tender.status = 'REGISTERED'
        tender.save()
        _to_bid_manager(request, tender)
        log_action(emp, 'TENDER_REGISTERED', tender.bid_title, 'Tender', tender.id, company)
        messages.success(request, 'Sent to Bid Manager.')
    return redirect('tender_detail', pk=pk)

@login_required
def delete_tender(request, pk):
    emp, company = _emp(request), _co(request)

    if not emp or not company or not _company_access(request):
        return redirect('select_company')

    if request.method != 'POST':
        return redirect('tender_detail', pk=pk)

    if not _company_access(request, {'ADMIN', 'CEO', 'GM', 'OFFICE'}):
        return redirect('dashboard')

    tender = get_object_or_404(Tender, pk=pk, company=company)

    if tender.status == 'DRAFT':
        tender.delete()
        messages.success(request, 'Draft tender deleted.')
        return redirect('tender_list')

    messages.error(request, 'You can only delete DRAFT tenders.')
    return redirect('tender_detail', pk=tender.id)

@login_required
def reupload_bid_document(request, pk):
    emp, company = _emp(request), _co(request)

    if not emp or not company or not _company_access(request):
        return redirect('select_company')

    if not _company_access(request, {'OFFICE', 'ADMIN'}):
        return redirect('dashboard')

    tender = get_object_or_404(Tender, pk=pk, company=company)

    if request.method == 'POST':
        if 'purchased_bid_document' in request.FILES:
            tender.purchased_bid_document = request.FILES['purchased_bid_document']
            tender.bid_document_uploaded_at = timezone.now()
            tender.bid_document_purged = False
            tender.save()
            messages.success(request, 'Bid Document re-uploaded successfully!')
            return redirect('tender_detail', pk=tender.id)

    return render(request, 'reupload_doc.html', {'tender': tender})

@login_required
def view_ai_log(request):
    # Global diagnostic logs are not available to company users.
    return redirect('dashboard')


@login_required
def view_ai_success_log(request):
    # Global diagnostic logs are not available to company users.
    return redirect('dashboard')


def _to_bid_manager(request, tender):
    task = create_task(
        company=tender.company, code='T02', role='BIDM',
        title='Feasibility review: %s' % tender.bid_title,
        description='Review feasibility and press GO or NO-GO.',
        payload={'tender_id': tender.id, 'serial': tender.serial_number, 'reference': tender.bid_reference_number},
        action_url=reverse('tender_detail', args=[tender.id]),
    )
    if task is None:
        messages.warning(request, 'No Bid Manager in this company.')

def _safe_date(val):
    if not val: return None
    try: return date_parser.parse(val).date()
    except: return None

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
        ('cpo_type', ['AMOUNT', 'PERCENT']),
    ):
        val = data.get(field)
        if val:
            val = str(val).strip().upper().replace(' ', '_')
            if val in allowed: setattr(tender, field, val)

    for field in ('bidder_location', 'contact_phone', 'account_holder_name', 'bank_name', 'bank_account_number'):
        if data.get(field): setattr(tender, field, str(data[field])[:200])
            
    for field in ('published_date', 'bid_submission_date', 'bid_opening_date'):
        if data.get(field): setattr(tender, field, _safe_date(str(data[field])))
            
    for field in ('bid_document_cost_amount', 'cpo_amount'):
        if data.get(field) not in (None, ''):
            try: setattr(tender, field, str(data[field]).replace(',', ''))
            except: pass

    if 'lots' in data and isinstance(data['lots'], list):
        tender.lots.all().delete()
        for lot_data in data['lots']:
            lot_name = lot_data.get('lot_name') or 'General'
            desc = lot_data.get('description') or ''
            cpo = lot_data.get('cpo_amount') or 0
            cpo_type = lot_data.get('cpo_type') or 'AMOUNT'
            sub_date = _safe_date(lot_data.get('submission_date'))
            open_date = _safe_date(lot_data.get('opening_date'))
            TenderLot.objects.create(
                tender=tender, lot_name=str(lot_name)[:100], description=str(desc)[:300],
                cpo_amount=cpo, cpo_type=cpo_type,
                submission_date=sub_date, opening_date=open_date
            )

def _initial(t):
    return {
        'bid_reference_number': t.bid_reference_number, 'bid_title': t.bid_title,
        'bid_type': t.bid_type, 'purchaser_name': t.purchaser_name,
        'organization_type': t.organization_type,
        'published_date': t.published_date.isoformat() if t.published_date else '',
        'bidder_location': t.bidder_location, 'contact_phone': t.contact_phone,
        'bid_document_cost_amount': t.bid_document_cost_amount,
        'bid_document_cost_payment_mode': t.bid_document_cost_payment_mode,
        'account_holder_name': t.account_holder_name, 'bank_name': t.bank_name,
        'bank_account_number': t.bank_account_number, 'cpo_amount': t.cpo_amount,
        'cpo_type': t.cpo_type,
        'bid_submission_date': t.bid_submission_date.isoformat() if t.bid_submission_date else '',
        'bid_opening_date': t.bid_opening_date.isoformat() if t.bid_opening_date else '',
    }

def _map_ai_to_initial(data):
    return {
        'bid_reference_number': data.get('bid_reference_number', ''),
        'bid_title': data.get('bid_title', ''),
        'bid_type': data.get('bid_type', 'DOCUMENT'),
        'purchaser_name': data.get('purchaser_name', ''),
        'organization_type': data.get('organization_type', 'GOVERNMENT'),
        'published_date': data.get('published_date', ''),
        'bidder_location': data.get('bidder_location', ''),
        'contact_phone': data.get('contact_phone', ''),
        'bid_document_cost_amount': data.get('bid_document_cost_amount', ''),
        'bid_document_cost_payment_mode': data.get('bid_document_cost_payment_mode', 'CASH'),
        'account_holder_name': data.get('account_holder_name', ''),
        'bank_name': data.get('bank_name', ''),
        'bank_account_number': data.get('bank_account_number', ''),
        'cpo_amount': data.get('cpo_amount', ''),
        'cpo_type': data.get('cpo_type', 'AMOUNT'),
        'bid_submission_date': data.get('bid_submission_date', ''),
        'bid_opening_date': data.get('bid_opening_date', ''),
    }
