import imaplib
import email
from email.header import decode_header
import os
from django.core.files.base import ContentFile
from django.conf import settings
from .models import Correspondence, Document, Employee
from .services import log_action

def fetch_new_emails(company, employee):
    # 1. Get the correct credentials for this company
    email_user, email_pass = settings.get_company_email(company)
    
    if not email_user or not email_pass:
        return {'error': f'Email credentials not configured for {company.short_name}.'}

    host = os.environ.get('EMAIL_IMAP_HOST', 'imap.gmail.com')

    try:
        mail = imaplib.IMAP4_SSL(host)
        mail.login(email_user, email_pass)
        mail.select('inbox')
    except Exception as e:
        return {'error': f'Failed to connect to email server for {email_user}: {str(e)}'}

    status, messages = mail.search(None, 'UNSEEN')
    if status != 'OK':
        return {'error': 'Failed to search emails.'}

    email_ids = messages[0].split()
    if not email_ids:
        mail.logout()
        return {'success': True, 'count': 0, 'message': 'No new emails found.'}

    processed_count = 0

    for e_id in email_ids:
        status, msg_data = mail.fetch(e_id, '(RFC822)')
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                
                subject, encoding = decode_header(msg["Subject"])[0]
                if isinstance(subject, bytes):
                    subject = subject.decode(encoding if encoding else "utf-8")
                
                sender, encoding = decode_header(msg.get("From"))[0]
                if isinstance(sender, bytes):
                    sender = sender.decode(encoding if encoding else "utf-8")

                attachment_file = None
                attachment_name = None

                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        content_disposition = str(part.get("Content-Disposition"))
                        
                        if "attachment" in content_disposition:
                            filename = part.get_filename()
                            if filename:
                                fname, encoding = decode_header(filename)[0]
                                if isinstance(fname, bytes):
                                    fname = fname.decode(encoding if encoding else "utf-8")
                                
                                payload = part.get_payload(decode=True)
                                if payload:
                                    attachment_file = ContentFile(payload, name=fname)
                                    attachment_name = fname
                                    break

                letter = Correspondence.objects.create(
                    company=company,
                    direction='INCOMING',
                    sender_recipient=sender,
                    subject=subject,
                    file=attachment_file,
                    uploaded_by=employee
                )

                if attachment_file:
                    Document.objects.create(
                        company=company,
                        title=f"Email Attachment: {subject}",
                        file=attachment_file,
                        uploaded_by=employee,
                        category='MAIL'
                    )

                mail.store(e_id, '+FLAGS', '\\Seen')
                processed_count += 1

    mail.logout()
    log_action(employee, 'FETCH_EMAILS', f'Fetched {processed_count} new emails from {email_user}.', 'Correspondence', '', company)
    return {'success': True, 'count': processed_count}
