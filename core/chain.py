from django.urls import reverse
from django.db import transaction
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from .models import Employee, Task, Tender, TenderReview, Document, CompanySetting, CashFlowEntry, Shipment, CompanyRoleAssignment, Correspondence, InventoryItem, Product, StockTransaction
from .services import notify, log_task_history
from decimal import Decimal

def find_role(company, role):
    """Return the designated active employee for a role in this company.

    Routing is strictly company-scoped. An employee's legacy global role
    is never used to determine workflow recipients.
    """
    if company is None or not getattr(company, 'is_active', False):
        return None

    assignment = (
        CompanyRoleAssignment.objects
        .select_related('membership__employee__user')
        .filter(
            company=company,
            role=role,
            membership__company=company,
            membership__is_active=True,
            membership__employee__is_active=True,
            membership__roles__role=role,
        )
        .first()
    )

    if assignment is None:
        return None

    return assignment.membership.employee

def create_task(company, code, role, title, description='', payload=None, action_url=''):
    assignee = find_role(company, role)
    if assignee is None:
        return None
    task = Task.objects.create(
        code=code, title=title, description=description, kind='CHAIN',
        company=company, assigned_to=assignee, payload=payload or {}, action_url=action_url,
    )
    notify(assignee, title='New task: %s' % title, body=description[:200],
           task=task, link=action_url)
    return task


def register_document(tender, file_obj, title, employee, category='TENDER'):
    if not file_obj: return
    Document.objects.create(
        company=tender.company,
        title=f"{tender.serial_number} - {title}",
        file=file_obj,
        tender=tender,
        uploaded_by=employee,
        category=category
    )

def advance_tender_chain(request, tender, task, decision, note):
    """Record T02/T03 decisions only for the authorized company membership."""

    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    expected_roles = {
        'T02': 'BIDM',
        'T03': 'CEO',
    }
    allowed_decisions = {
        'T02': ('GO', 'NO_GO'),
        'T03': ('APPROVED', 'REJECTED'),
    }

    expected_role = expected_roles.get(task.code)

    if (
        emp is None
        or company is None
        or membership is None
        or expected_role is None
        or not membership.is_active
        or not company.is_active
        or membership.employee_id != emp.id
        or membership.company_id != company.id
        or task.company_id != company.id
        or task.assigned_to_id != emp.id
        or tender.company_id != company.id
        or decision not in allowed_decisions[task.code]
    ):
        raise PermissionDenied("You are not authorized to approve this tender task.")

    if not membership.roles.filter(role=expected_role).exists():
        raise PermissionDenied("Your company membership does not hold the required role.")

    designated = CompanyRoleAssignment.objects.filter(
        company=company,
        role=expected_role,
        membership=membership,
    ).exists()

    if not designated:
        raise PermissionDenied("You are not the designated approver for this company role.")

    next_role = None
    if task.code == 'T02' and decision == 'GO':
        next_role = 'CEO'
    elif task.code == 'T03' and decision == 'APPROVED':
        next_role = 'CASE'

    if next_role and find_role(company, next_role) is None:
        return (
            "Cannot continue: no active employee is assigned to the "
            "%s workflow role. Assign the role in Manage Users first."
            % next_role
        )

    TenderReview.objects.create(
        tender=tender,
        employee=emp,
        role=expected_role,
        decision=decision,
        note=note,
    )

    task.status = 'COMPLETED'
    task.save()
    log_task_history(task, decision, note, emp)

    if task.code == 'T02':
        if decision == 'GO':
            tender.status = 'GO'
            tender.save()
            create_task(
                tender.company, 'T03', 'CEO',
                f'CEO Approval: {tender.bid_title}',
                f'Bid Manager marked GO. Note: {note}',
                {'tender_id': tender.id},
                reverse('tender_detail', args=[tender.id])
            )
        else:
            tender.status = 'NO_GO'
            tender.save()

    elif task.code == 'T03':
        if decision == 'APPROVED':
            tender.status = 'CEO_APPROVED'
            tender.save()
            create_task(
                tender.company, 'T04', 'CASE',
                f'Cost Request: {tender.bid_title}',
                f'CEO Approved. Note: {note}',
                {'tender_id': tender.id},
                reverse('tender_detail', args=[tender.id])
            )
        else:
            tender.status = 'CEO_REJECTED'
            tender.save()


def process_cost_request(tender, task, action, note):
    next_roles = {
        'SUBMIT_COST': 'FIN',
        'FIN_APPROVE': (
            'CEO' if tender.total_cost >= Decimal('2000.00') else 'CASE'
        ),
        'CEO_COST_APPROVE': 'CASE',
        'RECEIPT_UPLOADED': 'OFFICE',
    }
    next_role = next_roles.get(action)
    if next_role and find_role(tender.company, next_role) is None:
        return (
            "Cannot continue: no active employee is assigned to the "
            "%s workflow role. Assign the role in Manage Users first."
            % next_role
        )

    task.status = 'COMPLETED'
    task.save()
    log_task_history(task, action, note, task.assigned_to)

    if action == 'SUBMIT_COST':
        create_task(tender.company, 'T05', 'FIN',
                    f'Cost Approval: {tender.bid_title}',
                    f'Total requested: {tender.total_cost} ETB. Note: {note}',
                    {'tender_id': tender.id, 'total_cost': str(tender.total_cost)},
                    reverse('tender_detail', args=[tender.id]))
                    
    elif action == 'FIN_APPROVE':
        if tender.total_cost >= Decimal('2000.00'):
            create_task(tender.company, 'T06', 'CEO',
                        f'High Cost Approval: {tender.bid_title}',
                        f'Amount: {tender.total_cost} ETB is >= 2000. Finance Note: {note}',
                        {'tender_id': tender.id},
                        reverse('tender_detail', args=[tender.id]))
        else:
            create_task(tender.company, 'T07', 'CASE',
                        f'Purchase Document: {tender.bid_title}',
                        f'Finance Approved. Note: {note}',
                        {'tender_id': tender.id},
                        reverse('tender_detail', args=[tender.id]))

    elif action == 'CEO_COST_APPROVE':
        create_task(tender.company, 'T07', 'CASE',
                    f'Purchase Document: {tender.bid_title}',
                    f'CEO Approved Cost. Note: {note}',
                    {'tender_id': tender.id},
                    reverse('tender_detail', args=[tender.id]))

    elif action == 'RECEIPT_UPLOADED':
        create_task(tender.company, 'T08', 'OFFICE',
                    f'Upload Bid Document: {tender.bid_title}',
                    f'Receipt uploaded by Case Exec.',
                    {'tender_id': tender.id},
                    reverse('tender_detail', args=[tender.id]))

