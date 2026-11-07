from django.utils import timezone
from django.contrib.auth.models import User
from django.db import models


class Company(models.Model):
    name = models.CharField(max_length=200, unique=True)
    short_name = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Companies'

    def __str__(self):
        return self.short_name or self.name


ROLE_CHOICES = [
    ('ADMIN', 'System Admin'),
    ('IT', 'IT Coordinator'),
    ('CEO', 'Chief Executive Officer'),
    ('GM', 'General Manager'),
    ('VGM', 'Vice General Manager'),
    ('BIDM', 'Bid Manager'),
    ('CASE', 'Case Executive'),
    ('FIN', 'Finance Manager'),
    ('HR', 'HR Manager'),
    ('OFFICE', 'Office Assistant'),
    ('BIDOP', 'Bid Operator'),
    ('LEGAL', 'Legal Counselor'),
    ('PROC', 'Procurement & Import Officer'),
    ('LOG', 'Logistics & Delivery Officer'),
    ('STAFF', 'Staff'),
]


class Employee(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='employee')
    company = models.ForeignKey(Company, null=True, blank=True,
                                on_delete=models.PROTECT, related_name='employees')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='STAFF')
    phone = models.CharField(max_length=30, blank=True)
    telegram_chat_id = models.CharField(max_length=60, blank=True)
    telegram_id = models.CharField(max_length=50, blank=True, null=True, unique=True, help_text='Telegram User ID')
    base_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    contract_expiry = models.DateField(null=True, blank=True)
    whatsapp_number = models.CharField(max_length=30, blank=True)
    is_group_level = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return '%s (%s)' % (self.full_name, self.get_role_display())

    @property
    def full_name(self):
        return self.user.get_full_name() or self.user.username


class Task(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('REJECTED', 'Rejected'),
        ('CANCELLED', 'Cancelled'),
    ]
    KIND_CHOICES = [
        ('CHAIN', 'Chain Task'),
        ('ASSIGNED', 'Assigned Task'),
        ('ROUTINE', 'Routine Task'),
        ('MEMO', 'Memo'),
    ]
    code = models.CharField(max_length=20, blank=True)
    title = models.CharField(max_length=250)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default='CHAIN')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='tasks')
    assigned_to = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='tasks')
    assigned_by = models.ForeignKey(Employee, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='created_tasks')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    priority = models.CharField(max_length=10, default='NORMAL')
    due_date = models.DateField(null=True, blank=True)
    action_url = models.CharField(max_length=300, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    uploaded_file = models.FileField(upload_to='task_files/', blank=True, null=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return '[%s] %s' % (self.code or 'TASK', self.title)


class Notification(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL)
    link = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    sent_telegram = models.BooleanField(default=False)
    sent_whatsapp = models.BooleanField(default=False)
    sent_email = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class AuditLog(models.Model):
    actor = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL)
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100)
    detail = models.TextField(blank=True)
    object_type = models.CharField(max_length=60, blank=True)
    object_id = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return '%s - %s' % (self.action, self.created_at)


