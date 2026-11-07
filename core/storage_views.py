import os
import hashlib
import zipfile
import io
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, FileResponse
from .models import Document, Tender
from .services import log_action

def calculate_file_hash(file_obj):
    """Calculates SHA-256 hash of an uploaded file."""
    hasher = hashlib.sha256()
    if hasattr(file_obj, 'chunks'):
        for chunk in file_obj.chunks():
            hasher.update(chunk)
    else:
        hasher.update(file_obj.read())
    return hasher.hexdigest()

@login_required
def storage_dashboard(request):
    company = getattr(request, 'company', None)
    if not company: return redirect('select_company')

    # Calculate total storage usage
    docs = Document.objects.filter(company=company)
    total_size = 0
    doc_sizes = []

    for d in docs:
        try:
            size = d.file.size
            total_size += size
            doc_sizes.append((d, size))
        except:
            pass # Handle missing files gracefully

    total_mb = round(total_size / (1024 * 1024), 2)
    
    # Sort by largest files
    doc_sizes.sort(key=lambda x: x[1], reverse=True)
    largest_files = doc_sizes[:10]

    return render(request, 'storage_dashboard.html', {
        'total_mb': total_mb,
        'total_files': docs.count(),
        'largest_files': largest_files
    })

@login_required
def archive_tender_files(request, pk):
    """Bundles all files for a completed tender into a Zip and deletes them from cloud (if confirmed)."""
    company = getattr(request, 'company', None)
    tender = get_object_or_404(Tender, pk=pk, company=company)
    emp = getattr(request.user, 'employee', None)

    if not emp or emp.role not in ('ADMIN', 'CEO', 'IT'):
        return redirect('dashboard')

    if request.method == 'POST':
        # Create an in-memory ZIP file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Add Tender Files
            fields = ['bid_information_sheet', 'purchased_bid_document', 'receipt', 'cpo_document', 'bank_letter', 'final_technical_proposal', 'final_agreement']
            for field in fields:
                file_obj = getattr(tender, field)
                if file_obj:
                    try:
                        zip_file.writestr(f"{field}_{file_obj.name.split('/')[-1]}", file_obj.read())
                    except:
                        pass
            
            # Add Linked Documents
            docs = Document.objects.filter(tender=tender)
            for d in docs:
                if d.file:
                    try:
                        zip_file.writestr(f"doc_{d.doc_id}_{d.title}.pdf", d.file.read())
                    except:
                        pass

        zip_buffer.seek(0)
        log_action(emp, 'ARCHIVE_TENDER', f'Archived files for {tender.serial_number}', 'Tender', tender.id, company)
        
        response = HttpResponse(zip_buffer, content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="Tender_Archive_{tender.serial_number}.zip"'
        return response

    return render(request, 'archive_confirm.html', {'tender': tender})