def trigger_ai_tasks(tender):
    # Now only triggers the Master T09 Task for the Bid Manager
    company = tender.company
    t_id = tender.id
    url = reverse('tender_detail', args=[t_id])
    
    create_task(company, 'T09', 'BIDM',
                f'BID Manager War Room Review: {tender.bid_title}',
                'Review AI findings, edit the compliance matrix, verify suppliers and brands, and approve or reject each tender lot individually.',
                {'tender_id': t_id}, url)

def route_t09_tasks(tender, task, action, note, data, files):
    task.status = 'COMPLETED'
    task.save()
    log_task_history(task, 'ROUTED_TASKS', note, task.assigned_to)
    url = reverse('tender_detail', args=[tender.id])
    
    # Save the manual notes/AI summary to the tender
    if note:
        tender.ai_summary = note
        tender.save()

    # Create T10 (Finance)
    if data.get('route_fin'):
        create_task(tender.company, 'T10', 'FIN', 
                    f'Financial Standing Forms: {tender.bid_title}',
                    'Upload the completed Financial Standing documents.',
                    {'tender_id': tender.id}, url)

    # Create T11 (HR)
    if data.get('route_hr'):
        create_task(tender.company, 'T11', 'HR', 
                    f'Workers Information Forms: {tender.bid_title}',
                    'Upload the completed CVs and Workers Information forms.',
                    {'tender_id': tender.id}, url)

    # Create T12 (Bid Operator)
    if data.get('route_bidop'):
        create_task(tender.company, 'T12', 'BIDOP', 
                    f'Submission Sheet & Compliance: {tender.bid_title}',
                    'Upload the filled Bid Submission Sheet and Compliance Matrix.',
                    {'tender_id': tender.id}, url)

def advance_supplier_chain(tender, task, action, note, data, files):
    task.status = 'COMPLETED'
    task.save()
    log_task_history(task, action, note, task.assigned_to)
    url = reverse('tender_detail', args=[tender.id])

    if action == 'APPROVE_AI':
        create_task(tender.company, 'T10', 'BIDM', f'Supplier Selection & Proforma: {tender.bid_title}', 'Select brands, register supplier, upload Proforma Invoice.', {'tender_id': tender.id}, url)
    
    elif action == 'SUPPLIER_SELECTED':
        create_task(tender.company, 'T11', 'BIDOP', f'Prepare Technical Proposal: {tender.bid_title}', 'Prepare compliance matrix and technical proposal.', {'tender_id': tender.id}, url)
        
    elif action == 'TECH_PROPOSAL_READY':
        create_task(tender.company, 'T12', 'CEO', f'FX Rate & Landed Cost: {tender.bid_title}', 'Decide FX rate and calculate landed cost.', {'tender_id': tender.id}, url)
        
    elif action == 'FX_APPROVED':
        create_task(tender.company, 'T13', 'FIN', f'Prepare Bank Letter: {tender.bid_title}', 'Prepare bid security summary and bank letter.', {'tender_id': tender.id}, url)
        
    elif action == 'BANK_LETTER_READY':
        create_task(tender.company, 'T14', 'OFFICE', f'Upload Bank Letter: {tender.bid_title}', 'Upload the bank letter to outgoing correspondence.', {'tender_id': tender.id}, url)
        
    elif action == 'LETTER_UPLOADED':
        create_task(tender.company, 'T15', 'CASE', f'Upload CPO / Guarantee: {tender.bid_title}', 'Upload CPO and hold physical copy.', {'tender_id': tender.id}, url)
        
    elif action == 'CPO_UPLOADED':
        create_task(tender.company, 'T16', 'CEO', f'Final Profit Margin & Price Lock: {tender.bid_title}', 'Enter final bid price and lock it.', {'tender_id': tender.id}, url)
        
    elif action == 'FINAL_PRICE_LOCKED':
        create_task(tender.company, 'T17', 'BIDOP', f'Upload Final Digital Docs: {tender.bid_title}', 'Upload final scanned technical and financial docs.', {'tender_id': tender.id}, url)

def advance_closing_chain(tender, task, action, note, data, files):
    task.status = 'COMPLETED'
    task.save()
    log_task_history(task, action, note, task.assigned_to)
    url = reverse('tender_detail', args=[tender.id])

    if action == 'FINAL_DOCS_UPLOADED':
        create_task(tender.company, 'T18', 'CASE', f'Physical Submission & Results: {tender.bid_title}', 'Submit the document physically and report the results.', {'tender_id': tender.id}, url)
        
    elif action == 'RESULTS_REPORTED':
        # T19 is just a summary task for the Bid Manager
        create_task(tender.company, 'T19', 'BIDM', f'Bid Results Summary: {tender.bid_title}', 'Review the submission results and competitor prices.', {'tender_id': tender.id}, url)
        # T20 is for the Office Assistant to upload the award
        create_task(tender.company, 'T20', 'OFFICE', f'Upload Award Document: {tender.bid_title}', 'Upload the award document if won.', {'tender_id': tender.id}, url)
        
    elif action == 'SUMMARY_REVIEWED':
        # T19 just gets marked complete
        pass
        
    elif action == 'AWARD_UPLOADED':
        tender.status = 'WON'
        tender.save()
        create_task(tender.company, 'T21', 'OFFICE', f'Upload Draft Agreement: {tender.bid_title}', 'Upload the draft agreement for legal review.', {'tender_id': tender.id}, url)

    elif action == 'DRAFT_AGREEMENT_UPLOADED':
        create_task(tender.company, 'T22', 'LEGAL', f'Review Draft Agreement: {tender.bid_title}', 'Review and approve the draft agreement.', {'tender_id': tender.id}, url)