class Tender(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('REGISTERED', 'Registered'),
        ('GO', 'Go - Bid Manager Approved'),
        ('NO_GO', 'No-Go'),
        ('CEO_APPROVED', 'CEO Approved'),
        ('CEO_REJECTED', 'CEO Rejected'),
        ('IN_PROGRESS', 'In Progress'),
        ('SUBMITTED', 'Submitted'),
        ('WON', 'Won'),
        ('LOST', 'Lost'),
        ('CANCELLED', 'Cancelled'),
    ]
    BID_TYPE_CHOICES = [('EGP', 'eGP'), ('DOCUMENT', 'Document')]
    ORG_TYPE_CHOICES = [
        ('GOVERNMENT', 'Government'),
        ('NON_GOVERNMENT', 'Non-Government'),
        ('PRIVATE', 'Private'),
    ]
    COST_MODE_CHOICES = [('CASH', 'Cash'), ('DEPOSIT', 'Deposit in Account')]
    CPO_TYPE_CHOICES = [('AMOUNT', 'Amount'), ('PERCENT', 'Percent')]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='tenders')
    serial_number = models.CharField(max_length=40, unique=True, blank=True)
    bid_reference_number = models.CharField(max_length=150)
    bid_title = models.CharField(max_length=300)
    bid_type = models.CharField(max_length=20, choices=BID_TYPE_CHOICES, default='DOCUMENT')
    purchaser_name = models.CharField(max_length=250)
    organization_type = models.CharField(max_length=30, choices=ORG_TYPE_CHOICES, default='GOVERNMENT')
    published_date = models.DateField(null=True, blank=True)
    bidder_location = models.CharField(max_length=200, blank=True)
    contact_phone = models.CharField(max_length=80, blank=True)
    bid_document_cost_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bid_document_cost_payment_mode = models.CharField(max_length=20, choices=COST_MODE_CHOICES, default='CASH')
    account_holder_name = models.CharField(max_length=200, blank=True)
    bank_name = models.CharField(max_length=200, blank=True)
    bank_account_number = models.CharField(max_length=100, blank=True)
    cpo_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cpo_type = models.CharField(max_length=20, choices=CPO_TYPE_CHOICES, default='AMOUNT')
    bid_submission_date = models.DateField(null=True, blank=True)
    bid_opening_date = models.DateField(null=True, blank=True)
    bid_information_sheet = models.FileField(upload_to='tenders/info_sheets/', blank=True, null=True)
    ai_extracted = models.BooleanField(default=False)
    ai_status = models.CharField(max_length=20, default='PENDING')
    ai_error = models.TextField(blank=True)
    ai_summary = models.TextField(blank=True, help_text='AI Bid Analysis Summary')
    war_room_summary = models.TextField(blank=True)
    war_room_brands = models.TextField(blank=True)
    war_room_suppliers = models.TextField(blank=True)
    war_room_checklist = models.TextField(blank=True)
    final_technical_proposal = models.FileField(upload_to='tenders/tech_proposals/', blank=True, null=True)
    bid_op_notes = models.TextField(blank=True)
    bm_manual_upload = models.FileField(upload_to='tenders/bm_manual/', blank=True, null=True)
    bm_manual_summary = models.FileField(upload_to='tenders/bm_manual_summary/', blank=True, null=True)
    bm_manual_checklist = models.FileField(upload_to='tenders/bm_manual_checklist/', blank=True, null=True)
    bm_manual_brands = models.FileField(upload_to='tenders/bm_manual_brands/', blank=True, null=True)
    bm_manual_suppliers = models.FileField(upload_to='tenders/bm_manual_suppliers/', blank=True, null=True)
    fx_rate = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    profit_margin_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    final_bid_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bank_letter = models.FileField(upload_to='tenders/bank_letters/', blank=True, null=True)
    cpo_document = models.FileField(upload_to='tenders/cpos/', blank=True, null=True)
    physical_submission_status = models.CharField(max_length=100, blank=True)
    competitor_prices = models.TextField(blank=True)
    technical_result = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    registered_by = models.ForeignKey(Employee, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='registered_tenders')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return '%s - %s' % (self.serial_number or 'TND', self.bid_title)


    transport_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    transport_from = models.CharField(max_length=200, blank=True)
    transport_to = models.CharField(max_length=200, blank=True)
    transport_reason = models.TextField(blank=True)
    total_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    receipt = models.FileField(upload_to='tenders/receipts/', blank=True, null=True)
    purchased_bid_document = models.FileField(upload_to='tenders/bid_docs/', blank=True, null=True)


    final_docs_uploaded = models.FileField(upload_to='tenders/final_docs/', blank=True, null=True)
    loss_reason = models.TextField(blank=True, help_text='If lost, why did we lose?')
    ceo_signature = models.TextField(blank=True, null=True)
    bidm_signature = models.TextField(blank=True, null=True)
    final_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    supplier_portal_token = models.CharField(max_length=50, blank=True, null=True, unique=True)
    supplier_name = models.CharField(max_length=200, blank=True)
    submission_status = models.CharField(max_length=50, blank=True)
    tech_result = models.CharField(max_length=50, blank=True)
    fin_result = models.CharField(max_length=50, blank=True)
    competitor_prices = models.TextField(blank=True)
    award_document = models.FileField(upload_to='tenders/awards/', blank=True, null=True)
    draft_agreement = models.FileField(upload_to='tenders/agreements/', blank=True, null=True)


    legal_approved_agreement = models.FileField(upload_to='tenders/legal_agreements/', blank=True, null=True)
    signed_agreement = models.FileField(upload_to='tenders/signed_agreements/', blank=True, null=True)
    final_agreement = models.FileField(upload_to='tenders/final_agreements/', blank=True, null=True)
    po_document = models.FileField(upload_to='import/po/', blank=True, null=True)
    payment_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    lc_swift_copy = models.FileField(upload_to='import/swift/', blank=True, null=True)


    shipping_docs = models.FileField(upload_to='import/shipping/', blank=True, null=True)
    clearing_agent = models.CharField(max_length=200, blank=True)
    duty_tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    customs_docs = models.FileField(upload_to='import/customs/', blank=True, null=True)
    warehouse_receipt = models.FileField(upload_to='import/warehouse/', blank=True, null=True)
    inspection_report = models.FileField(upload_to='import/inspection/', blank=True, null=True)
    delivery_note = models.FileField(upload_to='import/delivery/', blank=True, null=True)
    grn_invoice = models.FileField(upload_to='import/grn/', blank=True, null=True)
    payment_receipt = models.FileField(upload_to='import/payment/', blank=True, null=True)
    closure_report = models.FileField(upload_to='import/closure/', blank=True, null=True)


    bid_tracker = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name='tracked_bids', help_text="Employee assigned to track bid results")
    cpo_expiry = models.DateField(null=True, blank=True)
    performance_bond_expiry = models.DateField(null=True, blank=True)
    advance_payment_bond_expiry = models.DateField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.serial_number and self.company_id:
            prefix = '%s-TND-%s-' % (self.company.short_name or 'CMP', timezone.now().year)
            existing = Tender.objects.filter(
                serial_number__startswith=prefix).values_list('serial_number', flat=True)
            max_n = 0
            for s in existing:
                try:
                    n = int(s[len(prefix):])
                    if n > max_n:
                        max_n = n
                except (ValueError, IndexError):
                    pass
            self.serial_number = '%s%04d' % (prefix, max_n + 1)
        super().save(*args, **kwargs)

