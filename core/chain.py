from django.urls import reverse
from .models import Employee, Task, Tender, TenderReview, Document, CompanySetting, CashFlowEntry, Shipment
from .services import notify, log_task_history
from decimal import Decimal

def find_role(company, role):
    return Employee.objects.filter(role=role, is_active=True).first()

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
    emp = getattr(request.user, 'employee', None)
    TenderReview.objects.create(
        tender=tender, employee=emp, role=emp.role, decision=decision, note=note)
    task.status = 'COMPLETED'
    task.save()
    log_task_history(task, decision, note, emp)

    if task.code == 'T02':
        if decision == 'GO':
            tender.status = 'GO'
            tender.save()
            create_task(tender.company, 'T03', 'CEO',
                        f'CEO Approval: {tender.bid_title}',
                        f'Bid Manager marked GO. Note: {note}',
                        {'tender_id': tender.id},
                        reverse('tender_detail', args=[tender.id]))
        else:
            tender.status = 'NO_GO'
            tender.save()

    elif task.code == 'T03':
        if decision == 'APPROVED':
            tender.status = 'CEO_APPROVED'
            tender.save()
            create_task(tender.company, 'T04', 'CASE',
                        f'Cost Request: {tender.bid_title}',
                        f'CEO Approved. Note: {note}',
                        {'tender_id': tender.id},
                        reverse('tender_detail', args=[tender.id]))
        else:
            tender.status = 'CEO_REJECTED'
            tender.save()

def process_cost_request(tender, task, action, note):
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
                f'AI Analysis & Task Routing: {tender.bid_title}',
                'Review the AI summary, add manual notes, and check which departments need to submit forms.',
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