def advance_import_chain(tender, task, action, note, data, files):
    task.status = 'COMPLETED'
    task.save()
    log_task_history(task, action, note, task.assigned_to)
    url = reverse('tender_detail', args=[tender.id])

    if action == 'LEGAL_APPROVED':
        create_task(tender.company, 'T23', 'BIDM', f'Sign Agreement: {tender.bid_title}', 'The Legal team approved the draft. Please sign it.', {'tender_id': tender.id}, url)
        
    elif action == 'AGREEMENT_SIGNED':
        create_task(tender.company, 'T24', 'OFFICE', f'Upload Final Signed Agreement: {tender.bid_title}', 'Upload the fully signed agreement to kick off the Import phase.', {'tender_id': tender.id}, url)
        
    elif action == 'FINAL_AGREEMENT_UPLOADED':
        # Transition to Import
        create_task(tender.company, 'I01', 'PROC', f'Issue PO & Confirm Supplier: {tender.bid_title}', 'Issue Purchase Order and upload it here.', {'tender_id': tender.id}, url)
        
    elif action == 'PO_ISSUED':
        create_task(tender.company, 'I02', 'FIN', f'Prepare Payment Request: {tender.bid_title}', 'Prepare payment request based on the PO.', {'tender_id': tender.id}, url)
        
    elif action == 'PAYMENT_REQUESTED':
        # Check threshold
        setting, _ = CompanySetting.objects.get_or_create(company=tender.company)
        threshold = setting.payment_approval_threshold
        
        if tender.payment_amount > threshold:
            create_task(tender.company, 'I03', 'CEO', f'Approve Payment (>{threshold} ETB): {tender.bid_title}', f'Amount: {tender.payment_amount}. Exceeds threshold. CEO Approval required.', {'tender_id': tender.id}, url)
        else:
            # Auto-approve and move to LC
            create_task(tender.company, 'I04', 'FIN', f'Open LC (Auto-Approved): {tender.bid_title}', f'Amount: {tender.payment_amount} is below threshold. Proceed to bank.', {'tender_id': tender.id}, url)
        
    elif action == 'PAYMENT_APPROVED':
        create_task(tender.company, 'I04', 'FIN', f'Open LC / Bank Transfer: {tender.bid_title}', 'Execute the bank transfer or LC opening and upload the SWIFT copy.', {'tender_id': tender.id}, url)

def advance_logistics_chain(tender, task, action, note, data, files):
    task.status = 'COMPLETED'
    task.save()
    log_task_history(task, action, note, task.assigned_to)
    url = reverse('tender_detail', args=[tender.id])

    if action == 'SHIPPING_DOCS_UPLOADED':
        create_task(tender.company, 'I06', 'LOG', f'Assign Clearing Agent: {tender.bid_title}', 'Assign a freight forwarder or clearing agent.', {'tender_id': tender.id}, url)
    elif action == 'AGENT_ASSIGNED':
        create_task(tender.company, 'I06G', 'GM', f'GM Approve Agent Fees: {tender.bid_title}', 'Review the Clearing Agent assignment and approve their operational fees before Finance pays them.', {'tender_id': tender.id}, url)
        
    elif action == 'GM_AGENT_APPROVED':
        create_task(tender.company, 'I07', 'FIN', f'Duty & Tax Payment Request: {tender.bid_title}', 'Prepare the customs duty and tax payment request.', {'tender_id': tender.id}, url)
    elif action == 'DUTY_REQUESTED':
        create_task(tender.company, 'I08', 'CEO', f'Approve Duty & Tax: {tender.bid_title}', 'Approve the duty and tax payment.', {'tender_id': tender.id}, url)
    elif action == 'DUTY_APPROVED':
        create_task(tender.company, 'I09', 'LOG', f'Customs Clearing: {tender.bid_title}', 'Complete customs declaration and upload clearance docs.', {'tender_id': tender.id}, url)
    elif action == 'CUSTOMS_CLEARED':
        create_task(tender.company, 'I10', 'LOG', f'Inland Transport to Warehouse: {tender.bid_title}', 'Transport goods to warehouse and upload receipt.', {'tender_id': tender.id}, url)
    elif action == 'WAREHOUSE_RECEIVED':
        create_task(tender.company, 'I11', 'PROC', f'Receiving & Inspection: {tender.bid_title}', 'Inspect goods and upload inspection report.', {'tender_id': tender.id}, url)
    elif action == 'INSPECTION_DONE':
        create_task(tender.company, 'I12', 'LOG', f'Deliver to Customer: {tender.bid_title}', 'Deliver to customer and upload signed delivery note.', {'tender_id': tender.id}, url)
    elif action == 'DELIVERED':
        create_task(tender.company, 'I13', 'CASE', f'Customer Acceptance (GRN): {tender.bid_title}', 'Get customer acceptance and upload GRN + Invoice.', {'tender_id': tender.id}, url)
    elif action == 'GRN_RECEIVED':
        create_task(tender.company, 'I14', 'FIN', f'Receivable & Collection: {tender.bid_title}', 'Collect payment from customer and upload receipt.', {'tender_id': tender.id}, url)
    elif action == 'PAYMENT_COLLECTED':
        create_task(tender.company, 'I15', 'BIDM', f'Project Closure Report: {tender.bid_title}', 'Write final project closure report.', {'tender_id': tender.id}, url)
    elif action == 'CLOSURE_REPORTED':
        create_task(tender.company, 'I16', 'CEO', f'Final Project Approval: {tender.bid_title}', 'Approve project closure.', {'tender_id': tender.id}, url)
    elif action == 'PROJECT_CLOSED':
        tender.status = 'COMPLETED'
        tender.save()
        
        # Automatically log the final customer revenue into the Cash Flow Ledger
        from .models import CashFlowEntry
        # Calculate total revenue based on the lots
        total_revenue = sum(lot.final_price for lot in tender.lots.all() if lot.final_price > 0)
        
        if total_revenue > 0:
            CashFlowEntry.objects.create(
                company=tender.company,
                flow_type='IN',
                category='CUSTOMER',
                amount=total_revenue,
                description=f'Final Revenue Collection for {tender.serial_number}',
                related_tender=tender,
                created_by=task.assigned_to
            )


