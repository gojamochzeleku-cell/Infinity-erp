import uuid
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from .models import Tender, Document, Task
from .chain import find_role
from .services import notify, log_action

@login_required
def generate_supplier_link(request, pk):
    company = getattr(request, 'company', None)
    tender = Tender.objects.filter(pk=pk, company=company).first()
    if not tender:
        return redirect('dashboard')

    # Generate token if it doesn't exist
    if not tender.supplier_portal_token:
        tender.supplier_portal_token = str(uuid.uuid4()).replace('-', '')[:16]
        tender.save()

    # Build the absolute URL
    domain = request.build_absolute_uri('/')[:-1]
    portal_url = f"{domain}/supplier/upload/{tender.supplier_portal_token}/"

    return render(request, 'generate_supplier_link.html', {'tender': tender, 'portal_url': portal_url})

# NO LOGIN REQUIRED FOR THIS VIEW
def supplier_upload(request, token):
    tender = Tender.objects.filter(supplier_portal_token=token).first()
    
    if not tender:
        return HttpResponse("<h1 style='text-align:center; margin-top:50px;'>Invalid or Expired Link.</h1>", status=404)

    if request.method == 'POST':
        company_name = request.POST.get('company_name', '')
        proforma_file = request.FILES.get('proforma_file')
        auth_file = request.FILES.get('auth_file')

        if proforma_file:
            # 1. Register in Global Doc Registry
            reg_doc = Document.objects.create(
                company=tender.company, 
                title=f"Supplier Proforma: {company_name} - {tender.serial_number}", 
                file=proforma_file, uploaded_by=None, category='TENDER', tender=tender
            )
            # 2. Attach to Tender
            tender.supplier_name = company_name
            tender.save()

        if auth_file:
            Document.objects.create(
                company=tender.company, 
                title=f"Manufacturer Auth: {company_name} - {tender.serial_number}", 
                file=auth_file, uploaded_by=None, category='TENDER', tender=tender
            )

        # 3. Create a Task for the Bid Manager to review
        bid_manager = find_role(tender.company, 'BIDM')
        if bid_manager:
            task = Task.objects.create(
                code='T10_REVIEW', title=f'Review Supplier Docs: {tender.bid_title}',
                description=f'Supplier {company_name} uploaded new documents via the portal.',
                kind='CHAIN', company=tender.company, assigned_to=bid_manager,
                payload={'tender_id': tender.id}
            )
            notify(bid_manager, 'Supplier Uploaded Docs', f'{company_name} uploaded files for {tender.serial_number}', task)
            
        log_action(None, 'SUPPLIER_UPLOAD', f'{company_name} uploaded files for {tender.serial_number}', 'Tender', tender.id, tender.company)

        return render(request, 'supplier_success.html', {'tender': tender})

    return render(request, 'supplier_portal.html', {'tender': tender})
