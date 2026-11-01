import os
import shutil
from django.conf import settings
from django.core.management.base import BaseCommand
from core.models import (
    Notification, Task, Tender, Document, Memo, Correspondence, 
    LegalCase, CashFlowEntry, Supplier, Shipment, InventoryItem, 
    Product, StockTransaction, CourierDispatch, SupplyRequest, 
    VisitorLog, LeaveRequest, ExpenseRequest, PayrollRecord, 
    EmployeeDocument, ITTicket, PerformanceReview, TenderReview, 
    AuditLog
)

class Command(BaseCommand):
    help = 'Deletes all transactional data and uploaded files, but keeps users and companies.'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting factory reset...")

        # 1. Delete all transactional database records
        models_to_clear = [
            Notification, Task, Tender, Document, Memo, Correspondence, 
            LegalCase, CashFlowEntry, Supplier, Shipment, InventoryItem, 
            Product, StockTransaction, CourierDispatch, SupplyRequest, 
            VisitorLog, LeaveRequest, ExpenseRequest, PayrollRecord, 
            EmployeeDocument, ITTicket, PerformanceReview, TenderReview, 
            AuditLog
        ]

        for model in models_to_clear:
            count = model.objects.all().delete()[0]
            self.stdout.write(f"Deleted {count} records from {model.__name__}")

        # 2. Delete physical files from the media folder
        media_root = settings.MEDIA_ROOT
        if os.path.exists(media_root):
            for filename in os.listdir(media_root):
                file_path = os.path.join(media_root, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                    self.stdout.write(f"Deleted physical file/folder: {filename}")
                except Exception as e:
                    self.stdout.write(f"Failed to delete {file_path}. Reason: {e}")
        else:
            self.stdout.write("No media folder found, skipping file deletion.")

        self.stdout.write(self.style.SUCCESS("FACTORY RESET COMPLETE. The system is now a clean slate."))
