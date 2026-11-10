from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone

from .models import Tender, Task, TenderLot
from .chain import create_task
from .services import log_action


def _pricing_membership(request):
    """Validate active employee and current company membership."""
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
    if not roles.intersection({'CEO', 'GM', 'ADMIN'}):
        raise PermissionDenied("CEO, GM, or ADMIN company role required.")

    return emp, company


def _pricing_decimal(raw_value, field_name, max_value, decimal_places,
                     strictly_positive=False):
    """Parse and validate a value against its model DecimalField limits."""
    try:
        value = Decimal(raw_value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Enter a valid {field_name}.")

    if not value.is_finite():
        raise ValueError(f"Enter a finite {field_name}.")

    if strictly_positive and value <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")

    if not strictly_positive and value < 0:
        raise ValueError(f"{field_name} cannot be negative.")

    if value > max_value:
        raise ValueError(f"{field_name} exceeds the permitted maximum.")

    if max(0, -value.as_tuple().exponent) > decimal_places:
        raise ValueError(
            f"{field_name} supports at most {decimal_places} decimal places."
        )

    return value


@login_required
def ceo_lot_pricing(request, pk, lot_id):
    emp, company = _pricing_membership(request)

    tender = get_object_or_404(Tender, pk=pk, company=company)
    lot = get_object_or_404(TenderLot, pk=lot_id, tender=tender)

    active_task_filter = {
        'company': company,
        'assigned_to': emp,
        'code': 'T13_LOT',
        'payload__tender_id': tender.id,
        'payload__lot_id': lot.id,
        'status__in': ('PENDING', 'IN_PROGRESS'),
    }

    task = get_object_or_404(Task, **active_task_filter)

    if lot.status == 'PRICED':
        messages.info(request, 'This lot has already been priced.')
        return redirect('dashboard')

    if lot.status != 'ASSEMBLED':
        messages.error(
            request,
            'This lot must be assembled before its price can be locked.',
        )
        return redirect('dashboard')

    if request.method == 'POST':
        try:
            fx_rate = _pricing_decimal(
                request.POST.get('fx_rate', ''),
                'FX rate',
                Decimal('999999.9999'),
                4,
                strictly_positive=True,
            )
            profit_margin = _pricing_decimal(
                request.POST.get('profit_margin', ''),
                'profit margin',
                Decimal('999.99'),
                2,
            )
            final_price = _pricing_decimal(
                request.POST.get('final_price', ''),
                'final price',
                Decimal('999999999999.99'),
                2,
                strictly_positive=True,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(
                'ceo_lot_pricing',
                pk=tender.id,
                lot_id=lot.id,
            )

        with transaction.atomic():
            # Serialize pricing operations for this tender.
            locked_tender = get_object_or_404(
                Tender.objects.select_for_update(),
                pk=tender.id,
                company=company,
            )

            locked_lot = get_object_or_404(
                TenderLot.objects.select_for_update(),
                pk=lot.id,
                tender=locked_tender,
            )

            locked_task = get_object_or_404(
                Task.objects.select_for_update(),
                **active_task_filter,
            )

            if locked_lot.status == 'PRICED':
                messages.info(request, 'This lot has already been priced.')
                return redirect('dashboard')

            if locked_lot.status != 'ASSEMBLED':
                messages.error(
                    request,
                    'This lot is no longer available for pricing.',
                )
                return redirect('dashboard')

            locked_lot.fx_rate = fx_rate
            locked_lot.profit_margin = profit_margin
            locked_lot.final_price = final_price
            locked_lot.status = 'PRICED'
            locked_lot.save(update_fields=[
                'fx_rate',
                'profit_margin',
                'final_price',
                'status',
            ])

            locked_task.status = 'COMPLETED'
            locked_task.completed_at = timezone.now()
            locked_task.save(update_fields=[
                'status',
                'completed_at',
                'updated_at',
            ])

            log_action(
                emp,
                'CEO_LOCK_PRICE',
                f'Locked price for {locked_lot.lot_name} at '
                f'{locked_lot.final_price} ETB',
                'TenderLot',
                locked_lot.id,
                company,
            )

            all_priced = (
                locked_tender.lots.exists()
                and not locked_tender.lots.exclude(status='PRICED').exists()
            )

            if all_priced:
                existing_finance_task = Task.objects.filter(
                    company=company,
                    code='T14',
                    payload__tender_id=locked_tender.id,
                ).exists()

                if not existing_finance_task:
                    finance_task = create_task(
                        company,
                        'T14',
                        'FIN',
                        f'Prepare CPOs & Bank Letters: '
                        f'{locked_tender.bid_title}',
                        'All lots are priced. Prepare the CPOs for submission.',
                        {'tender_id': locked_tender.id},
                        reverse(
                            'tender_detail',
                            args=[locked_tender.id],
                        ),
                    )

                    if finance_task is None:
                        messages.warning(
                            request,
                            'Lot price locked, but no designated Finance '
                            'employee is available. An administrator must '
                            'assign the FIN role before the next step.',
                        )
                    else:
                        messages.success(
                            request,
                            'All lots are priced. The Finance task has been created.',
                        )
                else:
                    messages.info(
                        request,
                        'All lots are priced. An existing Finance task was preserved.',
                    )

            messages.success(
                request,
                f'{locked_lot.lot_name} price locked successfully.',
            )

        return redirect('dashboard')

    return render(request, 'ceo_lot_pricing.html', {
        'tender': tender,
        'lot': lot,
        'task': task,
    })
