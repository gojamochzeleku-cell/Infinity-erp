import hashlib
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .chain import create_task
from .models import Task, Tender, WarRoomArtifact
from .services import log_action, log_task_history


ACTIVE = ('PENDING', 'IN_PROGRESS')


def _context(request, pk, artifact_id):
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
    if not roles.intersection({'BIDM', 'BIDOP'}):
        raise PermissionDenied("BIDM or BIDOP company role required.")

    tender = get_object_or_404(Tender, pk=pk, company=company)
    artifact = get_object_or_404(
        WarRoomArtifact,
        pk=artifact_id,
        company=company,
        tender=tender,
    )

    source = artifact.source_artifact or artifact
    bidm_task = None
    review_task = None
    bidop_task = None

    if 'BIDM' in roles:
        bidm_task = Task.objects.filter(
            company=company,
            code='T09',
            assigned_to=emp,
            payload__tender_id=tender.id,
        ).first()

        review_task = Task.objects.filter(
            company=company,
            code='WR_REVIEW',
            assigned_to=emp,
            payload__artifact_id=artifact.id,
        ).first()

        if bidm_task or review_task:
            return (
                emp, company, membership, tender, artifact, source,
                'BIDM', bidm_task, review_task, None,
            )

    if 'BIDOP' in roles:
        bidop_task = Task.objects.filter(
            company=company,
            code='WR_ARTIFACT',
            assigned_to=emp,
            status__in=ACTIVE,
            payload__artifact_id=source.id,
        ).first()

        if bidop_task:
            return (
                emp, company, membership, tender, artifact, source,
                'BIDOP', None, None, bidop_task,
            )

    raise PermissionDenied("No matching assigned War Room artifact task.")


def _finish_task(task, employee, action, note):
    if task is None:
        return
    task.status = 'COMPLETED'
    task.completed_at = timezone.now()
    task.save(update_fields=['status', 'completed_at', 'updated_at'])
    log_task_history(task, action, note, employee)


