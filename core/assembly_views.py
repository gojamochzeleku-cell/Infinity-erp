from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from .models import Tender, Task, TenderLot, LotItem
from .chain import create_task
from .services import log_action

def _assembly_membership(request, allowed_roles):
    """Validate the active user and current company membership."""
    from django.core.exceptions import PermissionDenied

    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or emp is None
        or not emp.is_active
        or company is None
        or not company.is_active
        or membership is None
        or not membership.is_active
        or membership.employee_id != emp.id
        or membership.company_id != company.id
    ):
        raise PermissionDenied("Active company membership required.")

    roles = set(membership.roles.values_list('role', flat=True))
    if not roles.intersection(allowed_roles):
        raise PermissionDenied("Your company membership does not have the required role.")

    return emp, company, roles


@login_required
def lot_assembly(request, pk, lot_id):
    from django.db import transaction
    from django.core.exceptions import PermissionDenied

    emp, company, roles = _assembly_membership(request, {'BIDOP'})

    tender = get_object_or_404(Tender, pk=pk, company=company)
    lot = get_object_or_404(TenderLot, pk=lot_id, tender=tender)

    task = get_object_or_404(
        Task,
        company=company,
        assigned_to=emp,
        code='T10_LOT',
        payload__tender_id=tender.id,
        payload__lot_id=lot.id,
        status__in=('PENDING', 'IN_PROGRESS'),
    )

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'save':
            if lot.status in ('ASSEMBLED', 'PRICED'):
                messages.error(request, 'This lot has already been assembled or priced.')
                return redirect('lot_assembly', pk=tender.id, lot_id=lot.id)

            if 'bo_technical_proposal' in request.FILES:
                lot.bo_technical_proposal = request.FILES['bo_technical_proposal']
            if 'bo_financial_proposal' in request.FILES:
                lot.bo_financial_proposal = request.FILES['bo_financial_proposal']
            if 'bo_compliance_matrix' in request.FILES:
                lot.bo_compliance_matrix = request.FILES['bo_compliance_matrix']

            lot.save()
            messages.success(request, f'{lot.lot_name} files uploaded successfully.')
            return redirect('lot_assembly', pk=tender.id, lot_id=lot.id)

        if action == 'complete':
            with transaction.atomic():
                locked_lot = get_object_or_404(
                    TenderLot.objects.select_for_update(),
                    pk=lot.id,
                    tender=tender,
                )

                locked_task = get_object_or_404(
                    Task.objects.select_for_update(),
                    pk=task.id,
                    company=company,
                    assigned_to=emp,
                    code='T10_LOT',
                    payload__tender_id=tender.id,
                    payload__lot_id=locked_lot.id,
                    status__in=('PENDING', 'IN_PROGRESS'),
                )

                if locked_lot.status in ('ASSEMBLED', 'PRICED'):
                    messages.error(request, 'This lot has already been assembled or priced.')
                    return redirect('dashboard')

                locked_lot.status = 'ASSEMBLED'
                locked_lot.save(update_fields=['status'])

                locked_task.status = 'COMPLETED'
                locked_task.completed_at = timezone.now()
                locked_task.save(
                    update_fields=['status', 'completed_at', 'updated_at']
                )

                existing_pricing_task = Task.objects.filter(
                    company=company,
                    code='T13_LOT',
                    payload__tender_id=tender.id,
                    payload__lot_id=locked_lot.id,
                ).exists()

                if not existing_pricing_task:
                    pricing_task = create_task(
                        company,
                        'T13_LOT',
                        'CEO',
                        f'Set Price for {locked_lot.lot_name} ({tender.serial_number})',
                        f'Bid Operator assembled {locked_lot.lot_name}. Please set FX and final price.',
                        {
                            'tender_id': tender.id,
                            'lot_id': locked_lot.id,
                        },
                        reverse('tender_detail', args=[tender.id]),
                    )

                    if pricing_task is None:
                        messages.warning(
                            request,
                            'Lot assembled, but no designated CEO is available. '
                            'An administrator must assign the CEO role before pricing can proceed.',
                        )
                    else:
                        messages.success(
                            request,
                            'Lot assembled and sent to the CEO for pricing.',
                        )
                else:
                    messages.info(
                        request,
                        'Lot assembled. An existing CEO pricing task was preserved.',
                    )

                log_action(
                    emp,
                    'ASSEMBLE_LOT',
                    f'Assembled {locked_lot.lot_name}',
                    'TenderLot',
                    locked_lot.id,
                    company,
                )

            return redirect('dashboard')

        messages.error(request, 'Invalid lot assembly action.')
        return redirect('lot_assembly', pk=tender.id, lot_id=lot.id)

    return render(request, 'lot_assembly.html', {
        'tender': tender,
        'lot': lot,
        'task': task,
    })