# INFINITY_PATCH_03
class TenderReview(models.Model):
    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name='reviews')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    role = models.CharField(max_length=20)
    decision = models.CharField(max_length=20) # GO, NO_GO, APPROVED, REJECTED
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.tender.serial_number} - {self.role} - {self.decision}'


class Memo(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='memos')
    title = models.CharField(max_length=250)
    content = models.TextField()
    sender = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='sent_memos')
    recipient_role = models.CharField(max_length=20, blank=True, help_text="Leave blank to send to everyone")
    recipient_employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.CASCADE, related_name='received_memos')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Document(models.Model):
    CATEGORY_CHOICES = [
        ('TENDER_SRC', 'CAB 01: Tender Sourcing'),
        ('TENDER_PREP', 'CAB 02: Tender Prep & Sub'),
        ('PROCUREMENT', 'CAB 03: Procurement & Import'),
        ('LOGISTICS', 'CAB 04: Logistics & Delivery'),
        ('FINANCE', 'CAB 05: Bank Guarantees & LCs'),
        ('ACCOUNTING', 'CAB 06: Accounting & Payroll'),
        ('LEGAL', 'CAB 07: Legal & Contracts'),
        ('HR', 'CAB 08: Human Resources'),
        ('MAIL', 'CAB 09: Mail & Correspondence'),
        ('ADMIN', 'CAB 10: Corporate Admin'),
        ('GENERAL', 'General / Unfiled')
    ]
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='documents')
    doc_id = models.CharField(max_length=50, unique=True, blank=True)
    title = models.CharField(max_length=250)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='GENERAL')
    binder_name = models.CharField(max_length=200, blank=True, help_text='Specific File Holder / Binder')
    binder_volume = models.PositiveIntegerField(default=1, help_text='Binder Volume (e.g., 1, 2, 3)')
    file = models.FileField(upload_to='documents/')
    tender = models.ForeignKey(Tender, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_by = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL)
    
    # Standard Filing System Hierarchy
    room = models.CharField(max_length=100, blank=True, help_text="e.g., Main Archive Room")
    cabinet = models.CharField(max_length=100, blank=True, help_text="e.g., Cabinet A")
    shelf = models.CharField(max_length=100, blank=True, help_text="e.g., Shelf 2")
    box = models.CharField(max_length=100, blank=True, help_text="e.g., Box 12")
    folder = models.CharField(max_length=100, blank=True, help_text="e.g., Folder 2026")
    
    is_physically_filed = models.BooleanField(default=False)
    checked_out_to = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name='checked_out_docs')
    checkout_date = models.DateField(null=True, blank=True)
    
    expiry_date = models.DateField(null=True, blank=True, help_text="When does this document expire?")
    is_archived = models.BooleanField(default=False, help_text="Moved to long-term storage")
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.doc_id and self.company_id:
            prefix = '%s-DOC-' % (self.company.short_name or 'CMP')
            existing = Document.objects.filter(doc_id__startswith=prefix).values_list('doc_id', flat=True)
            max_n = 0
            for s in existing:
                try:
                    n = int(s[len(prefix):])
                    if n > max_n: max_n = n
                except: pass
            self.doc_id = '%s%04d' % (prefix, max_n + 1)
        super().save(*args, **kwargs)

    def __str__(self):
        return '%s - %s' % (self.doc_id, self.title)

