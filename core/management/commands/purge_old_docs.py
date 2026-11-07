from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from core.models import Tender, Employee, Task
from core.services import notify

class Command(BaseCommand):
    help = 'Purges bid documents older than 3 days to save storage.'

    def handle(self, *args, **kwargs):
        three_days_ago = timezone.now() - timedelta(days=3)
        
        # Find tenders with bid documents uploaded more than 3 days ago
        tenders = Tender.objects.filter(
            bid_document_uploaded_at__lt=three_days_ago,
            bid_document_purged=False
        ).exclude(purchased_bid_document='')

        count = 0
        for t in tenders:
            # Find the Office Assistant to notify
            office = Employee.objects.filter(company=t.company, role='OFFICE', is_active=True).first()
            
            # 1. Delete the file from Cloudflare R2
            if t.purchased_bid_document:
                t.purchased_bid_document.delete(save=False)
                
            # 2. Mark as purged
            t.bid_document_purged = True
            t.save()
            
            # 3. Notify Office Assistant
            if office:
                notify(
                    office, 
                    f'Action Required: Bid Document Archived', 
                    f'The heavy bid document for {t.serial_number} has been removed from the cloud to save space. Please ensure a local backup exists. You can re-upload it from the Tender page if needed.'
                )
            count += 1
            
        self.stdout.write(self.style.SUCCESS(f'Successfully purged {count} bid documents.'))
