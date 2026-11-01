from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.utils import timezone
from dateutil import parser as date_parser
from .models import Correspondence
from .chain import register_document

@login_required
def mail_list(request):
    company = getattr(request, 'company', None)
    if not company:
        return redirect('select_company')
        
    letters = Correspondence.objects.filter(company=company)
    direction_filter = request.GET.get('type', '')
    if direction_filter:
        letters = letters.filter(direction=direction_filter)
        
    return render(request, 'mail_list.html', {
        'letters': letters,
        'direction_filter': direction_filter
    })

@login_required
def mail_create(request, direction):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company:
        return redirect('select_company')
        
    if request.method == 'POST':
        ref = request.POST.get('reference_number', '')
        ldate = date_parser.parse(request.POST.get('letter_date')).date()
        sender_rec = request.POST.get('sender_recipient')
        subject = request.POST.get('subject')
        assigned_id = request.POST.get('assigned_to')
        pdf_file = request.FILES.get('file')
        
        assigned_emp = None
        if assigned_id:
            from .models import Employee
            assigned_emp = Employee.objects.filter(id=assigned_id).first()
            
        letter = Correspondence.objects.create(
            company=company, direction=direction, reference_number=ref,
            letter_date=ldate, sender_recipient=sender_rec, subject=subject,
            file=pdf_file, uploaded_by=emp, assigned_to=assigned_emp
        )
        
        # Automatically register it in the Document Registry for QR/Physical tracking!
        from .models import Document
        Document.objects.create(
            company=company, 
            title=f"{direction} Letter: {subject}", 
            file=pdf_file, 
            uploaded_by=emp,
            category='MAIL'
        )
        
        return redirect('mail_list')

    from .models import Employee
    employees = Employee.objects.all().order_by('user__first_name')
    return render(request, 'mail_form.html', {'direction': direction, 'employees': employees})
