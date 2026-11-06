import imaplib
import email
from email.header import decode_header
import os
import io
from django.core.files.base import ContentFile
from .models import Correspondence, Document, Employee
from .services import log_action

def fetch_new_emails(company, employee):
    # 1. Connect to IMAP
    host = os.environ.get('EMAIL_IMAP_HOST', 'imap.gmail.com')
    user = os.environ.get('EMAIL_HOST_USER', '')
    password = os.environ.get('EMAIL_HOST_PASSWORD', '')

    if not user or not password:
        return {'error': 'Email credentials not configured in Render environment variables.'}

    try:
        mail = imaplib.IMAP4_SSL(host)
        mail.login(user, password)
        mail.select('inbox')
    except Exception as e:
        return {'error': f'Failed to connect to email server: {str(e)}'}

    # 2. Search for Unread Emails
    status, messages = mail.search(None, 'UNSEEN')
    if status != 'OK':
        return {'error': 'Failed to search emails.'}

    email_ids = messages[0].split()
    if not email_ids:
        mail.logout()
        return {'success': True, 'count': 0, 'message': 'No new emails found.'}

    processed_count = 0

    # 3. Process each email
    for e_id in email_ids:
        status, msg_data = mail.fetch(e_id, '(RFC822)')
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                
                # Extract Subject
                subject, encoding = decode_header(msg["Subject"])[0]
                if isinstance(subject, bytes):
                    subject = subject.decode(encoding if encoding else "utf-8")
                
                # Extract Sender
                sender, encoding = decode_header(msg.get("From"))[0]
                if isinstance(sender, bytes):
                    sender = sender.decode(encoding if encoding else "utf-8")

                attachment_file = None
                attachment_name = None

                # Extract Attachments (PDFs / Images)
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        content_disposition = str(part.get("Content-Disposition"))
                        
                        if "attachment" in content_disposition:
                            filename = part.get_filename()
                            if filename:
                                # Decode filename if needed
                                fname, encoding = decode_header(filename)[0]
                                if isinstance(fname, bytes):
                                    fname = fname.decode(encoding if encoding else "utf-8")
                                
                                payload = part.get_payload(decode=True)
                                if payload:
                                    attachment_file = ContentFile(payload, name=fname)
                                    attachment_name = fname
                                    break # Just take the first attachment for simplicity

                # 4. Create the Correspondence Record
                letter = Correspondence.objects.create(
                    company=company,
                    direction='INCOMING',
                    sender_recipient=sender,
                    subject=subject,
                    file=attachment_file,
                    uploaded_by=employee
                )

                # 5. Create the Document Record if there's an attachment
                if attachment_file:
                    Document.objects.create(
                        company=company,
                        title=f"Email Attachment: {subject}",
                        file=attachment_file,
                        uploaded_by=employee,
                        category='MAIL'
                    )

                # Mark as Read
                mail.store(e_id, '+FLAGS', '\\Seen')
                processed_count += 1

    mail.logout()
    log_action(employee, 'FETCH_EMAILS', f'Fetched {processed_count} new emails.', 'Correspondence', '', company)
    return {'success': True, 'count': processed_count}

