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


from django.core.mail import EmailMessage
from django.conf import settings
from .models import Correspondence, Document
from dateutil import parser as date_parser

@login_required
def compose_email(request):
    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    if not emp or not company: return redirect('select_company')
    
    if emp.role not in ('OFFICE', 'ADMIN'):
        return redirect('dashboard')

    if request.method == 'POST':
        to_email = request.POST.get('to_email')
        subject = request.POST.get('subject')
        body = request.POST.get('body')
        file_obj = request.FILES.get('attachment')
        
        try:
            # 1. Send the Email
            email = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email])
            if file_obj:
                email.attach(file_obj.name, file_obj.read(), file_obj.content_type)
            email.send()
            
            # 2. Log it as an Outgoing Letter in the Registry
            letter = Correspondence.objects.create(
                company=company, direction='OUTGOING', sender_recipient=to_email,
                subject=subject, file=file_obj, uploaded_by=emp
            )
            
            # 3. Register the attachment in the Document Registry if it exists
            if file_obj:
                Document.objects.create(
                    company=company, title=f"Email Attachment: {subject}", 
                    file=file_obj, uploaded_by=emp, category='MAIL'
                )
                
            from .services import log_action
            log_action(emp, 'SEND_EMAIL', f'Sent email to {to_email} - Subject: {subject}', 'Correspondence', letter.id, company)
            
            from django.contrib import messages
            messages.success(request, f'Email successfully sent to {to_email}!')
            return redirect('mail_list')
            
        except Exception as e:
            from django.contrib import messages
            messages.error(request, f'Failed to send email: {str(e)}')

    return render(request, 'compose_email.html')
