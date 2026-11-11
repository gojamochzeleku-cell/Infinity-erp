from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Tender, BidDocumentVersion
from core.bid_documents import record_bid_document_version
from core.chain import find_role
from core.services import notify, log_action


ELIGIBLE_STATUSES = ['NO_GO', 'CEO_REJECTED', 'CANCELLED']


class Command(BaseCommand):
    help = 'Safely archives eligible closed-tender PDFs before purging.'

    def handle(self, *args, **kwargs):
        if not getattr(settings, 'BID_DOCUMENT_PURGE_ENABLED', False):
            self.stdout.write(self.style.WARNING(
                'Purge disabled. No documents were deleted.'
            ))
            return

        retention_days = max(
            90, int(getattr(settings, 'BID_DOCUMENT_RETENTION_DAYS', 90))
        )
        cutoff = timezone.now() - timedelta(days=retention_days)

        tenders = Tender.objects.filter(
            status__in=ELIGIBLE_STATUSES,
            updated_at__lt=cutoff,
            bid_document_uploaded_at__isnull=False,
            bid_document_purged=False,
        ).exclude(purchased_bid_document='')

        purged = skipped = warnings = 0

        for candidate in tenders.iterator():
            try:
                storage_name = candidate.purchased_bid_document.name
                if not storage_name:
                    continue

                storage = candidate.purchased_bid_document.storage

                version = BidDocumentVersion.objects.filter(
                    tender=candidate,
                    storage_name=storage_name,
                ).first()

                if version is None:
                    version = record_bid_document_version(
                        candidate,
                        notes='Captured before automatic document purge',
                    )

                # Stage 1: persist the recovery marker before touching storage.
                with transaction.atomic():
                    locked = Tender.objects.select_for_update().get(
                        pk=candidate.pk,
                        company=candidate.company,
                    )
                    locked_version = BidDocumentVersion.objects.select_for_update().get(
                        pk=version.pk,
                        tender=locked,
                    )

                    if (
                        locked.status not in ELIGIBLE_STATUSES
                        or locked.updated_at >= cutoff
                        or locked.bid_document_purged
                        or locked.purchased_bid_document.name != storage_name
                        or locked_version.is_purged
                    ):
                        skipped += 1
                        continue

                    if not locked_version.purge_pending:
                        locked_version.purge_pending = True
                        locked_version.save(update_fields=['purge_pending'])

                # Stage 2: delete while holding the tender lock, then finalize.
                with transaction.atomic():
                    locked = Tender.objects.select_for_update().get(
                        pk=candidate.pk,
                        company=candidate.company,
                    )
                    locked_version = BidDocumentVersion.objects.select_for_update().get(
                        pk=version.pk,
                        tender=locked,
                    )

                    if not locked_version.purge_pending:
                        skipped += 1
                        continue

                    if (
                        locked.status not in ELIGIBLE_STATUSES
                        or locked.updated_at >= cutoff
                        or locked.purchased_bid_document.name != storage_name
                    ):
                        locked_version.purge_pending = False
                        locked_version.save(update_fields=['purge_pending'])
                        skipped += 1
                        continue

                    # A missing file is treated as a previous interrupted
                    # deletion. Finalize its database state.
                    if storage.exists(storage_name):
                        storage.delete(storage_name)

                    if storage.exists(storage_name):
                        raise RuntimeError(
                            'PDF remains in storage after deletion.'
                        )

                    locked_version.is_purged = True
                    locked_version.purge_pending = False
                    locked_version.purged_at = timezone.now()
                    locked_version.save(update_fields=[
                        'is_purged',
                        'purge_pending',
                        'purged_at',
                    ])

                    locked.purchased_bid_document = ''
                    locked.bid_document_purged = True
                    locked.save(update_fields=[
                        'purchased_bid_document',
                        'bid_document_purged',
                    ])

                purged += 1

                try:
                    log_action(
                        actor=None,
                        company=candidate.company,
                        action='BID_DOCUMENT_PURGED',
                        detail='Purged document v%s for tender %s after %s days.'
                        % (
                            version.version_number,
                            candidate.serial_number,
                            retention_days,
                        ),
                        object_type='Tender',
                        object_id=str(candidate.pk),
                    )

                    office = find_role(candidate.company, 'OFFICE')
                    if office:
                        notify(
                            office,
                            'Bid Document Archived',
                            'The PDF for tender %s was purged. Version history '
                            'and analysis records are retained.'
                            % candidate.serial_number,
                        )
                except Exception as exc:
                    warnings += 1
                    self.stderr.write(
                        'Post-purge warning for %s: %s'
                        % (candidate.serial_number, exc)
                    )

            except Exception as exc:
                skipped += 1
                self.stderr.write(
                    'Skipped tender %s: %s'
                    % (candidate.serial_number, exc)
                )

        self.stdout.write(self.style.SUCCESS(
            'Purged: %s | Skipped: %s | Warnings: %s | Retention: %s days'
            % (purged, skipped, warnings, retention_days)
        ))
