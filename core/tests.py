from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
import tempfile

from core.models import (
    Company,
    Employee,
    CompanyMembership,
    MembershipRole,
    Supplier,
    Product,
    StockTransaction,
    Shipment,
    Tender,
    InventoryItem,
    CashFlowEntry,
    TaskHistory,
    Task,
)


class InventorySupplierPermissionTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Test Company A",
            short_name="TCA",
        )
        self.other_company = Company.objects.create(
            name="Test Company B",
            short_name="TCB",
        )

        self.user = User.objects.create_user(
            username="inventory_test_user",
            password="test-password-only",
        )
        self.employee = Employee.objects.create(
            user=self.user,
            company=self.company,
            role="STAFF",
        )
        self.membership = CompanyMembership.objects.create(
            employee=self.employee,
            company=self.company,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["company_id"] = self.company.pk
        session.save()

        self.set_role("PROC")

    def set_role(self, role):
        MembershipRole.objects.filter(
            membership=self.membership
        ).delete()
        MembershipRole.objects.create(
            membership=self.membership,
            role=role,
        )

    def test_supplier_list_is_company_scoped(self):
        own = Supplier.objects.create(
            company=self.company,
            name="Own Company Supplier",
        )
        Supplier.objects.create(
            company=self.other_company,
            name="Other Company Supplier",
        )

        response = self.client.get(reverse("supplier_list"))

        self.assertEqual(response.status_code, 200)
        supplier_ids = set(
            response.context["suppliers"].values_list("id", flat=True)
        )
        self.assertEqual(supplier_ids, {own.pk})

    def test_read_only_logistics_cannot_add_supplier(self):
        self.set_role("LOG")

        response = self.client.post(
            reverse("supplier_list"),
            {"name": "Unauthorized Supplier"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            Supplier.objects.filter(
                company=self.company,
                name="Unauthorized Supplier",
            ).exists()
        )

    def test_procurement_can_add_supplier_to_own_company(self):
        response = self.client.post(
            reverse("supplier_list"),
            {"name": "Authorized Supplier"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Supplier.objects.filter(
                company=self.company,
                name="Authorized Supplier",
            ).exists()
        )
        self.assertFalse(
            Supplier.objects.filter(
                company=self.other_company,
                name="Authorized Supplier",
            ).exists()
        )

    def test_procurement_can_add_product_and_initial_stock(self):
        response = self.client.post(
            reverse("inventory_dashboard"),
            {
                "name": "Test Generator",
                "sku": "TEST-GEN-001",
                "category": "Machinery",
                "location": "Test Warehouse",
                "initial_stock": "12",
                "min_stock": "3",
            },
        )

        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(
            company=self.company,
            sku="TEST-GEN-001",
        )
        self.assertEqual(product.current_stock, 12)

        transaction = StockTransaction.objects.get(product=product)
        self.assertEqual(transaction.trans_type, "IN")
        self.assertEqual(transaction.quantity, 12)
        self.assertEqual(transaction.created_by, self.employee)

    def test_logistics_cannot_add_product(self):
        self.set_role("LOG")

        response = self.client.post(
            reverse("inventory_dashboard"),
            {
                "name": "Unauthorized Product",
                "initial_stock": "5",
                "min_stock": "1",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            Product.objects.filter(
                company=self.company,
                name="Unauthorized Product",
            ).exists()
        )

    def test_manual_adjustment_records_actual_stock_delta(self):
        product = Product.objects.create(
            company=self.company,
            name="Adjustment Test Product",
            current_stock=10,
        )

        response = self.client.post(
            reverse("inventory_adjust", args=[product.pk]),
            {
                "trans_type": "ADJUST",
                "quantity": "4",
                "notes": "Test stock count correction",
            },
        )

        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.current_stock, 4)

        transaction = StockTransaction.objects.get(product=product)
        self.assertEqual(transaction.trans_type, "ADJUST")
        self.assertEqual(transaction.quantity, -6)
        self.assertEqual(transaction.created_by, self.employee)

    def test_cannot_adjust_another_company_product(self):
        product = Product.objects.create(
            company=self.other_company,
            name="Other Company Product",
            current_stock=10,
        )

        response = self.client.post(
            reverse("inventory_adjust", args=[product.pk]),
            {
                "trans_type": "OUT",
                "quantity": "2",
                "notes": "Cross-company access test",
            },
        )

        self.assertEqual(response.status_code, 404)
        product.refresh_from_db()
        self.assertEqual(product.current_stock, 10)
        self.assertFalse(
            StockTransaction.objects.filter(product=product).exists()
        )


# Seven focused tests for supplier and inventory authorization.


class ShipmentPermissionTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Shipment Test Company A",
            short_name="STA",
        )
        self.other_company = Company.objects.create(
            name="Shipment Test Company B",
            short_name="STB",
        )

        self.user = User.objects.create_user(
            username="shipment_test_user",
            password="test-password-only",
        )
        self.employee = Employee.objects.create(
            user=self.user,
            company=self.company,
            role="STAFF",
        )
        self.membership = CompanyMembership.objects.create(
            employee=self.employee,
            company=self.company,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["company_id"] = self.company.pk
        session.save()

        self.set_role("PROC")

    def set_role(self, role):
        MembershipRole.objects.filter(
            membership=self.membership
        ).delete()
        MembershipRole.objects.create(
            membership=self.membership,
            role=role,
        )

    def make_shipment(self, company, suffix):
        tender = Tender.objects.create(
            company=company,
            bid_reference_number="SHIP-REF-" + suffix,
            bid_title="Shipment Test Tender " + suffix,
            purchaser_name="Test Purchaser",
            status="WON",
        )
        return Shipment.objects.create(
            company=company,
            tender=tender,
        )

    def test_shipment_list_is_company_scoped(self):
        own = self.make_shipment(self.company, "OWN")
        self.make_shipment(self.other_company, "OTHER")

        response = self.client.get(reverse("shipment_list"))

        self.assertEqual(response.status_code, 200)
        shipment_ids = set(
            response.context["shipments"].values_list("id", flat=True)
        )
        self.assertEqual(shipment_ids, {own.pk})

    def test_ceo_can_view_but_cannot_update_shipment(self):
        shipment = self.make_shipment(self.company, "CEO")
        self.set_role("CEO")

        list_response = self.client.get(reverse("shipment_list"))
        self.assertEqual(list_response.status_code, 200)

        update_response = self.client.post(
            reverse("shipment_update", args=[shipment.pk]),
            {"status": "AT_PORT"},
        )
        self.assertEqual(update_response.status_code, 403)

        shipment.refresh_from_db()
        self.assertEqual(shipment.current_status, "ON_VESSEL")

    def test_logistics_can_update_shipment(self):
        shipment = self.make_shipment(self.company, "LOG")
        self.set_role("LOG")

        response = self.client.post(
            reverse("shipment_update", args=[shipment.pk]),
            {
                "status": "AT_PORT",
                "vessel_name": "Test Vessel",
                "bill_of_lading": "BL-TEST-001",
                "eta": "2026-12-15",
            },
        )

        self.assertEqual(response.status_code, 302)
        shipment.refresh_from_db()
        self.assertEqual(shipment.current_status, "AT_PORT")
        self.assertEqual(shipment.vessel_name, "Test Vessel")
        self.assertEqual(shipment.bill_of_lading, "BL-TEST-001")
        self.assertEqual(str(shipment.eta), "2026-12-15")

    def test_cannot_update_another_company_shipment(self):
        shipment = self.make_shipment(self.other_company, "CROSS")
        self.set_role("LOG")

        response = self.client.post(
            reverse("shipment_update", args=[shipment.pk]),
            {"status": "RELEASED"},
        )

        self.assertEqual(response.status_code, 404)
        shipment.refresh_from_db()
        self.assertEqual(shipment.current_status, "ON_VESSEL")

    def test_bidm_cannot_inspect_received_goods(self):
        shipment = self.make_shipment(self.company, "BIDM")
        self.set_role("BIDM")

        response = self.client.post(
            reverse("inventory_check", args=[shipment.pk]),
            {
                "item_name": "Test Generator",
                "qty_expected": "10",
                "qty_received": "10",
                "qty_damaged": "0",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(InventoryItem.objects.count(), 0)

    def test_procurement_inspection_records_mixed_condition(self):
        shipment = self.make_shipment(self.company, "MIXED")
        product = Product.objects.create(
            company=self.company,
            name="Existing Test Stock",
            current_stock=20,
        )

        response = self.client.post(
            reverse("inventory_check", args=[shipment.pk]),
            {
                "item_name": "Transformer Oil Drums",
                "qty_expected": "12",
                "qty_received": "10",
                "qty_damaged": "2",
                "notes": "Two drums damaged",
            },
        )

        self.assertEqual(response.status_code, 302)

        item = InventoryItem.objects.get(shipment=shipment)
        self.assertEqual(item.qty_expected, 12)
        self.assertEqual(item.qty_received, 10)
        self.assertEqual(item.qty_damaged, 2)
        self.assertEqual(item.condition, "MIXED")

        product.refresh_from_db()
        self.assertEqual(product.current_stock, 20)
        self.assertFalse(
            StockTransaction.objects.filter(product=product).exists()
        )

    def test_procurement_inspection_records_fully_damaged_goods(self):
        shipment = self.make_shipment(self.company, "DAMAGED")

        response = self.client.post(
            reverse("inventory_check", args=[shipment.pk]),
            {
                "item_name": "Damaged Pump Units",
                "qty_expected": "5",
                "qty_received": "3",
                "qty_damaged": "3",
                "notes": "All received units damaged",
            },
        )

        self.assertEqual(response.status_code, 302)

        item = InventoryItem.objects.get(shipment=shipment)
        self.assertEqual(item.condition, "DAMAGED")
        self.assertEqual(item.qty_received - item.qty_damaged, 0)


# Seven focused shipment permission and inspection tests.

class LogisticsExecutionWorkflowTests(TestCase):
    """Exercise I11, I12 and I16 through the real task-action view."""

    def setUp(self):
        self.temp_media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.temp_media.name)
        self.media_override.enable()

        self.company = Company.objects.create(
            name="Logistics Workflow Test Company",
            short_name="LWTC",
        )

        self.user = User.objects.create_user(
            username="logistics_workflow_test",
            password="test-password-only",
        )
        self.employee = Employee.objects.create(
            user=self.user,
            company=self.company,
            role="STAFF",
        )
        self.membership = CompanyMembership.objects.create(
            employee=self.employee,
            company=self.company,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["company_id"] = self.company.pk
        session.save()

    def tearDown(self):
        self.media_override.disable()
        self.temp_media.cleanup()
        super().tearDown()

    def make_tender(self, suffix, status="WON"):
        return Tender.objects.create(
            company=self.company,
            serial_number="LW-" + suffix,
            bid_reference_number="LW-REF-" + suffix,
            bid_title="Logistics Workflow " + suffix,
            purchaser_name="Test Purchaser",
            status=status,
        )

    def make_task(self, tender, code, assigned_to=None, status="PENDING"):
        return Task.objects.create(
            company=self.company,
            code=code,
            title="Test " + code,
            kind="CHAIN",
            assigned_to=assigned_to or self.employee,
            status=status,
            payload={"tender_id": tender.pk},
        )

    def upload(self, filename):
        return SimpleUploadedFile(
            filename,
            b"%PDF-1.4 test document",
            content_type="application/pdf",
        )

    def submit(self, task, action, data=None, files=None):
        payload = {"action": action}
        if data:
            payload.update(data)
        if files:
            payload.update(files)

        return self.client.post(
            reverse("task_action", args=[task.pk]),
            payload,
        )

    def test_i11_partial_damage_adds_only_usable_stock(self):
        tender = self.make_tender("I11MIX")
        task = self.make_task(tender, "I11")
        self.make_task(tender, "I12")

        response = self.submit(
            task,
            "INSPECTION_DONE",
            {
                "item_name": "Test Generator",
                "qty_expected": "12",
                "qty_received": "10",
                "qty_damaged": "2",
                "inspection_notes": "Two units damaged",
            },
            {"inspection_report": self.upload("inspection.pdf")},
        )

        self.assertEqual(response.status_code, 302)

        task.refresh_from_db()
        self.assertEqual(task.status, "COMPLETED")

        shipment = Shipment.objects.get(tender=tender, company=self.company)
        item = InventoryItem.objects.get(shipment=shipment)

        self.assertEqual(item.qty_expected, 12)
        self.assertEqual(item.qty_received, 10)
        self.assertEqual(item.qty_damaged, 2)
        self.assertEqual(item.condition, "MIXED")

        product = Product.objects.get(
            company=self.company,
            name="Test Generator",
        )
        self.assertEqual(product.current_stock, 8)

        stock_entry = StockTransaction.objects.get(product=product)
        self.assertEqual(stock_entry.trans_type, "IN")
        self.assertEqual(stock_entry.quantity, 8)
        self.assertEqual(stock_entry.created_by, self.employee)

        self.assertTrue(
            Task.objects.filter(
                company=self.company,
                code="I12",
                payload__tender_id=tender.pk,
            ).exists()
        )

    def test_i11_fully_damaged_goods_block_delivery(self):
        tender = self.make_tender("I11BAD")
        task = self.make_task(tender, "I11")

        response = self.submit(
            task,
            "INSPECTION_DONE",
            {
                "item_name": "Damaged Pump",
                "qty_expected": "5",
                "qty_received": "3",
                "qty_damaged": "3",
                "inspection_notes": "All received units damaged",
            },
            {"inspection_report": self.upload("damaged.pdf")},
        )

        self.assertEqual(response.status_code, 302)

        task.refresh_from_db()
        self.assertEqual(task.status, "IN_PROGRESS")
        self.assertIsNone(task.completed_at)

        shipment = Shipment.objects.get(tender=tender, company=self.company)
        item = InventoryItem.objects.get(shipment=shipment)

        self.assertEqual(item.condition, "DAMAGED")
        self.assertEqual(item.qty_received, 3)
        self.assertEqual(item.qty_damaged, 3)

        self.assertFalse(
            Task.objects.filter(
                company=self.company,
                code="I12",
                payload__tender_id=tender.pk,
            ).exists()
        )
        self.assertFalse(
            Product.objects.filter(
                company=self.company,
                name="Damaged Pump",
            ).exists()
        )
        self.assertTrue(
            TaskHistory.objects.filter(
                task_code="I11",
                action_taken="INSPECTION_BLOCKED",
                company=self.company,
            ).exists()
        )

    def test_i12_delivery_reduces_stock_and_records_out_transaction(self):
        tender = self.make_tender("I12OK")
        task = self.make_task(tender, "I12")
        self.make_task(tender, "I13")

        product = Product.objects.create(
            company=self.company,
            name="Delivery Test Product",
            current_stock=10,
        )

        response = self.submit(
            task,
            "DELIVERED",
            {
                "product_id": str(product.pk),
                "qty_delivered": "4",
            },
            {"delivery_note": self.upload("delivery.pdf")},
        )

        self.assertEqual(response.status_code, 302)

        task.refresh_from_db()
        product.refresh_from_db()

        self.assertEqual(task.status, "COMPLETED")
        self.assertEqual(product.current_stock, 6)

        stock_entry = StockTransaction.objects.get(product=product)
        self.assertEqual(stock_entry.trans_type, "OUT")
        self.assertEqual(stock_entry.quantity, 4)
        self.assertEqual(stock_entry.created_by, self.employee)

        tender.refresh_from_db()
        self.assertTrue(tender.delivery_note)

    def test_i12_insufficient_stock_does_not_change_inventory(self):
        tender = self.make_tender("I12LOW")
        task = self.make_task(tender, "I12")
        self.make_task(tender, "I13")

        product = Product.objects.create(
            company=self.company,
            name="Low Stock Product",
            current_stock=2,
        )

        response = self.submit(
            task,
            "DELIVERED",
            {
                "product_id": str(product.pk),
                "qty_delivered": "4",
            },
            {"delivery_note": self.upload("delivery-low.pdf")},
        )

        self.assertEqual(response.status_code, 302)

        task.refresh_from_db()
        product.refresh_from_db()

        self.assertEqual(task.status, "PENDING")
        self.assertEqual(product.current_stock, 2)
        self.assertFalse(
            StockTransaction.objects.filter(
                product=product,
                trans_type="OUT",
            ).exists()
        )

    def test_i16_closure_preserves_won_status_without_fake_revenue(self):
        tender = self.make_tender("I16OK", status="WON")
        task = self.make_task(tender, "I16")
        cash_entries_before = CashFlowEntry.objects.count()

        response = self.submit(task, "PROJECT_CLOSED")

        self.assertEqual(response.status_code, 302)

        task.refresh_from_db()
        tender.refresh_from_db()

        self.assertEqual(task.status, "COMPLETED")
        self.assertEqual(tender.status, "WON")
        self.assertEqual(CashFlowEntry.objects.count(), cash_entries_before)

    def test_duplicate_i16_submission_is_rejected(self):
        tender = self.make_tender("I16DUP", status="WON")
        task = self.make_task(tender, "I16")

        self.submit(task, "PROJECT_CLOSED")

        task.refresh_from_db()
        self.assertEqual(task.status, "COMPLETED")

        history_before = TaskHistory.objects.filter(
            task_code="I16",
            company=self.company,
        ).count()

        self.submit(task, "PROJECT_CLOSED")

        task.refresh_from_db()
        history_after = TaskHistory.objects.filter(
            task_code="I16",
            company=self.company,
        ).count()

        self.assertEqual(task.status, "COMPLETED")
        self.assertEqual(history_after, history_before)
        self.assertEqual(history_after, 1)

    def test_task_assigned_to_another_employee_cannot_be_submitted(self):
        tender = self.make_tender("ASSIGN")
        other_user = User.objects.create_user(
            username="another_logistics_employee",
            password="test-password-only",
        )
        other_employee = Employee.objects.create(
            user=other_user,
            company=self.company,
            role="STAFF",
        )
        task = self.make_task(
            tender,
            "I16",
            assigned_to=other_employee,
        )

        response = self.submit(task, "PROJECT_CLOSED")

        self.assertEqual(response.status_code, 404)

        task.refresh_from_db()
        self.assertEqual(task.status, "PENDING")
