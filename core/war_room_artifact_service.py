"""Build and persist BIDM War Room documents from saved company records."""
import hashlib

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max

from .models import Tender, TenderLot, Supplier, WarRoomArtifact
from .war_room_documents import ARTIFACTS, render_artifact


def _value(obj, field):
    value = getattr(obj, field, None)
    return str(value) if value is not None else ""


def build_war_room_payload(tender, company):
    analysis = tender.war_room_data if isinstance(tender.war_room_data, dict) else {}

    lots = []
    for lot in TenderLot.objects.filter(tender=tender).prefetch_related("items"):
        items = []
        for item in lot.items.all():
            items.append({
                "Item": _value(item, "item_name"),
                "Description": _value(item, "description"),
                "Quantity": _value(item, "quantity"),
                "Unit": _value(item, "unit"),
                "Unit Price ETB": _value(item, "unit_price"),
                "Total Price ETB": _value(item, "total_price"),
                "Proposed Brand": _value(item, "proposed_brand"),
                "Compliance Status": _value(item, "compliance_status"),
            })

        lots.append({
            "Lot": _value(lot, "lot_name"),
            "Description": _value(lot, "description"),
            "CPO Amount ETB": _value(lot, "cpo_amount"),
            "CPO Type": _value(lot, "cpo_type"),
            "Guarantee Details": _value(lot, "guarantee_details"),
            "Submission Date": _value(lot, "submission_date"),
            "Submission Time": _value(lot, "submission_time"),
            "Opening Date": _value(lot, "opening_date"),
            "Opening Time": _value(lot, "opening_time"),
            "Status": _value(lot, "status"),
            "Landed Cost ETB": _value(lot, "landed_cost"),
            "Final Price ETB": _value(lot, "final_price"),
            "Items": items,
        })

    suppliers = list(
        Supplier.objects.filter(company=company).values(
            "name", "contact_person", "phone", "email",
            "rating", "supplier_rating", "notes",
        )
    )

    tender_fields = (
        "serial_number", "bid_reference_number", "bid_title",
        "purchaser_name", "organization_type", "published_date",
        "bid_submission_date", "bid_opening_date",
        "bid_document_cost_amount", "bid_document_cost_payment_mode",
        "cpo_amount", "cpo_type",
    )
    tender_data = {field: _value(tender, field) for field in tender_fields}

    return {
        "company": _value(company, "name"),
        "tender": tender_data,
        "analysis": analysis,
        "lots": lots,
        "registered_suppliers": suppliers,
        "analysis_status": _value(tender, "ai_status") or "NOT STARTED",
        "provenance": {
            "Tender identification and saved lot/item records": "TENDER_EXTRACTED",
            "Structured War Room findings": (
                "AI_ASSESSMENT" if analysis else "UNKNOWN"
            ),
            "Registered company supplier records": "UNKNOWN",
            "Supplier authorization, stock, pricing and delivery": "UNKNOWN",
            "External market verification": "UNKNOWN",
        },
    }


@transaction.atomic
def generate_war_room_artifacts(tender, company, employee, task):
    # Lock the tender while assigning version numbers.
    tender = Tender.objects.select_for_update().get(
        pk=tender.pk, company=company
    )
    payload = build_war_room_payload(tender, company)
    created = []

    for artifact_type, spec in ARTIFACTS.items():
        for file_format in spec["formats"]:
            latest = (
                WarRoomArtifact.objects.filter(
                    tender=tender,
                    artifact_type=artifact_type,
                    file_format=file_format,
                ).aggregate(value=Max("version_number"))["value"] or 0
            )
            version = latest + 1

            content, mime_type, filename = render_artifact(
                artifact_type, file_format, payload
            )

            artifact = WarRoomArtifact(
                company=company,
                tender=tender,
                task=task,
                artifact_type=artifact_type,
                file_format=file_format,
                title=spec["title"],
                version_number=version,
                status="DRAFT",
                original_filename=filename,
                mime_type=mime_type,
                file_size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                data=payload,
                provenance=(
                    "AI_ASSESSMENT"
                    if payload["analysis"]
                    else "TENDER_EXTRACTED"
                ),
                created_by=employee,
            )
            artifact.file.save(filename, ContentFile(content), save=False)
            artifact.save()
            created.append(artifact)

    return created
