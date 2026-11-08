from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import (
    Company, Tender, Task, Document, Notification, ChatRoom, ChatMessage,
    CashFlowEntry, AuditLog, TenderReview, Supplier, Shipment, InventoryItem,
    Product, StockTransaction, LegalCase, LeaveRequest, ExpenseRequest,
    PayrollRecord, EmployeeDocument, ITTicket, PerformanceReview, Memo, 
    Correspondence, CompanyResource, CourierDispatch, SupplyRequest, VisitorLog
)

class Command(BaseCommand):
    help = 'Deletes test data and Abyssinia PLC, keeping core users.'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting deep clean...")

        # 1. Delete Abyssinia PLC
        abyssinia = Company.objects.filter(short_name='ABY').first()
        if abyssinia:
            self.stdout.write("Removing Abyssinia PLC and its employees...")
            # Delete employees specific to Abyssinia
            User.objects.filter(employee__company=abyssinia).delete()
            abyssinia.delete()

        # 2. Clear all transactional models
        models_to_clear = [
            Tender, Task, Document, Notification, ChatRoom, ChatMessage,
            CashFlowEntry, AuditLog, TenderReview, Supplier, Shipment, InventoryItem,
            Product, StockTransaction, LegalCase, LeaveRequest, ExpenseRequest,
            PayrollRecord, EmployeeDocument, ITTicket, PerformanceReview, Memo, 
            Correspondence, CompanyResource, CourierDispatch, SupplyRequest, VisitorLog
        ]

        for model in models_to_clear:
            count = model.objects.all().delete()[0]
            self.stdout.write(f"Cleared {count} records from {model.__name__}")

        self.stdout.write(self.style.SUCCESS("Deep clean complete. System is ready for real data."))