def advance_submission_chain(tender, task, action, note, data, files):
    task.status = 'COMPLETED'
    task.save()
    log_task_history(task, action, note, task.assigned_to)
    url = reverse('tender_detail', args=[tender.id])

    if action == 'BANK_LETTER_READY':
        # T14 -> T15 (Office Asst)
        cpo_exp = data.get('cpo_expiry')
        if cpo_exp:
            from dateutil import parser as date_parser
            tender.cpo_expiry = date_parser.parse(cpo_exp).date()
        if 'bank_letter' in files:
            tender.bank_letter = files['bank_letter']
        tender.save()
        
        create_task(tender.company, 'T15', 'OFFICE', 
                    f'Log Outgoing Bank Letter: {tender.bid_title}',
                    'Log the Bank Letter in the Mail Registry and prepare physical dispatch.',
                    {'tender_id': tender.id}, url)
                    
    elif action == 'LETTER_LOGGED':
        # T15 -> T16 (Case Exec)
        create_task(tender.company, 'T16', 'CASE', 
                    f'Upload CPO & Hold Original: {tender.bid_title}',
                    'Upload the received CPO and record its physical shelf location.',
                    {'tender_id': tender.id}, url)
                    
    elif action == 'CPO_UPLOADED':
        # T16 -> T17 (Bid Operator)
        if 'cpo_document' in files:
            tender.cpo_document = files['cpo_document']
            tender.save()
            # Register it in Doc Registry automatically
            register_document(tender, tender.cpo_document, 'Original CPO', task.assigned_to, 'FINANCE')
            
        create_task(tender.company, 'T17', 'BIDOP', 
                    f'Final Sealing & Checklist: {tender.bid_title}',
                    'Print, sign, stamp, and seal all documents. Confirm the checklist to proceed.',
                    {'tender_id': tender.id}, url)
                    
    elif action == 'DOCUMENTS_SEALED':
        # T17 -> T18 (Case Exec)
        create_task(tender.company, 'T18', 'CASE', 
                    f'Physical Submission & Results: {tender.bid_title}',
                    'Go to the purchaser, submit the bid, and report the results.',
                    {'tender_id': tender.id}, url)
                    
    elif action == 'RESULTS_REPORTED':
        # T18 -> T20 (Office Asst - Award Document)
        tender.physical_submission_status = data.get('submission_status', '')
        tender.competitor_prices = data.get('competitor_prices', '')
        tender.technical_result = data.get('technical_result', '')
        tender.save()
        
        create_task(tender.company, 'T20', 'OFFICE', 
                    f'Upload Award Document: {tender.bid_title}',
                    'If the company won, upload the award document to trigger the WON status.',
                    {'tender_id': tender.id}, url)

def trigger_lot_assemblies(tender):
    """When T08 is complete, generate a T10 task for EVERY lot."""
    company = tender.company
    url = reverse('tender_detail', args=[tender.id])
    
    for lot in tender.lots.all():
        create_task(
            company, 'T10_LOT', 'BIDOP',
            f'Assemble Bid: {lot.lot_name} ({tender.serial_number})',
            f'Prepare technical and financial documents for {lot.lot_name}.',
            {'tender_id': tender.id, 'lot_id': lot.id}, url
        )