class LeaveRequest(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    leave_type = models.CharField(max_length=50, default='Annual')
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=50, default='PENDING_HR')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee.full_name} - {self.leave_type}"

class ExpenseRequest(models.Model):
    TYPE_CHOICES = [('EXPENSE', 'Expense Reimbursement'), ('LOAN', 'Salary Advance / Loan')]
    
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    request_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='EXPENSE')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    repayment_months = models.PositiveIntegerField(default=1, help_text="For loans only")
    reason = models.TextField()
    status = models.CharField(max_length=50, default='PENDING_FIN')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee.full_name} - {self.amount} ETB"

class Correspondence(models.Model):
    DIRECTION_CHOICES = [('INCOMING', 'Incoming'), ('OUTGOING', 'Outgoing')]
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='correspondence')
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    reference_number = models.CharField(max_length=100, blank=True, help_text="Reference number on the letter")
    letter_date = models.DateField()
    sender_recipient = models.CharField(max_length=250, help_text="Who sent it or who is it going to?")
    subject = models.CharField(max_length=300)
    file = models.FileField(upload_to='correspondence/')
    
    uploaded_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, related_name='uploaded_letters')
    assigned_to = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_letters', help_text="Who should take action?")
    
    reply_deadline = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-letter_date', '-created_at']
        verbose_name_plural = 'Correspondence'

    def __str__(self):
        return f"{self.direction} - {self.subject}"


class LegalCase(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='legal_cases')
    title = models.CharField(max_length=250)
    description = models.TextField()
    status = models.CharField(max_length=50, default='ACTIVE')
    next_hearing_date = models.DateField(null=True, blank=True)
    assigned_lawyer = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class CompanyResource(models.Model):
    CATEGORY_CHOICES = [
        ('MANUAL', 'Working Manual'),
        ('TEMPLATE', 'Template'),
        ('FORM', 'Form'),
        ('OTHER', 'Other')
    ]
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='resources')
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='OTHER')
    file = models.FileField(upload_to='resources/')
    uploaded_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.get_category_display()})"

class CompanySetting(models.Model):
    company = models.OneToOneField(Company, on_delete=models.CASCADE)
    payment_approval_threshold = models.DecimalField(max_digits=14, decimal_places=2, default=50000.00)
    
    def __str__(self):
        return f"{self.company.short_name} Settings"

class CashFlowEntry(models.Model):
    FLOW_CHOICES = [('IN', 'Inflow (Money In)'), ('OUT', 'Outflow (Money Out)')]
    CATEGORY_CHOICES = [
        ('TENDER_COST', 'Tender Cost'),
        ('SUPPLIER', 'Supplier Payment'),
        ('DUTY', 'Duty & Tax'),
        ('CUSTOMER', 'Customer Payment'),
        ('SALARY', 'Salary / Payroll'),
        ('RENT', 'Rent / Utilities'),
        ('OTHER', 'Other')
    ]
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    date = models.DateField(auto_now_add=True)
    flow_type = models.CharField(max_length=10, choices=FLOW_CHOICES)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=250)
    related_tender = models.ForeignKey(Tender, null=True, blank=True, on_delete=models.SET_NULL)
    created_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name_plural = "Cash Flow Entries"

    def __str__(self):
        return f"{self.flow_type} - {self.amount} ({self.category})"


class EmployeeDocument(models.Model):
    registry_doc = models.OneToOneField(Document, null=True, blank=True, on_delete=models.SET_NULL)
    DOC_TYPES = [('CV', 'CV / Resume'), ('CONTRACT', 'Employment Contract'), ('CERT', 'Certificate / Degree'), ('OTHER', 'Other')]
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='documents')
    doc_type = models.CharField(max_length=20, choices=DOC_TYPES)
    title = models.CharField(max_length=150)
    file = models.FileField(upload_to='hr_docs/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee.full_name} - {self.title}"


class PayrollRecord(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    month = models.CharField(max_length=20) # e.g., "November"
    year = models.IntegerField()
    total_amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, default='PENDING_FIN')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.company.short_name} - {self.month} {self.year} Payroll"


