import hashlib
import mimetypes

from django.db import transaction
from django.utils import timezone

from .models import BidDocumentVersion, Tender


@transaction.atomic
def record_bid_document_version(tender, employee=None, notes='', original_filename=None):
    tender = Tender.objects.select_for_update().get(
        pk=tender.pk, company=tender.company
    )

    document = tender.purchased_bid_document
    if not document or not document.name:
        raise ValueError('No purchased bid document is stored.')

    storage = document.storage
    storage_name = document.name

    if not storage.exists(storage_name):
        raise ValueError('The purchased bid document is missing from storage.')

    digest = hashlib.sha256()
    file_size = 0

    with storage.open(storage_name, 'rb') as stored_file:
        for chunk in stored_file.chunks():
            digest.update(chunk)
            file_size += len(chunk)

    original_filename = (original_filename or storage_name.rsplit('/', 1)[-1])[:255]
    mime_type = mimetypes.guess_type(original_filename)[0] or ''

    metadata = {
        'tender_serial_number': tender.serial_number,
        'tender_details': {
            'bid_reference_number': tender.bid_reference_number,
            'bid_title': tender.bid_title,
            'purchaser_name': tender.purchaser_name,
            'bid_submission_date': (
                tender.bid_submission_date.isoformat()
                if tender.bid_submission_date else None
            ),
            'bid_opening_date': (
                tender.bid_opening_date.isoformat()
                if tender.bid_opening_date else None
            ),
        },
        'lots': [
            {
                'lot_name': lot.lot_name,
                'description': lot.description,
                'items': [
                    {
                        'item_name': item.item_name,
                        'description': item.description,
                        'unit': item.unit,
                        'quantity': str(item.quantity),
                    }
                    for item in lot.items.all()
                ],
            }
            for lot in tender.lots.all()
        ],
        'ai_extracted': tender.ai_extracted,
        'ai_status': tender.ai_status,
        'ai_error': tender.ai_error,
        'ai_summary': tender.ai_summary,
        'war_room_summary': tender.war_room_summary,
        'war_room_brands': tender.war_room_brands,
        'war_room_suppliers': tender.war_room_suppliers,
        'war_room_checklist': tender.war_room_checklist,
        'war_room_compliance': tender.war_room_compliance,
        'document_uploaded_at': (
            tender.bid_document_uploaded_at.isoformat()
            if tender.bid_document_uploaded_at else None
        ),
    }

    latest = (
        BidDocumentVersion.objects
        .filter(tender=tender)
        .order_by('-version_number')
        .first()
    )
    version_number = latest.version_number + 1 if latest else 1

    return BidDocumentVersion.objects.create(
        tender=tender,
        company=tender.company,
        version_number=version_number,
        original_filename=original_filename,
        storage_name=storage_name,
        file_size=file_size,
        sha256=digest.hexdigest(),
        mime_type=mime_type,
        uploaded_by=employee,
        uploaded_at=timezone.now(),
        metadata=metadata,
        notes=notes,
    )