@login_required
def manage_lot_items(request, pk, lot_id):
    from decimal import Decimal, InvalidOperation

    emp, company, roles = _assembly_membership(
        request,
        {'BIDOP', 'BIDM', 'ADMIN'},
    )

    tender = get_object_or_404(Tender, pk=pk, company=company)
    lot = get_object_or_404(TenderLot, pk=lot_id, tender=tender)

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'add_item':
            name = request.POST.get('item_name', '').strip()
            unit = request.POST.get('unit', 'Pcs').strip() or 'Pcs'
            brand = request.POST.get('proposed_brand', '').strip()

            try:
                quantity = Decimal(request.POST.get('quantity', '1'))
                price = Decimal(request.POST.get('unit_price', '0'))

                if not quantity.is_finite() or quantity <= 0:
                    raise InvalidOperation
                if not price.is_finite() or price < 0:
                    raise InvalidOperation
            except (InvalidOperation, TypeError, ValueError):
                messages.error(
                    request,
                    'Enter a valid positive quantity and a non-negative unit price.',
                )
                return redirect(
                    'manage_lot_items',
                    pk=tender.id,
                    lot_id=lot.id,
                )

            if not name:
                messages.error(request, 'Item name is required.')
            else:
                LotItem.objects.create(
                    lot=lot,
                    item_name=name,
                    quantity=quantity,
                    unit=unit,
                    proposed_brand=brand,
                    unit_price=price,
                )
                messages.success(request, 'Lot item added successfully.')

            return redirect(
                'manage_lot_items',
                pk=tender.id,
                lot_id=lot.id,
            )

        if action == 'delete_item':
            item_id = request.POST.get('item_id')
            deleted, _ = LotItem.objects.filter(
                pk=item_id,
                lot=lot,
            ).delete()

            if deleted:
                messages.success(request, 'Lot item deleted.')
            else:
                messages.error(request, 'The selected item was not found.')

            return redirect(
                'manage_lot_items',
                pk=tender.id,
                lot_id=lot.id,
            )

        messages.error(request, 'Invalid lot item action.')
        return redirect(
            'manage_lot_items',
            pk=tender.id,
            lot_id=lot.id,
        )

    items = lot.items.all()
    total_items_cost = sum(
        (item.total_price for item in items),
        Decimal('0'),
    )

    return render(request, 'manage_lot_items.html', {
        'tender': tender,
        'lot': lot,
        'items': items,
        'total_items_cost': total_items_cost,
    })


@login_required
def bid_operator_workspace(request, pk, task_id):
    """Workspace for a Bid Operator's War Room section task."""
    from django.db import transaction

    emp = getattr(request.user, 'employee', None)
    company = getattr(request, 'company', None)
    membership = getattr(request, 'membership', None)

    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or emp is None
        or not emp.is_active
        or company is None
        or not company.is_active
        or membership is None
        or not membership.is_active
        or membership.employee_id != emp.id
        or membership.company_id != company.id
    ):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Active company membership required.")

    roles = set(membership.roles.values_list('role', flat=True))
    if 'BIDOP' not in roles:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Bid Operator company role required.")

    tender = get_object_or_404(Tender, pk=pk, company=company)

    task = get_object_or_404(
        Task,
        pk=task_id,
        company=company,
        assigned_to=emp,
        code='T10',
        payload__tender_id=tender.id,
    )

    section = (task.payload or {}).get('section')
    allowed_sections = {'summary', 'checklist', 'brands', 'suppliers', 'all'}

    if section not in allowed_sections:
        from django.http import Http404
        raise Http404("This is not a War Room Bid Operator task.")

    section_fields = {
        'summary': [('Bid Summary & Requirements', tender.war_room_summary)],
        'checklist': [('Submission Checklist', tender.war_room_checklist)],
        'brands': [('Brand Sourcing', tender.war_room_brands)],
        'suppliers': [('Supplier Intelligence', tender.war_room_suppliers)],
        'all': [
            ('Bid Summary & Requirements', tender.war_room_summary),
            ('Submission Checklist', tender.war_room_checklist),
            ('Brand Sourcing', tender.war_room_brands),
            ('Supplier Intelligence', tender.war_room_suppliers),
        ],
    }

    if request.method == 'POST':
        action = request.POST.get('action', '')

        with transaction.atomic():
            locked_task = get_object_or_404(
                Task.objects.select_for_update(),
                pk=task.id,
                company=company,
                assigned_to=emp,
            )

            if locked_task.status not in ('PENDING', 'IN_PROGRESS'):
                messages.error(request, 'This task is no longer active.')
                return redirect('dashboard')

            if action not in ('save', 'complete'):
                messages.error(request, 'Invalid workspace action.')
                return redirect(
                    'bid_operator_workspace',
                    pk=tender.id,
                    task_id=locked_task.id,
                )

            if 'task_file' in request.FILES:
                locked_task.uploaded_file = request.FILES['task_file']

            note = request.POST.get('bid_op_note', '').strip()
            if note:
                timestamp = timezone.localtime().strftime('%Y-%m-%d %H:%M')
                tender.bid_op_notes = (
                    (tender.bid_op_notes or '')
                    + f"\n\n[{timestamp}] {section.upper()} - {emp.full_name}:\n{note}"
                )
                tender.save(update_fields=['bid_op_notes', 'updated_at'])

            if action == 'complete':
                locked_task.status = 'COMPLETED'
                locked_task.completed_at = timezone.now()
                locked_task.save(
                    update_fields=[
                        'uploaded_file',
                        'status',
                        'completed_at',
                        'updated_at',
                    ]
                )
                log_action(
                    emp,
                    'BID_OPERATOR_SECTION_COMPLETED',
                    f'Completed {section} review for {tender.serial_number}',
                    'Task',
                    locked_task.id,
                    company,
                )
                messages.success(request, 'War Room section task completed.')
                return redirect('dashboard')

            locked_task.save(update_fields=['uploaded_file', 'updated_at'])
            messages.success(request, 'Your work has been saved.')

        return redirect(
            'bid_operator_workspace',
            pk=tender.id,
            task_id=task.id,
        )

    return render(request, 'bid_operator_workspace.html', {
        'tender': tender,
        'task': task,
        'section': section,
        'section_contents': section_fields[section],
    })