def advance_tender_execution_chain(tender, task, action, note, data, files, actor):
    """
    Process T14-I16 tender execution tasks.

    Validate submitted information and successor-role availability before
    completing the current task. All database changes are atomic.
    """

    expected_actions = {
        'T14': 'LETTER_UPLOADED',
        'T15': 'CPO_UPLOADED',
        'T16': 'FINAL_PRICE_LOCKED',
        'T17': 'FINAL_DOCS_UPLOADED',
        'T18': 'RESULTS_REPORTED',
        'T19': 'SUMMARY_REVIEWED',
        'T20': 'AWARD_UPLOADED',
        'T21': 'DRAFT_AGREEMENT_UPLOADED',
        'T22': 'LEGAL_APPROVED',
        'T23': 'AGREEMENT_SIGNED',
        'T24': 'FINAL_AGREEMENT_UPLOADED',
        'I01': 'PO_ISSUED',
        'I02': 'PAYMENT_REQUESTED',
        'I03': 'PAYMENT_APPROVED',
        'I04': 'LC_OPENED',
        'I05': 'SHIPPING_DOCS_UPLOADED',
        'I06': 'AGENT_ASSIGNED',
        'I06G': 'GM_AGENT_APPROVED',
        'I07': 'DUTY_REQUESTED',
        'I08': 'DUTY_APPROVED',
        'I09': 'CUSTOMS_CLEARED',
        'I10': 'WAREHOUSE_RECEIVED',
        'I11': 'INSPECTION_DONE',
        'I12': 'DELIVERED',
        'I13': 'GRN_RECEIVED',
        'I14': 'PAYMENT_COLLECTED',
        'I15': 'CLOSURE_REPORTED',
        'I16': 'PROJECT_CLOSED',
    }

    if task.code not in expected_actions:
        return "This task is not part of the T14-I16 workflow."

    if action != expected_actions[task.code]:
        return (
            "Invalid action for %s. Expected %s."
            % (task.code, expected_actions[task.code])
        )

    if task.status in ('COMPLETED', 'REJECTED', 'CANCELLED'):
        return "This task has already been closed."

    def required_file(key, label):
        uploaded = files.get(key)
        if not uploaded:
            raise ValueError("%s is required." % label)
        return uploaded

    def required_text(key, label):
        value = str(data.get(key, '') or '').strip()
        if not value:
            raise ValueError("%s is required." % label)
        return value

    def queue_definition(code, role, title, description):
        return {
            'code': code,
            'role': role,
            'title': title,
            'description': description,
        }

    url = reverse('tender_detail', args=[tender.id])
    next_tasks = []

    try:
        # Validate the submitted information and prepare successor tasks.
        if task.code == 'T14':
            bank_file = required_file('bank_letter', 'Bank letter')
            recipient = (tender.purchaser_name or '').strip()
            if not recipient:
                raise ValueError(
                    "The tender purchaser name is missing. "
                    "Complete it before registering the outgoing letter."
                )

            next_tasks.append(queue_definition(
                'T15', 'CASE',
                'Register CPO: %s' % tender.bid_title,
                'Register and upload the bid security CPO.',
            ))

        elif task.code == 'T15':
            cpo_file = required_file('cpo', 'CPO document')
            next_tasks.append(queue_definition(
                'T16', 'CEO',
                'Lock Final Bid Price: %s' % tender.bid_title,
                'Confirm the final bid price and authorized signature.',
            ))

        elif task.code == 'T16':
            final_price = Decimal(required_text(
                'final_price', 'Final bid price'
            ))
            if final_price <= 0:
                raise ValueError("Final bid price must be greater than zero.")

            signature = required_text(
                'signature_data', 'Final price authorization signature'
            )
            next_tasks.append(queue_definition(
                'T17', 'BIDOP',
                'Upload Sealed Final Documents: %s' % tender.bid_title,
                'Upload the final sealed bid documents.',
            ))

        elif task.code == 'T17':
            final_docs = required_file('final_docs', 'Final bid documents')
            sealed = str(data.get('is_sealed', '')).lower()
            if sealed not in ('true', 'on', '1', 'yes'):
                raise ValueError(
                    "Confirm that the final bid documents are sealed."
                )

            next_tasks.append(queue_definition(
                'T18', 'CASE',
                'Report Tender Submission Results: %s' % tender.bid_title,
                'Record submission, technical and financial results.',
            ))

        elif task.code == 'T18':
            submission = required_text(
                'submission_status', 'Submission status'
            )
            technical = required_text('tech_result', 'Technical result')
            financial = required_text('fin_result', 'Financial result')

            if submission not in ('Submitted', 'Rejected'):
                raise ValueError("Invalid submission status.")
            if technical not in ('In Progress', 'Pass', 'Fail'):
                raise ValueError("Invalid technical result.")
            if financial not in ('In Progress', 'Opened'):
                raise ValueError("Invalid financial result.")

            # T19 is always required for management review.
            next_tasks.append(queue_definition(
                'T19', 'BIDM',
                'Review Tender Results: %s' % tender.bid_title,
                'Review and summarize the tender results.',
            ))

            # Do not create an award task for rejected or failed bids.
            if submission != 'Rejected' and technical != 'Fail':
                next_tasks.append(queue_definition(
                    'T20', 'OFFICE',
                    'Upload Award Document: %s' % tender.bid_title,
                    'Upload the official award document if awarded.',
                ))

        elif task.code == 'T19':
            if tender.status == 'LOST':
                required_text('loss_reason', 'Loss reason')

        elif task.code == 'T20':
            if tender.status == 'LOST':
                raise ValueError(
                    "This tender is marked LOST. Award upload is blocked."
                )

            award_file = required_file('award_doc', 'Award document')
            next_tasks.append(queue_definition(
                'T21', 'OFFICE',
                'Upload Draft Agreement: %s' % tender.bid_title,
                'Upload the draft agreement received from the purchaser.',
            ))

        elif task.code == 'T21':
            draft_file = required_file(
                'draft_agreement', 'Draft agreement'
            )
            next_tasks.append(queue_definition(
                'T22', 'LEGAL',
                'Review Draft Agreement: %s' % tender.bid_title,
                'Review the draft agreement and upload the legally approved copy.',
            ))

        elif task.code == 'T22':
            legal_file = required_file(
                'legal_doc', 'Legally approved agreement'
            )
            next_tasks.append(queue_definition(
                'T23', 'BIDM',
                'Sign Agreement: %s' % tender.bid_title,
                'Sign the legally approved agreement.',
            ))

        elif task.code == 'T23':
            signed_file = required_file(
                'signed_doc', 'Signed agreement'
            )
            signature = required_text(
                'signature_data', 'Agreement signature'
            )
            next_tasks.append(queue_definition(
                'T24', 'OFFICE',
                'Upload Final Signed Agreement: %s' % tender.bid_title,
                'Upload the fully signed final agreement.',
            ))

        elif task.code == 'T24':
            final_file = required_file(
                'final_doc', 'Final signed agreement'
            )
            next_tasks.append(queue_definition(
                'I01', 'PROC',
                'Issue Purchase Order: %s' % tender.bid_title,
                'Issue the purchase order and confirm the supplier.',
            ))

        elif task.code == 'I01':
            po_file = required_file('po_doc', 'Purchase order')
            next_tasks.append(queue_definition(
                'I02', 'FIN',
                'Prepare Payment Request: %s' % tender.bid_title,
                'Prepare the payment request based on the purchase order.',
            ))

        elif task.code == 'I02':
            amount = Decimal(required_text(
                'payment_amount', 'Payment amount'
            ))
            if amount <= 0:
                raise ValueError("Payment amount must be greater than zero.")

            setting, _ = CompanySetting.objects.get_or_create(
                company=tender.company
            )
            if amount > setting.payment_approval_threshold:
                next_tasks.append(queue_definition(
                    'I03', 'CEO',
                    'Approve Payment: %s' % tender.bid_title,
                    'Payment amount %s ETB exceeds the approval threshold.'
                    % amount,
                ))
            else:
                next_tasks.append(queue_definition(
                    'I04', 'FIN',
                    'Open LC / Bank Transfer: %s' % tender.bid_title,
                    'Payment is within the approval threshold. '
                    'Open the LC or execute the bank transfer.',
                ))

        elif task.code == 'I03':
            if not (note or '').strip():
                raise ValueError(
                    "A payment approval note is required."
                )
            next_tasks.append(queue_definition(
                'I04', 'FIN',
                'Open LC / Bank Transfer: %s' % tender.bid_title,
                'Payment approved. Execute the bank transfer or open the LC.',
            ))

        elif task.code == 'I04':
            swift_file = required_file('swift_doc', 'SWIFT document')
            next_tasks.append(queue_definition(
                'I05', 'LOG',
                'Upload Shipping Documents: %s' % tender.bid_title,
                'Upload the shipping documents received from the supplier.',
            ))

        elif task.code == 'I05':
            shipping_file = required_file(
                'shipping_docs', 'Shipping documents'
            )
            next_tasks.append(queue_definition(
                'I06', 'LOG',
                'Assign Clearing Agent: %s' % tender.bid_title,
                'Review shipping documents and assign the clearing agent.',
            ))

        elif task.code == 'I06':
            clearing_agent = required_text(
                'clearing_agent', 'Clearing agent'
            )
            next_tasks.append(queue_definition(
                'I06G', 'GM',
                'Approve Clearing Agent: %s' % tender.bid_title,
                'Review and approve the assigned clearing agent.',
            ))

        elif task.code == 'I06G':
            if not (note or '').strip():
                raise ValueError("GM approval note is required.")
            next_tasks.append(queue_definition(
                'I07', 'FIN',
                'Prepare Duty and Tax Payment: %s' % tender.bid_title,
                'Calculate and submit customs duty and tax for approval.',
            ))

        elif task.code == 'I07':
            duty_amount = Decimal(required_text(
                'duty_tax_amount', 'Duty and tax amount'
            ))
            if duty_amount <= 0:
                raise ValueError(
                    "Duty and tax amount must be greater than zero."
                )
            next_tasks.append(queue_definition(
                'I08', 'CEO',
                'Approve Duty and Tax: %s' % tender.bid_title,
                'Review and approve the customs duty and tax payment.',
            ))

        elif task.code == 'I08':
            if not (note or '').strip():
                raise ValueError("CEO duty approval note is required.")
            next_tasks.append(queue_definition(
                'I09', 'LOG',
                'Clear Customs: %s' % tender.bid_title,
                'Upload customs clearance documents.',
            ))

        elif task.code == 'I09':
            customs_file = required_file(
                'customs_docs', 'Customs clearance documents'
            )
            next_tasks.append(queue_definition(
                'I10', 'LOG',
                'Receive Goods at Warehouse: %s' % tender.bid_title,
                'Upload the warehouse receipt and confirm arrival.',
            ))

        elif task.code == 'I10':
            warehouse_file = required_file(
                'warehouse_receipt', 'Warehouse receipt'
            )
            next_tasks.append(queue_definition(
                'I11', 'PROC',
                'Inspect Received Goods: %s' % tender.bid_title,
                'Record received quantities and upload the inspection report.',
            ))

        elif task.code == 'I11':
            item_name = required_text('item_name', 'Item name')
            try:
                qty_expected = int(required_text(
                    'qty_expected', 'Expected quantity'
                ))
                qty_received = int(required_text(
                    'qty_received', 'Received quantity'
                ))
                qty_damaged = int(required_text(
                    'qty_damaged', 'Damaged quantity'
                ))
            except (TypeError, ValueError):
                raise ValueError(
                    "Expected, received and damaged quantities must be whole numbers."
                )

            if qty_expected <= 0:
                raise ValueError("Expected quantity must be greater than zero.")
            if qty_received <= 0:
                raise ValueError(
                    "At least one item must be physically received for inspection."
                )
            if qty_damaged < 0 or qty_damaged > qty_received:
                raise ValueError(
                    "Damaged quantity must be between zero and received quantity."
                )

            qty_usable = qty_received - qty_damaged

            if qty_damaged == 0:
                inspection_condition = 'GOOD'
            elif qty_usable == 0:
                inspection_condition = 'DAMAGED'
            else:
                inspection_condition = 'MIXED'

            inspection_notes = (data.get('inspection_notes') or '').strip()

            inspection_file = required_file(
                'inspection_report', 'Inspection report'
            )

            # Do not create I12 when no usable goods are available.
            if qty_usable > 0:
                next_tasks.append(queue_definition(
                    'I12', 'LOG',
                    'Deliver Goods: %s' % tender.bid_title,
                    'Record goods delivered to the purchaser.',
                ))

        elif task.code == 'I12':
            try:
                product_id = int(required_text(
                    'product_id', 'Inventory product'
                ))
                qty_delivered = int(required_text(
                    'qty_delivered', 'Delivered quantity'
                ))
            except (TypeError, ValueError):
                raise ValueError(
                    "Select a valid product and enter a whole-number quantity."
                )
            if qty_delivered <= 0:
                raise ValueError(
                    "Delivered quantity must be greater than zero."
                )
            if not Product.objects.filter(
                pk=product_id, company=tender.company
            ).exists():
                raise ValueError(
                    "The selected inventory product does not belong to this company."
                )
            delivery_file = required_file(
                'delivery_note', 'Delivery note'
            )
            next_tasks.append(queue_definition(
                'I13', 'CASE',
                'Register Goods Received Note: %s' % tender.bid_title,
                'Upload the purchaser goods received note and invoice.',
            ))

        elif task.code == 'I13':
            grn_file = required_file(
                'grn_invoice', 'Goods received note / invoice'
            )
            next_tasks.append(queue_definition(
                'I14', 'FIN',
                'Collect Customer Payment: %s' % tender.bid_title,
                'Upload the customer payment receipt.',
            ))

        elif task.code == 'I14':
            payment_file = required_file(
                'payment_receipt', 'Customer payment receipt'
            )
            next_tasks.append(queue_definition(
                'I15', 'BIDM',
                'Prepare Project Closure Report: %s' % tender.bid_title,
                'Prepare and upload the project closure report.',
            ))

        elif task.code == 'I15':
            closure_file = required_file(
                'closure_report', 'Project closure report'
            )
            next_tasks.append(queue_definition(
                'I16', 'CEO',
                'Approve Project Closure: %s' % tender.bid_title,
                'Review and approve final project closure.',
            ))

        elif task.code == 'I16':
            if tender.status != 'WON':
                raise ValueError(
                    "Only a tender with WON status can be finally closed."
                )

        # Confirm successor roles exist before closing the current task.
        for item in next_tasks:
            existing = Task.objects.filter(
                company=tender.company,
                code=item['code'],
                payload__tender_id=tender.id,
            ).exists()

            if not existing and find_role(tender.company, item['role']) is None:
                raise ValueError(
                    "No active employee is assigned to the %s role. "
                    "Assign that company role before completing this task."
                    % item['role']
                )

        with transaction.atomic():
            # Lock and recheck the task to prevent concurrent duplicate submissions.
            task = Task.objects.select_for_update().get(
                pk=task.pk,
                company=tender.company,
            )
            if task.status in ('COMPLETED', 'REJECTED', 'CANCELLED'):
                raise ValueError("This task has already been closed.")

            # Save task-specific tender information and documents.
            if task.code == 'T14':
                tender.bank_letter = bank_file
                tender.save()

                Correspondence.objects.create(
                    company=tender.company,
                    direction='OUTGOING',
                    reference_number=tender.bid_reference_number or '',
                    letter_date=timezone.localdate(),
                    sender_recipient=tender.purchaser_name,
                    subject='Bank Letter - %s' % tender.bid_title,
                    file=tender.bank_letter,
                    uploaded_by=actor,
                )
                register_document(
                    tender, tender.bank_letter,
                    'Outgoing Bank Letter', actor, 'MAIL'
                )

            elif task.code == 'T15':
                tender.cpo_document = cpo_file
                tender.save()
                register_document(
                    tender, tender.cpo_document,
                    'CPO Document', actor, 'FINANCE'
                )

            elif task.code == 'T16':
                tender.final_price = final_price
                tender.final_bid_price = final_price
                tender.ceo_signature = signature
                tender.save()

            elif task.code == 'T17':
                tender.final_docs_uploaded = final_docs
                tender.save()
                register_document(
                    tender, tender.final_docs_uploaded,
                    'Sealed Final Bid Documents', actor, 'TENDER_PREP'
                )

            elif task.code == 'T18':
                tender.submission_status = submission
                tender.physical_submission_status = submission
                tender.tech_result = technical
                tender.technical_result = technical
                tender.fin_result = financial
                tender.competitor_prices = data.get(
                    'competitor_prices', ''
                )

                if submission == 'Rejected' or technical == 'Fail':
                    tender.status = 'LOST'
                elif submission == 'Submitted':
                    tender.status = 'SUBMITTED'

                tender.save()

            elif task.code == 'T19':
                if tender.status == 'LOST':
                    tender.loss_reason = required_text(
                        'loss_reason', 'Loss reason'
                    )
                    tender.save()

                    # Close any obsolete award task for this lost tender.
                    Task.objects.filter(
                        company=tender.company,
                        code='T20',
                        payload__tender_id=tender.id,
                        status__in=['PENDING', 'IN_PROGRESS'],
                    ).update(
                        status='CANCELLED',
                        updated_at=timezone.now(),
                    )

            elif task.code == 'T20':
                if tender.status == 'LOST':
                    raise ValueError(
                        "This tender is marked LOST. It cannot be marked WON."
                    )

                tender.award_document = award_file
                tender.status = 'WON'
                tender.save()
                register_document(
                    tender, tender.award_document,
                    'Official Award Document', actor, 'TENDER_SRC'
                )

            elif task.code == 'T21':
                tender.draft_agreement = draft_file
                tender.save()
                register_document(
                    tender, tender.draft_agreement,
                    'Draft Agreement', actor, 'LEGAL'
                )

            elif task.code == 'T22':
                tender.legal_approved_agreement = legal_file
                tender.save()
                register_document(
                    tender, tender.legal_approved_agreement,
                    'Legally Approved Agreement', actor, 'LEGAL'
                )

            elif task.code == 'T23':
                tender.signed_agreement = signed_file
                tender.bidm_signature = signature
                tender.save()
                register_document(
                    tender, tender.signed_agreement,
                    'Signed Agreement', actor, 'LEGAL'
                )

            elif task.code == 'T24':
                tender.final_agreement = final_file
                tender.save()
                register_document(
                    tender, tender.final_agreement,
                    'Final Signed Agreement', actor, 'LEGAL'
                )

            elif task.code == 'I01':
                tender.po_document = po_file
                tender.save()
                register_document(
                    tender, tender.po_document,
                    'Purchase Order', actor, 'PROCUREMENT'
                )

            elif task.code == 'I02':
                tender.payment_amount = amount
                tender.save()

            elif task.code == 'I04':
                tender.lc_swift_copy = swift_file
                tender.save()
                register_document(
                    tender, tender.lc_swift_copy,
                    'LC SWIFT Copy', actor, 'FINANCE'
                )

            elif task.code == 'I05':
                tender.shipping_docs = shipping_file
                tender.save()
                register_document(
                    tender, tender.shipping_docs,
                    'Shipping Documents', actor, 'LOGISTICS'
                )

            elif task.code == 'I06':
                tender.clearing_agent = clearing_agent
                tender.save(update_fields=['clearing_agent'])

            elif task.code == 'I06G':
                # The approval note is retained in task history.
                pass

            elif task.code == 'I07':
                tender.duty_tax_amount = duty_amount
                tender.save(update_fields=['duty_tax_amount'])

            elif task.code == 'I08':
                # The CEO approval note is retained in task history.
                pass

            elif task.code == 'I09':
                tender.customs_docs = customs_file
                tender.save(update_fields=['customs_docs'])
                register_document(
                    tender, tender.customs_docs,
                    'Customs Clearance Documents', actor, 'LOGISTICS'
                )
                shipment, _ = Shipment.objects.get_or_create(
                    company=tender.company, tender=tender
                )
                shipment.current_status = 'CUSTOMS'
                shipment.save()

            elif task.code == 'I10':
                tender.warehouse_receipt = warehouse_file
                tender.save(update_fields=['warehouse_receipt'])
                register_document(
                    tender, tender.warehouse_receipt,
                    'Warehouse Receipt', actor, 'LOGISTICS'
                )
                shipment, _ = Shipment.objects.get_or_create(
                    company=tender.company, tender=tender
                )
                shipment.current_status = 'WAREHOUSE'
                shipment.save()

            elif task.code == 'I11':
                tender.inspection_report = inspection_file
                tender.save(update_fields=['inspection_report'])
                register_document(
                    tender, tender.inspection_report,
                    'Goods Inspection Report', actor, 'LOGISTICS'
                )

                shipment, _ = Shipment.objects.get_or_create(
                    company=tender.company, tender=tender
                )

                InventoryItem.objects.create(
                    shipment=shipment,
                    item_name=item_name,
                    qty_expected=qty_expected,
                    qty_received=qty_received,
                    qty_damaged=qty_damaged,
                    condition=inspection_condition,
                    notes=(
                        'Tender %s. Usable: %s. Damaged: %s. %s'
                        % (
                            tender.serial_number,
                            qty_usable,
                            qty_damaged,
                            inspection_notes,
                        )
                    ),
                )

                # Only physically received, usable goods enter available stock.
                if qty_usable > 0:
                    product, _ = Product.objects.get_or_create(
                        company=tender.company,
                        name=item_name,
                        defaults={'current_stock': 0},
                    )
                    product.current_stock += qty_usable
                    product.save(update_fields=['current_stock'])

                    StockTransaction.objects.create(
                        product=product,
                        trans_type='IN',
                        quantity=qty_usable,
                        notes=(
                            'Tender %s goods received; %s damaged'
                            % (tender.serial_number, qty_damaged)
                        ),
                        created_by=actor,
                    )

            elif task.code == 'I12':
                product = Product.objects.select_for_update().get(
                    pk=product_id, company=tender.company
                )
                if product.current_stock < qty_delivered:
                    raise ValueError(
                        "Insufficient stock for %s. Available: %s; requested: %s."
                        % (
                            product.name,
                            product.current_stock,
                            qty_delivered,
                        )
                    )
                product.current_stock -= qty_delivered
                product.save(update_fields=['current_stock'])
                StockTransaction.objects.create(
                    product=product,
                    trans_type='OUT',
                    quantity=qty_delivered,
                    notes='Tender %s customer delivery' % tender.serial_number,
                    created_by=actor,
                )
                tender.delivery_note = delivery_file
                tender.save(update_fields=['delivery_note'])
                register_document(
                    tender, tender.delivery_note,
                    'Customer Delivery Note', actor, 'LOGISTICS'
                )

            elif task.code == 'I13':
                tender.grn_invoice = grn_file
                tender.save(update_fields=['grn_invoice'])
                register_document(
                    tender, tender.grn_invoice,
                    'Goods Received Note and Invoice', actor, 'ACCOUNTING'
                )

            elif task.code == 'I14':
                tender.payment_receipt = payment_file
                tender.save(update_fields=['payment_receipt'])
                register_document(
                    tender, tender.payment_receipt,
                    'Customer Payment Receipt', actor, 'FINANCE'
                )

            elif task.code == 'I15':
                tender.closure_report = closure_file
                tender.save(update_fields=['closure_report'])
                register_document(
                    tender, tender.closure_report,
                    'Project Closure Report', actor, 'ADMIN'
                )

            elif task.code == 'I16':
                # Keep the valid tender status WON. Task completion records
                # final workflow closure without inventing a tender status.
                pass

            # An inspection with zero usable goods is recorded but remains open.
            if task.code == 'I11' and qty_usable == 0:
                task.status = 'IN_PROGRESS'
                task.completed_at = None
                task.save(update_fields=['status', 'completed_at'])

                blocked_note = (
                    'INSPECTION_BLOCKED: No usable goods available. '
                    'Received=%s, damaged=%s, expected=%s. '
                    'Replacement or corrective action is required before delivery.'
                    % (qty_received, qty_damaged, qty_expected)
                )
                if note:
                    blocked_note += ' Employee note: ' + note

                log_task_history(
                    task, 'INSPECTION_BLOCKED', blocked_note, actor
                )

            else:
                # Complete and record the task using the actual acting employee.
                task.status = 'COMPLETED'
                task.completed_at = timezone.now()
                task.save()
                log_task_history(task, action, note, actor)

            # Create only successor tasks that do not already exist.
            for item in next_tasks:
                already_exists = Task.objects.filter(
                    company=tender.company,
                    code=item['code'],
                    payload__tender_id=tender.id,
                ).exists()

                if not already_exists:
                    create_task(
                        tender.company,
                        item['code'],
                        item['role'],
                        item['title'],
                        item['description'],
                        {'tender_id': tender.id},
                        url,
                    )

    except (ValueError, ArithmeticError) as exc:
        return str(exc)

    return None