@login_required
def war_room_artifact_detail(request, pk, artifact_id):
    (
        emp, company, membership, tender, artifact, source,
        role, bidm_task, review_task, bidop_task,
    ) = _context(request, pk, artifact_id)

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()

        with transaction.atomic():
            locked = get_object_or_404(
                WarRoomArtifact.objects.select_for_update(),
                pk=artifact.pk,
                company=company,
                tender=tender,
            )
            locked_source = locked.source_artifact or locked

            if role == 'BIDM':
                if action == 'submit_review':
                    if not bidm_task or bidm_task.status not in ACTIVE or locked.source_artifact_id:
                        raise PermissionDenied("Only the assigned BIDM can submit an original draft.")
                    if locked.status not in ('DRAFT', 'RETURNED'):
                        messages.error(request, "Only a draft or returned original can be submitted.")
                    else:
                        locked.status = 'REVIEW'
                        locked.approval_note = request.POST.get('note', '').strip()
                        locked.save(update_fields=['status', 'approval_note', 'updated_at'])
                        log_action(
                            emp, 'WAR_ROOM_ARTIFACT_SUBMITTED',
                            f'Submitted {locked.title} v{locked.version_number} for BIDM review',
                            'WarRoomArtifact', locked.id, company,
                        )
                        messages.success(request, "Artifact submitted for BIDM review.")

                elif action in ('approve', 'return'):
                    if locked.status != 'REVIEW':
                        messages.error(request, "This artifact is not awaiting review.")
                    elif locked.source_artifact_id and (
                        review_task is None or review_task.status not in ACTIVE
                    ):
                        raise PermissionDenied("The assigned BIDM review task is required.")
                    elif not locked.source_artifact_id and (
                        bidm_task is None or bidm_task.status not in ACTIVE
                    ):
                        raise PermissionDenied("The assigned BIDM War Room task is required.")
                    else:
                        note = request.POST.get('note', '').strip()
                        if action == 'approve':
                            locked.status = 'APPROVED'
                            locked.approved_by = emp
                            locked.approved_at = timezone.now()
                            locked.approval_note = note
                            locked.save(update_fields=[
                                'status', 'approved_by', 'approved_at',
                                'approval_note', 'updated_at',
                            ])
                            log_action(
                                emp, 'WAR_ROOM_ARTIFACT_APPROVED',
                                f'Approved {locked.title} v{locked.version_number}. {note}',
                                'WarRoomArtifact', locked.id, company,
                            )
                            if review_task:
                                _finish_task(review_task, emp, 'APPROVED', note)
                            messages.success(request, "Artifact approved.")
                        else:
                            locked.status = 'RETURNED'
                            locked.approval_note = note
                            locked.save(update_fields=[
                                'status', 'approval_note', 'updated_at',
                            ])
                            log_action(
                                emp, 'WAR_ROOM_ARTIFACT_RETURNED',
                                f'Returned {locked.title} v{locked.version_number}. {note}',
                                'WarRoomArtifact', locked.id, company,
                            )

                            if locked.source_artifact_id:
                                new_task = create_task(
                                    company,
                                    'WR_ARTIFACT',
                                    'BIDOP',
                                    f'Revise {locked.title} - {tender.serial_number}',
                                    'BIDM returned this revision. Prepare a new separate revision.',
                                    {
                                        'artifact_id': locked.source_artifact_id,
                                        'returned_revision_id': locked.id,
                                    },
                                    reverse(
                                        'war_room_artifact_detail',
                                        args=[tender.id, locked.source_artifact_id],
                                    ),
                                )
                                if new_task is None:
                                    raise ValueError("No designated BIDOP is available.")
                                _finish_task(review_task, emp, 'RETURNED', note)

                            messages.warning(request, "Artifact returned with your review note.")

                elif action == 'transfer':
                    if locked.source_artifact_id or locked.status != 'APPROVED':
                        messages.error(request, "Only an approved original artifact can be transferred.")
                    elif not bidm_task or bidm_task.status not in ACTIVE:
                        raise PermissionDenied("The active assigned BIDM War Room task is required.")
                    else:
                        existing = Task.objects.filter(
                            company=company,
                            code='WR_ARTIFACT',
                            status__in=ACTIVE,
                            payload__artifact_id=locked.id,
                        ).exists()

                        if existing:
                            messages.warning(request, "An active BIDOP task already exists.")
                        else:
                            new_task = create_task(
                                company,
                                'WR_ARTIFACT',
                                'BIDOP',
                                f'Prepare {locked.title} - {tender.serial_number}',
                                'Prepare a separate working revision. Do not overwrite the approved original.',
                                {'artifact_id': locked.id},
                                reverse(
                                    'war_room_artifact_detail',
                                    args=[tender.id, locked.id],
                                ),
                            )
                            if new_task is None:
                                messages.error(request, "No designated BIDOP is available. Nothing was transferred.")
                            else:
                                locked.status = 'TRANSFERRED'
                                locked.assigned_to = new_task.assigned_to
                                locked.task = new_task
                                locked.transferred_at = timezone.now()
                                locked.save(update_fields=[
                                    'status', 'assigned_to', 'task',
                                    'transferred_at', 'updated_at',
                                ])
                                log_action(
                                    emp, 'WAR_ROOM_ARTIFACT_TRANSFERRED',
                                    f'Transferred {locked.title} v{locked.version_number} to BIDOP',
                                    'WarRoomArtifact', locked.id, company,
                                )
                                messages.success(request, "Approved original transferred to BIDOP.")

            elif role == 'BIDOP':
                if bidop_task is None or bidop_task.status not in ACTIVE:
                    raise PermissionDenied("An active assigned BIDOP artifact task is required.")

                if action == 'upload_revision':
                    if locked.source_artifact_id or locked.status != 'TRANSFERRED':
                        messages.error(request, "Open the transferred original to upload a revision.")
                    else:
                        uploaded = request.FILES.get('revision_file')
                        if not uploaded:
                            messages.error(request, "Select a revision file.")
                        else:
                            extension = Path(uploaded.name).suffix.lower()
                            expected = {
                                'PDF': '.pdf',
                                'DOCX': '.docx',
                                'XLSX': '.xlsx',
                                'CSV': '.csv',
                            }.get(locked.file_format)

                            if extension != expected:
                                messages.error(
                                    request,
                                    f"This artifact requires a {expected or locked.file_format} file.",
                                )
                            elif uploaded.size > 50 * 1024 * 1024:
                                messages.error(request, "The revision exceeds the 50 MB limit.")
                            else:
                                content_hash = hashlib.sha256()
                                for chunk in uploaded.chunks():
                                    content_hash.update(chunk)
                                uploaded.seek(0)

                                latest = (
                                    WarRoomArtifact.objects.filter(
                                        tender=tender,
                                        artifact_type=locked.artifact_type,
                                        file_format=locked.file_format,
                                    ).order_by('-version_number')
                                    .values_list('version_number', flat=True)
                                    .first() or 0
                                )

                                revision = WarRoomArtifact(
                                    company=company,
                                    tender=tender,
                                    lot=locked.lot,
                                    task=bidop_task,
                                    artifact_type=locked.artifact_type,
                                    file_format=locked.file_format,
                                    title=locked.title,
                                    version_number=latest + 1,
                                    status='WORKING',
                                    original_filename=Path(uploaded.name).name,
                                    mime_type=getattr(uploaded, 'content_type', '') or '',
                                    file_size=uploaded.size,
                                    sha256=content_hash.hexdigest(),
                                    data=locked.data,
                                    provenance=locked.provenance,
                                    source_artifact=locked,
                                    assigned_to=emp,
                                    created_by=emp,
                                    is_required=locked.is_required,
                                )
                                revision.file.save(
                                    Path(uploaded.name).name,
                                    uploaded,
                                    save=False,
                                )
                                revision.save()
                                log_action(
                                    emp, 'WAR_ROOM_ARTIFACT_REVISION_CREATED',
                                    f'Created {locked.title} v{revision.version_number} from original v{locked.version_number}',
                                    'WarRoomArtifact', revision.id, company,
                                )
                                messages.success(
                                    request,
                                    f'Working revision v{revision.version_number} created. The original was not changed.',
                                )

                elif action == 'submit_revision':
                    if (
                        locked.source_artifact_id is None
                        or locked.status != 'WORKING'
                        or locked.task_id != bidop_task.id
                    ):
                        messages.error(request, "Only your active working revision can be submitted.")
                    else:
                        review = create_task(
                            company,
                            'WR_REVIEW',
                            'BIDM',
                            f'Review {locked.title} v{locked.version_number}',
                            'Review the BIDOP revision and approve it or return it with a note.',
                            {'artifact_id': locked.id},
                            reverse(
                                'war_room_artifact_detail',
                                args=[tender.id, locked.id],
                            ),
                        )
                        if review is None:
                            messages.error(request, "No designated BIDM is available. The revision remains with BIDOP.")
                        else:
                            locked.status = 'REVIEW'
                            locked.task = review
                            locked.assigned_to = review.assigned_to
                            locked.save(update_fields=[
                                'status', 'task', 'assigned_to', 'updated_at',
                            ])
                            _finish_task(
                                bidop_task, emp, 'SUBMITTED',
                                f'Submitted revision v{locked.version_number} to BIDM.',
                            )
                            log_action(
                                emp, 'WAR_ROOM_REVISION_SUBMITTED',
                                f'Submitted {locked.title} v{locked.version_number} to BIDM',
                                'WarRoomArtifact', locked.id, company,
                            )
                            messages.success(request, "Revision submitted to BIDM.")

        if role == 'BIDOP' and action == 'upload_revision' and locked.source_artifact_id is None:
            return redirect('war_room_artifact_detail', pk=tender.id, artifact_id=locked.id)
        return redirect('war_room_artifact_detail', pk=tender.id, artifact_id=artifact.id)

    revisions = WarRoomArtifact.objects.filter(
        company=company,
        tender=tender,
        source_artifact=source,
    ).order_by('-version_number', '-created_at')

    return render(request, 'war_room_artifact_detail.html', {
        'tender': tender,
        'artifact': artifact,
        'source': source,
        'revisions': revisions,
        'role': role,
    })


@login_required
def download_war_room_artifact_file(request, pk, artifact_id, file_id):
    (
        emp, company, membership, tender, artifact, source,
        role, bidm_task, review_task, bidop_task,
    ) = _context(request, pk, artifact_id)

    file_artifact = get_object_or_404(
        WarRoomArtifact,
        pk=file_id,
        company=company,
        tender=tender,
    )

    allowed = file_artifact.id == artifact.id
    if file_artifact.source_artifact_id:
        allowed = file_artifact.source_artifact_id == source.id

    if not allowed or not file_artifact.file:
        raise Http404("Artifact file is unavailable.")

    with file_artifact.file.open('rb') as stored:
        response = HttpResponse(
            stored.read(),
            content_type=file_artifact.mime_type or 'application/octet-stream',
        )
    response['Content-Disposition'] = (
        f'attachment; filename="{Path(file_artifact.original_filename or file_artifact.file.name).name}"'
    )
    return response