class PerformanceReview(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='reviews')
    reviewer = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, related_name='given_reviews')
    rating = models.IntegerField(default=5) # 1 to 5 stars
    feedback = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee.full_name} - {self.rating}/5"


class CourierDispatch(models.Model):
    STATUS_CHOICES = [('DISPATCHED', 'Dispatched'), ('DELIVERED', 'Delivered'), ('RETURNED', 'Returned')]
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    tender = models.ForeignKey(Tender, null=True, blank=True, on_delete=models.SET_NULL)
    courier_name = models.CharField(max_length=150)
    tracking_number = models.CharField(max_length=100, blank=True)
    recipient = models.CharField(max_length=200)
    dispatch_date = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DISPATCHED')
    logged_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True)

class SupplyRequest(models.Model):
    STATUS_CHOICES = [('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('FULFILLED', 'Fulfilled')]
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='supply_requests')
    item_name = models.CharField(max_length=200)
    quantity = models.IntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)

class VisitorLog(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    visitor_name = models.CharField(max_length=150)
    visitor_company = models.CharField(max_length=150, blank=True)
    visiting_employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    purpose = models.CharField(max_length=200)
    check_in_time = models.DateTimeField(auto_now_add=True)
    check_out_time = models.DateTimeField(null=True, blank=True)

class Supplier(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    rating = models.IntegerField(default=5, help_text="1 to 5 stars")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Shipment(models.Model):
    STATUS_CHOICES = [
        ('ON_VESSEL', 'On Vessel'),
        ('AT_PORT', 'At Port'),
        ('CUSTOMS', 'In Customs'),
        ('RELEASED', 'Released'),
        ('WAREHOUSE', 'At Warehouse')
    ]
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    tender = models.OneToOneField(Tender, on_delete=models.CASCADE, related_name='shipment')
    vessel_name = models.CharField(max_length=150, blank=True)
    bill_of_lading = models.CharField(max_length=100, blank=True)
    eta = models.DateField(null=True, blank=True)
    current_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ON_VESSEL')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Shipment for {self.tender.serial_number}"

class InventoryItem(models.Model):
    STATUS_CHOICES = [('GOOD', 'Good Condition'), ('DAMAGED', 'Damaged')]
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name='items')
    item_name = models.CharField(max_length=200)
    qty_expected = models.IntegerField(default=1)
    qty_received = models.IntegerField(default=0)
    condition = models.CharField(max_length=20, choices=STATUS_CHOICES, default='GOOD')
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.item_name} ({self.qty_received}/{self.qty_expected})"

class ITTicket(models.Model):
    STATUS_CHOICES = [('PENDING', 'Pending'), ('RESOLVED', 'Resolved')]
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='it_tickets')
    subject = models.CharField(max_length=200)
    description = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee.full_name} - {self.subject}"

class Product(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    sku = models.CharField(max_length=50, blank=True)
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True)
    current_stock = models.IntegerField(default=0)
    min_stock_level = models.IntegerField(default=5)
    location = models.CharField(max_length=100, blank=True, help_text="e.g., Aisle 2, Rack B")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.current_stock} in stock)"

class StockTransaction(models.Model):
    TYPE_CHOICES = [('IN', 'Stock In'), ('OUT', 'Stock Out'), ('ADJUST', 'Manual Adjustment')]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='transactions')
    trans_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    quantity = models.IntegerField()
    notes = models.CharField(max_length=250, blank=True)
    created_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.trans_type} - {self.quantity} {self.product.name}"


class TaskComment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(Employee, on_delete=models.CASCADE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.author.full_name} on {self.task.code}"

class ChatRoom(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    is_group = models.BooleanField(default=False)
    participants = models.ManyToManyField(Employee, related_name='chat_rooms')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class ChatMessage(models.Model):
    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(Employee, on_delete=models.CASCADE)
    text = models.TextField(blank=True)
    audio_file = models.FileField(upload_to='chat_audio/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender.full_name} - {self.created_at}"


class PasswordResetCode(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee.user.username} - {self.code}"


class TenderLot(models.Model):
    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name='lots')
    lot_name = models.CharField(max_length=100, help_text="e.g. Lot 1, Lot 2, or NIB/29/2026/27")
    description = models.CharField(max_length=300, blank=True)
    cpo_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    submission_date = models.DateField(null=True, blank=True)
    opening_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.tender.serial_number} - {self.lot_name}"