def advance_financial_submission(tender, task, action, note, data, files):
    task.status = 'COMPLETED'
    task.save()
    url = reverse('tender_detail', args=[tender.id])

    if action == 'BANK_LETTER_READY':
        if 'bank_letter' in files:
            tender.bank_letter = files['bank_letter']
            tender.save()
            register_document(tender, tender.bank_letter, 'Bank Letter', task.assigned_to, 'FINANCE')
            
        create_task(tender.company, 'T15', 'OFFICE', 
                    f'Log Outgoing Bank Letter: {tender.bid_title}',
                    'Log the Bank Letter in the Mail Registry and prepare physical dispatch.',
                    {'tender_id': tender.id}, url)
                    
    elif action == 'LETTER_LOGGED':
        create_task(tender.company, 'T16', 'CASE', 
                    f'Upload CPO & Hold Original: {tender.bid_title}',
                    'Upload the received CPO and record its physical shelf location.',
                    {'tender_id': tender.id}, url)
                    
    elif action == 'CPO_UPLOADED':
        if 'cpo_document' in files:
            tender.cpo_document = files['cpo_document']
            tender.save()
            register_document(tender, tender.cpo_document, 'Original CPO', task.assigned_to, 'FINANCE')
            
        create_task(tender.company, 'T17', 'BIDOP', 
                    f'Final Sealing & Checklist: {tender.bid_title}',
                    'Print, sign, stamp, and seal all lot documents. Confirm the checklist to proceed.',
                    {'tender_id': tender.id}, url)
                    
    elif action == 'DOCUMENTS_SEALED':
        create_task(tender.company, 'T18', 'CASE', 
                    f'Physical Submission & Results: {tender.bid_title}',
                    'Go to the purchaser, submit the bid, and report the results.',
                    {'tender_id': tender.id}, url)
                    
    elif action == 'RESULTS_REPORTED':
        tender.physical_submission_status = data.get('submission_status', '')
        tender.competitor_prices = data.get('competitor_prices', '')
        tender.technical_result = data.get('technical_result', '')
        tender.save()
        
        create_task(tender.company, 'T20', 'OFFICE', 
                    f'Upload Award Document: {tender.bid_title}',
                    'If the company won, upload the award document to trigger the WON status.',
                    {'tender_id': tender.id}, url)


