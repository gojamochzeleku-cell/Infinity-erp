from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from .models import Document, Tender
from .services import log_action
from dateutil import parser as date_parser

@login_required
def document_list(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('select_company')
    
    query = request.GET.get('q', '').strip()
    cat_filter = request.GET.get('cat', '')
    
    docs = Document.objects.filter(company=company)
    
    if query:
        docs = docs.filter(doc_id__icontains=query) | docs.filter(title__icontains=query)
    if cat_filter:
        docs = docs.filter(category=cat_filter)
        
    return render(request, 'doc_list.html', {
        'docs': docs, 'query': query, 'cat_filter': cat_filter,
        'categories': Document.CATEGORY_CHOICES
    })

@login_required
def document_file(request, pk):
    emp = getattr(request.user, 'employee', None)
    doc = get_object_or_404(Document, pk=pk)
    
    if request.method == 'POST':
        doc.room = request.POST.get('room', '')
        doc.cabinet = request.POST.get('cabinet', '')
        doc.shelf = request.POST.get('shelf', '')
        doc.box = request.POST.get('box', '')
        doc.folder = request.POST.get('folder', '')
        expiry = request.POST.get('expiry_date')
        if expiry:
            doc.expiry_date = date_parser.parse(expiry).date()
        doc.is_physically_filed = True
        doc.save()
        log_action(emp, 'DOCUMENT_FILED', f'Filed {doc.doc_id} at {doc.cabinet} / {doc.shelf} / {doc.box}', 'Document', doc.id, doc.company)
        return redirect('document_list')
        
    return render(request, 'doc_file.html', {'doc': doc})

@login_required
def document_scanner(request):
    doc_id = request.GET.get('scan_id', '').strip()
    if doc_id:
        company = getattr(request, 'company', None)
        doc = Document.objects.filter(company=company, doc_id__iexact=doc_id).first()
        if not doc:
            return render(request, 'doc_scanner.html', {'error': f'No document found with ID: {doc_id}'})
        else:
            return redirect('document_file', pk=doc.id)
            
    return render(request, 'doc_scanner.html')

@login_required
def document_upload(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    
    if not emp or not company or emp.role not in ('OFFICE', 'ADMIN', 'HR', 'FIN', 'LEGAL'):
        return redirect('dashboard')

    if request.method == 'POST':
        title = request.POST.get('title')
        category = request.POST.get('category', 'GENERAL')
        tender_serial = request.POST.get('tender_serial', '').strip()
        file_obj = request.FILES.get('file')
        
        tender = None
        if tender_serial:
            tender = Tender.objects.filter(company=company, serial_number__iexact=tender_serial).first()
            
        if file_obj:
            binder_name = request.POST.get('binder_name', '')
            doc = Document.objects.create(
                company=company,
                title=title,
                category=category,
                binder_name=binder_name,
                file=file_obj,
                tender=tender,
                uploaded_by=emp
            )
            log_action(emp, 'UPLOAD_DOCUMENT', f'Uploaded {title} (Doc ID: {doc.doc_id})', 'Document', doc.id, company)
            return redirect('document_list')

    return render(request, 'document_upload.html')
