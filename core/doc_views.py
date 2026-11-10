from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages
from .models import Document, Tender
from .services import log_action
from dateutil import parser as date_parser


def _document_context(request):
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


@login_required
def document_list(request):
    emp, company, roles = _document_context(request)
    if not company:
        return redirect('select_company')

    query = request.GET.get('q', '').strip()
    cat_filter = request.GET.get('cat', '')

    docs = Document.objects.filter(company=company)

    if query:
        from django.db.models import Q
        docs = docs.filter(
            Q(doc_id__icontains=query) | Q(title__icontains=query)
        )
    if cat_filter:
        docs = docs.filter(category=cat_filter)

    return render(request, 'doc_list.html', {
        'docs': docs,
        'query': query,
        'cat_filter': cat_filter,
        'categories': Document.CATEGORY_CHOICES,
        'membership_roles': roles,
    })


@login_required
def document_file(request, pk):
    emp, company, roles = _document_context(request)
    if not company:
        return redirect('select_company')

    doc = get_object_or_404(Document, pk=pk, company=company)

    if request.method == 'POST':
        if not roles.intersection({'OFFICE', 'ADMIN'}):
            return redirect('dashboard')

        try:
            binder_volume = int(request.POST.get('binder_volume', 1))
            if binder_volume < 1:
                raise ValueError
            expiry = request.POST.get('expiry_date', '').strip()
            expiry_date = date_parser.parse(expiry).date() if expiry else None
        except (TypeError, ValueError, OverflowError):
            messages.error(request, 'Invalid binder volume or expiry date.')
            return redirect('document_file', pk=doc.pk)
        except Exception:
            messages.error(request, 'Invalid expiry date.')
            return redirect('document_file', pk=doc.pk)

        doc.room = request.POST.get('room', '')
        doc.cabinet = request.POST.get('cabinet', '')
        doc.shelf = request.POST.get('shelf', '')
        doc.box = request.POST.get('box', '')
        doc.binder_volume = binder_volume
        doc.folder = request.POST.get('folder', '')
        if expiry:
            doc.expiry_date = expiry_date
        doc.is_physically_filed = True
        doc.save()

        log_action(
            emp,
            'DOCUMENT_FILED',
            f'Filed {doc.doc_id} at {doc.cabinet} / {doc.shelf} / {doc.box}',
            'Document',
            doc.id,
            company,
        )
        return redirect('document_list')

    return render(request, 'doc_file.html', {
        'doc': doc,
        'membership_roles': roles,
    })


@login_required
def document_scanner(request):
    emp, company, roles = _document_context(request)
    if not company:
        return redirect('select_company')

    doc_id = request.GET.get('scan_id', '').strip()
    if doc_id:
        doc = Document.objects.filter(
            company=company,
            doc_id__iexact=doc_id,
        ).first()
        if not doc:
            return render(request, 'doc_scanner.html', {
                'error': f'No document found with ID: {doc_id}'
            })
        return redirect('document_file', pk=doc.id)

    return render(request, 'doc_scanner.html')


@login_required
def document_upload(request):
    emp, company, roles = _document_context(request)
    if not company:
        return redirect('select_company')
    if not roles.intersection({'OFFICE', 'ADMIN', 'HR', 'FIN', 'LEGAL'}):
        return redirect('dashboard')

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        category = request.POST.get('category', 'GENERAL')
        tender_serial = request.POST.get('tender_serial', '').strip()
        file_obj = request.FILES.get('file')

        if not title or not file_obj:
            messages.error(request, 'A document title and file are required.')
            return redirect('document_upload')

        valid_categories = {choice[0] for choice in Document.CATEGORY_CHOICES}
        if category not in valid_categories:
            messages.error(request, 'Invalid document category.')
            return redirect('document_upload')

        tender = None
        if tender_serial:
            tender = Tender.objects.filter(
                company=company,
                serial_number__iexact=tender_serial,
            ).first()
            if not tender:
                messages.error(request, 'Tender not found in the selected company.')
                return redirect('document_upload')

        try:
            binder_volume = int(request.POST.get('binder_volume', 1))
            if binder_volume < 1:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, 'Binder volume must be a positive number.')
            return redirect('document_upload')

        binder_name = request.POST.get('binder_name', '')
        doc = Document.objects.create(
            company=company,
            title=title,
            category=category,
            binder_name=binder_name,
            binder_volume=binder_volume,
            file=file_obj,
            tender=tender,
            uploaded_by=emp,
        )
        log_action(
            emp,
            'UPLOAD_DOCUMENT',
            f'Uploaded {title} (Doc ID: {doc.doc_id})',
            'Document',
            doc.id,
            company,
        )
        return redirect('document_list')

    return render(request, 'document_upload.html')