def advance_final_delivery(tender, task, action, note, data, files):
    task.status = 'COMPLETED'
    task.save()
    url = reverse('tender_detail', args=[tender.id])

    if action == 'DELIVERED':
        create_task(tender.company, 'I13', 'CASE', 
                    f'Customer Acceptance (GRN): {tender.bid_title}',
                    'Get the customer signature and upload the GRN.',
                    {'tender_id': tender.id}, url)
                    
    elif action == 'GRN_RECEIVED':
        create_task(tender.company, 'I14', 'FIN', 
                    f'Receivable & Collection: {tender.bid_title}',
                    'Collect payment from the customer and upload the receipt.',
                    {'tender_id': tender.id}, url)
                    
    elif action == 'PAYMENT_COLLECTED':
        create_task(tender.company, 'I15', 'BIDM', 
                    f'Project Closure Report: {tender.bid_title}',
                    'Write the final project closure report.',
                    {'tender_id': tender.id}, url)
                    
    elif action == 'CLOSURE_REPORTED':
        create_task(tender.company, 'I16', 'CEO', 
                    f'Final Project Approval: {tender.bid_title}',
                    'Approve project closure.',
                    {'tender_id': tender.id}, url)
                    
    elif action == 'PROJECT_CLOSED':
        tender.status = 'COMPLETED'
        tender.save()
        
        # Automatically log the final customer revenue into the Cash Flow Ledger
        from .models import CashFlowEntry
        # Calculate total revenue based on the lots
        total_revenue = sum(lot.final_price for lot in tender.lots.all() if lot.final_price > 0)
        
        if total_revenue > 0:
            CashFlowEntry.objects.create(
                company=tender.company,
                flow_type='IN',
                category='CUSTOMER',
                amount=total_revenue,
                description=f'Final Revenue Collection for {tender.serial_number}',
                related_tender=tender,
                created_by=task.assigned_to
            )
