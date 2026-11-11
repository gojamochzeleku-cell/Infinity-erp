import base64
import json
import os
import io
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.conf import settings
from .gemini_file_api import upload_to_google_file_api

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

MODELS_TO_TRY = ['gemini-3-flash-preview', 'gemini-flash-latest']

TENDER_PROMPT = """You are an expert procurement document analyst for an Ethiopian import company.

Read the ENTIRE attached tender document carefully, including all pages, tables, annexes, schedules and lot descriptions.

Extract the information into ONE valid JSON object only. Do not include Markdown or explanations.

IMPORTANT LOT EXTRACTION RULES:
1. Extract EVERY distinct lot separately. Never merge different lots.
2. Extract ALL items belonging to each lot. Do not omit items from tables.
3. Each lot may have its own CPO amount or percentage, guarantee requirements, submission deadline and opening schedule. Keep these details attached to the correct lot.
4. Do not assume that all lots share the same dates, times or CPO.
5. If the document has no lot divisions, create one "General" lot and include the identified items.
6. Preserve item names, quantities and units as written.
7. Never invent missing information. Use null for unknown values.
8. Dates must use YYYY-MM-DD. Times must use 24-hour HH:MM format.
9. Distinguish a fixed CPO amount from a percentage. Use AMOUNT or PERCENT.
10. Include guarantee type, validity, issuing bank requirements and other security conditions in guarantee_details when stated.

Use this exact JSON structure:

{
  "bid_reference_number": null,
  "bid_title": null,
  "bid_type": null,
  "purchaser_name": null,
  "organization_type": null,
  "published_date": null,
  "bidder_location": null,
  "contact_phone": null,
  "bid_document_cost_amount": null,
  "bid_document_cost_payment_mode": null,
  "account_holder_name": null,
  "bank_name": null,
  "bank_account_number": null,
  "lots": [
    {
      "lot_name": null,
      "description": null,
      "cpo_amount": null,
      "cpo_type": null,
      "guarantee_details": null,
      "submission_date": null,
      "submission_time": null,
      "opening_date": null,
      "opening_time": null,
      "items": [
        {
          "item_name": null,
          "description": null,
          "quantity": null,
          "unit": null
        }
      ]
    }
  ]
}

Return valid JSON only. Ensure every lot and every identifiable item in the document is included.
"""

ANALYSIS_PROMPT = """
You are Infinity ERP's senior procurement intelligence, tender compliance,
technical evaluation and import-strategy analyst.

MISSION
Read and analyze the ENTIRE attached tender PDF, including every page,
table, schedule, annex, footnote, form and lot. This is a professional
procurement decision-support report for an Ethiopian tender-based importer.

NON-NEGOTIABLE EVIDENCE RULES
1. Never invent a tender requirement, price, supplier, brand, quotation,
   authorization, certificate, deadline, page number or market fact.
2. Separate VERIFIED TENDER FACTS, REGISTERED COMPANY DATA,
   ANALYST RECOMMENDATIONS and UNKNOWN / NOT FOUND information.
3. Every important tender finding must cite its actual PDF page number
   or identifiable section. Never fabricate a page reference.
4. Preserve exact technical values, units, quantities, model numbers,
   standards, tolerances and wording where material.
5. Do not silently resolve contradictions. Explain both conflicting
   statements and identify the page/section for each.
6. Do not call a supplier eligible, authorized, compliant or available
   unless the supplied evidence establishes it.
7. Do not invent current market prices. Mark prices requiring quotations
   as QUOTATION REQUIRED.
8. Analyze each lot independently. Never merge lots or transfer one lot's
   deadline, guarantee or specification to another lot.
9. Identify illegible, missing, incomplete or ambiguous pages and tables.
10. If a field cannot be verified, use UNKNOWN or NOT FOUND.

DEPTH AND COVERAGE
Read the complete document before producing the report. Extract every
identifiable administrative, legal, technical, commercial and delivery
requirement. Include every identifiable item and lot. Do not intentionally
summarize away material conditions.

Return ONE valid JSON object only, with exactly these top-level keys:

{
  "executive_summary": "",
  "tender_facts": [
    {"field":"","value":"","source_page":""}
  ],
  "submission_checklist": [
    {"task":"","mandatory":"YES/NO/UNKNOWN",
     "responsible_document_or_person":"","evidence_or_action":"",
     "source_page":"","risk_if_missed":""}
  ],
  "technical_requirements": [
    {"id":"","lot":"","item":"","requirement":"",
     "exact_tender_specification":"","mandatory":"YES/NO/UNKNOWN",
     "evidence_required":"","supplier_response":"NOT PROVIDED",
     "compliance_status":"NOT VERIFIED","remarks":"",
     "source_page":""}
  ],
  "lot_analysis": [
    {"lot":"","scope_and_items":"","quantities_and_units":"",
     "lot_specific_deadline":"","bid_security_or_cpo":"",
     "technical_conditions":"","commercial_conditions":"",
     "risks_and_open_questions":"","source_page":""}
  ],
  "commercial_analysis": [
    {"topic":"","tender_condition":"","commercial_implication":"",
     "recommended_action":"","source_page":""}
  ],
  "brand_sourcing": [
    {"item_or_lot":"","specified_brand_or_standard":"",
     "equivalent_policy":"","manufacturer_or_origin_condition":"",
     "authorization_required":"","sourcing_options_to_investigate":"",
     "verification_needed":"","source_page":""}
  ],
  "supplier_strategy": [
    {"supplier_or_supplier_type":"","relevant_scope":"",
     "known_registered_information":"","required_qualification_or_evidence":"",
     "gaps_to_verify":"","recommended_action":""}
  ],
  "bid_strategy": [
    {"priority":"HIGH/MEDIUM/LOW","action":"",
     "reason":"","responsible_role":"",
     "dependency_or_deadline":"","evidence_or_source":""}
  ],
  "risk_register": [
    {"risk":"","likelihood":"LOW/MEDIUM/HIGH/UNKNOWN",
     "impact":"LOW/MEDIUM/HIGH/UNKNOWN",
     "mitigation":"","source_page_or_basis":""}
  ],
  "missing_information": [
    {"missing_or_unclear_information":"","why_it_matters":"",
     "how_to_verify":"","source_page":""}
  ],
  "source_register": [
    {"page_or_section":"","information_supported":""}
  ]
}

ANALYSIS INSTRUCTIONS

A. TENDER FACTS
Identify purchaser, tender title/reference, publication date, procurement
method, document fee, submission location/method, deadline and time,
opening schedule, bid validity, bid security/CPO, delivery location,
delivery period, payment terms, currency and contact details when stated.

B. SUBMISSION CHECKLIST
Identify every required form, eligibility document, registration,
tax/VAT evidence, licenses, audited accounts, experience evidence,
manufacturer authorization, power of attorney, signatures, stamps,
bid security, technical envelope, financial envelope, copies, sealing,
labeling and submission instruction. Only include items supported by
the tender; mark uncertain requirements UNKNOWN.

C. TECHNICAL REQUIREMENTS
Create a separate row for every identifiable requirement and item.
Preserve exact model, capacity, dimensions, performance, standards,
compatibility, accessories, software, licenses, warranty, installation,
training, documentation and service requirements. Include administrative
requirements in this matrix where they affect compliance.
Do not mark a supplier compliant without its actual response and evidence.

D. LOT ANALYSIS
List every lot independently, its item descriptions, quantities and units,
its own deadline, security, delivery and technical/commercial conditions.
Identify lots with missing item detail or conflicting information.

E. COMMERCIAL ANALYSIS
Analyze currency, price schedule, taxes, duties, delivery/incoterms,
payment, advance payment, retention, guarantees, penalties, liquidated
damages, warranty costs, validity, price adjustment, delivery risk and
other cost exposure. Distinguish tender facts from recommendations.
Do not calculate a landed cost without the required input data.

F. BRAND SOURCING
Identify named brands, models, standards and permitted equivalents.
Identify manufacturer authorization and country-of-origin conditions.
Suggest sourcing routes or alternative manufacturers only as research
leads requiring verification, never as approved tender alternatives.

G. SUPPLIER STRATEGY
Use the registered supplier context provided separately. Match only
documented supplier capabilities to tender scope. Identify missing
catalogues, authorization, references, certificates, quotations,
delivery commitments, warranty and after-sales evidence.
If no matching registered supplier is documented, say so.

H. BID STRATEGY
Produce prioritized practical actions for the Bid Manager, Bid Operator,
Finance and management. Identify dependencies and deadline-sensitive
actions. Do not make unsupported claims about probability of winning.

I. RISK REGISTER AND INFORMATION GAPS
Identify disqualification risks, ambiguous specifications, short lead
times, security exposure, delivery and warranty obligations, currency
and import risks, missing evidence and clarification questions.

OUTPUT QUALITY
Be detailed, structured and commercially useful. Prefer complete
requirement coverage over a short generic summary. Use concise factual
entries, not vague advice. Return valid JSON only, without Markdown fences.
"""



def _api_key():
    key = os.environ.get('GEMINI_API_KEY', '') or getattr(settings, 'GEMINI_API_KEY', '')
    if key in ('', 'PASTE_YOUR_GEMINI_API_KEY_HERE'):
        return ''
    return key

def compress_pdf_if_needed(file_obj, max_size_mb=3.5):
    if fitz is None:
        return _get_file_bytes(file_obj)

    try:
        if hasattr(file_obj, 'storage'):
            file_obj.open()
            raw = file_obj.read()
            file_obj.close()
        elif hasattr(file_obj, 'read'):
            file_obj.seek(0)
            raw = file_obj.read()
        else:
            with open(file_obj, 'rb') as f:
                raw = f.read()

        if len(raw) <= max_size_mb * 1024 * 1024:
            return raw

        doc = fitz.open(stream=raw, filetype="pdf")
        out_buffer = io.BytesIO()
        doc.save(out_buffer, garbage=4, deflate=True)
        doc.close()
        return out_buffer.getvalue()
    except Exception as e:
        print(f"Compression failed: {e}")
        return _get_file_bytes(file_obj)

def _get_file_bytes(file_obj):
    try:
        if hasattr(file_obj, 'storage'):
            file_obj.open()
            raw = file_obj.read()
            file_obj.close()
            return raw
        elif hasattr(file_obj, 'read'):
            file_obj.seek(0)
            return file_obj.read()
        else:
            with open(file_obj, 'rb') as f:
                return f.read()
    except Exception as e:
        raise Exception(f"Could not read file: {str(e)}")

def _send_to_gemini(file_obj, prompt):
    key = _api_key()
    if not key:
        return "GEMINI_API_KEY not set. Configure it in Render Environment Variables."

    try:
        raw = compress_pdf_if_needed(file_obj, max_size_mb=3.5)
    except Exception as exc:
        return f"Could not read tender PDF: {exc}"

    if not raw:
        return "Tender PDF is empty."

    if len(raw) > 4 * 1024 * 1024:
        try:
            uri, error = upload_to_google_file_api(raw, "tender_document.pdf")
            if error:
                return f"File API Error: {error}"
            body = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"file_data": {
                            "mime_type": "application/pdf",
                            "file_uri": uri
                        }}
                    ]
                }]
            }
        except Exception as exc:
            return f"Failed to upload large PDF: {exc}"
    else:
        encoded = base64.b64encode(raw).decode("ascii")
        body = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {
                        "mime_type": "application/pdf",
                        "data": encoded
                    }}
                ]
            }]
        }

    # Apply the same JSON response configuration to BOTH upload paths.
    body["generationConfig"] = {
        "response_mime_type": "application/json",
        "temperature": 0.15,
        "maxOutputTokens": 16000
    }

    session = requests.Session()
    retry = Retry(
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504]
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    headers = {"User-Agent": "Infinity-ERP-Procurement-Analyst"}

    last_error = "Unknown Gemini error"

    for model in MODELS_TO_TRY:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        try:
            response = session.post(
                url,
                params={"key": key},
                json=body,
                headers=headers,
                timeout=240
            )

            if response.status_code == 200:
                payload = response.json()
                candidates = payload.get("candidates") or []
                if not candidates:
                    last_error = (
                        payload.get("promptFeedback", {}).get("blockReason")
                        or "Gemini returned no analysis candidate."
                    )
                    continue

                candidate = candidates[0]
                parts = candidate.get("content", {}).get("parts", [])
                result = "\n".join(
                    part.get("text", "")
                    for part in parts
                    if part.get("text")
                ).strip()

                if not result:
                    last_error = (
                        "Gemini returned an empty response. Finish reason: "
                        + str(candidate.get("finishReason", "UNKNOWN"))
                    )
                    continue

                if candidate.get("finishReason") == "MAX_TOKENS":
                    last_error = (
                        "Gemini response reached its output limit. "
                        "The analysis may be incomplete; retry or use a smaller PDF."
                    )
                    continue

                result = re.sub(
                    r"^```(?:json)?\s*|\s*```$",
                    "",
                    result,
                    flags=re.IGNORECASE
                ).strip()
                return result

            last_error = (
                f"Gemini HTTP {response.status_code}: "
                f"{response.text[:1200]}"
            )
            if response.status_code not in (404, 429, 500, 502, 503, 504):
                break

        except Exception as exc:
            last_error = str(exc)

    return f"AI analysis failed. {last_error}"



def extract_tender_from_pdf(file_obj):
    result_text = _send_to_gemini(file_obj, TENDER_PROMPT)
    if result_text.startswith("{"):
        try:
            return {'data': json.loads(result_text)}
        except Exception as e:
            return {'error': f"JSON parse error: {e}. Raw: {result_text[:100]}"}
    return {'error': result_text}

def analyze_bid_document(file_obj, supplier_context=""):
    prompt = ANALYSIS_PROMPT
    if supplier_context:
        prompt += (
            "\n\nREGISTERED COMPANY SUPPLIER RECORDS "
            "(REFERENCE DATA ONLY; NOT PROOF OF ELIGIBILITY):\n"
            + supplier_context
            + "\nUse these records only to identify possible matches and "
              "evidence gaps. Do not invent capabilities, quotations, "
              "authorization, stock or delivery commitments."
        )

    result = _send_to_gemini(file_obj, prompt)
    error_prefixes = (
        "GEMINI_API_KEY",
        "Could not read",
        "Tender PDF is empty",
        "File API Error",
        "Failed to upload",
        "AI analysis failed",
        "Gemini Error",
        "All AI models failed",
    )
    if result.startswith(error_prefixes):
        raise RuntimeError(result)

    return result


def _display_value(value):
    if value is None:
        return "UNKNOWN"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(_display_value(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(
            f"{key}: {_display_value(val)}"
            for key, val in value.items()
        )
    return str(value)


def _format_records(records, fields):
    if not isinstance(records, list):
        return _display_value(records) if records else ""
    output = []
    for index, record in enumerate(records, 1):
        if isinstance(record, dict):
            lines = []
            for key, label in fields:
                value = record.get(key)
                if value not in (None, "", [], {}):
                    lines.append(f"{label}: {_display_value(value)}")
            if lines:
                output.append(f"{index}. " + "\n   ".join(lines))
        elif record:
            output.append(f"{index}. {_display_value(record)}")
    return "\n\n".join(output)


def parse_war_room_output(text):
    """
    Accept the current structured JSON response and legacy section headings.
    Convert structured technical requirements into an editable CSV matrix.
    """
    import csv
    import json
    import re

    if not text or not str(text).strip():
        raise ValueError("Gemini returned an empty analysis.")

    cleaned = str(text).strip()
    cleaned = re.sub(
        r"^```(?:json|text)?\s*|\s*```$",
        "",
        cleaned,
        flags=re.IGNORECASE
    ).strip()

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        data = None

    if isinstance(data, dict):
        summary_parts = []

        executive = data.get("executive_summary")
        if executive:
            summary_parts.append("EXECUTIVE SUMMARY\n" + _display_value(executive))

        summary_fields = [
            ("tender_facts", "TENDER FACTS", [
                ("field", "Field"), ("value", "Value"),
                ("source_page", "Source page")
            ]),
            ("lot_analysis", "LOT-BY-LOT ANALYSIS", [
                ("lot", "Lot"), ("scope_and_items", "Scope and items"),
                ("quantities_and_units", "Quantities and units"),
                ("lot_specific_deadline", "Deadline"),
                ("bid_security_or_cpo", "Bid security / CPO"),
                ("technical_conditions", "Technical conditions"),
                ("commercial_conditions", "Commercial conditions"),
                ("risks_and_open_questions", "Risks / questions"),
                ("source_page", "Source page")
            ]),
            ("commercial_analysis", "COMMERCIAL ANALYSIS", [
                ("topic", "Topic"), ("tender_condition", "Tender condition"),
                ("commercial_implication", "Implication"),
                ("recommended_action", "Recommended action"),
                ("source_page", "Source page")
            ]),
            ("bid_strategy", "RECOMMENDED BID STRATEGY", [
                ("priority", "Priority"), ("action", "Action"),
                ("reason", "Reason"), ("responsible_role", "Responsible role"),
                ("dependency_or_deadline", "Dependency / deadline"),
                ("evidence_or_source", "Evidence / source")
            ]),
            ("risk_register", "RISK REGISTER", [
                ("risk", "Risk"), ("likelihood", "Likelihood"),
                ("impact", "Impact"), ("mitigation", "Mitigation"),
                ("source_page_or_basis", "Source / basis")
            ]),
            ("missing_information", "MISSING INFORMATION AND CLARIFICATIONS", [
                ("missing_or_unclear_information", "Missing information"),
                ("why_it_matters", "Why it matters"),
                ("how_to_verify", "How to verify"),
                ("source_page", "Source page")
            ]),
            ("source_register", "SOURCE REGISTER", [
                ("page_or_section", "Page / section"),
                ("information_supported", "Information supported")
            ]),
        ]

        for key, heading, fields in summary_fields:
            rendered = _format_records(data.get(key, []), fields)
            if rendered:
                summary_parts.append(heading + "\n" + rendered)

        checklist = _format_records(
            data.get("submission_checklist", []),
            [
                ("task", "Required action"),
                ("mandatory", "Mandatory"),
                ("responsible_document_or_person", "Responsible document/person"),
                ("evidence_or_action", "Evidence/action"),
                ("source_page", "Source page"),
                ("risk_if_missed", "Risk if missed")
            ]
        )

        brands = _format_records(
            data.get("brand_sourcing", []),
            [
                ("item_or_lot", "Item / lot"),
                ("specified_brand_or_standard", "Specified brand / standard"),
                ("equivalent_policy", "Equivalent policy"),
                ("manufacturer_or_origin_condition", "Manufacturer / origin"),
                ("authorization_required", "Authorization"),
                ("sourcing_options_to_investigate", "Sourcing leads to verify"),
                ("verification_needed", "Verification required"),
                ("source_page", "Source page")
            ]
        )

        suppliers = _format_records(
            data.get("supplier_strategy", []),
            [
                ("supplier_or_supplier_type", "Supplier"),
                ("relevant_scope", "Relevant scope"),
                ("known_registered_information", "Registered information"),
                ("required_qualification_or_evidence", "Required evidence"),
                ("gaps_to_verify", "Gaps to verify"),
                ("recommended_action", "Recommended action")
            ]
        )

        compliance_rows = data.get("technical_requirements", [])
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        headers = [
            "Requirement ID", "Lot", "Item", "Requirement",
            "Tender Specification", "Mandatory", "Evidence Required",
            "Supplier Response", "Compliance Status", "Remarks",
            "Source Page"
        ]
        writer.writerow(headers)

        if isinstance(compliance_rows, list):
            for row in compliance_rows:
                if not isinstance(row, dict):
                    continue
                writer.writerow([
                    _display_value(row.get("id", "")),
                    _display_value(row.get("lot", "")),
                    _display_value(row.get("item", "")),
                    _display_value(row.get("requirement", "")),
                    _display_value(row.get("exact_tender_specification", "")),
                    _display_value(row.get("mandatory", "UNKNOWN")),
                    _display_value(row.get("evidence_required", "")),
                    _display_value(row.get("supplier_response", "NOT PROVIDED")),
                    _display_value(row.get("compliance_status", "NOT VERIFIED")),
                    _display_value(row.get("remarks", "")),
                    _display_value(row.get("source_page", ""))
                ])

        result = {
            "summary": "\n\n".join(summary_parts),
            "checklist": checklist,
            "compliance": csv_buffer.getvalue().strip(),
            "brands": brands,
            "suppliers": suppliers,
            "structured_data": data,
        }

        if not any(result.values()):
            raise ValueError("AI response contained no usable analysis sections.")
        return result

    # Backward compatibility for previously generated heading-based output.
    keys = {
        "SUMMARY": "summary",
        "CHECKLIST": "checklist",
        "COMPLIANCE CSV": "compliance",
        "COMMERCIAL": "commercial",
        "BRANDS": "brands",
        "SUPPLIERS": "suppliers",
        "SOURCES": "sources",
    }
    result = {value: "" for value in keys.values()}
    result["structured_data"] = {}
    current = None
    collected = {value: [] for value in keys.values()}

    for line in cleaned.splitlines():
        heading = line.strip().strip("-").strip().upper()
        if heading in keys:
            current = keys[heading]
            continue
        if current:
            collected[current].append(line)

    for key, lines in collected.items():
        result[key] = "\n".join(lines).strip()

    if not any(result.values()):
        raise ValueError(
            "AI response was not valid structured JSON or recognized analysis sections."
        )

    result["summary"] = (
        result.get("summary", "")
        + "\n\nCOMMERCIAL REVIEW\n" + result.get("commercial", "")
        + "\n\nSOURCE REFERENCES\n" + result.get("sources", "")
    ).strip()
    return result



def ask_infinity_brain(question, context_data):
    key = _api_key()
    if not key: return "API key missing."
    prompt = f"Company Data:\n{context_data}\n\nQuestion: {question}\nAnswer concisely:"
    body = {'contents': [{'parts': [{'text': prompt}]}]}
    session = requests.Session()
    for model in MODELS_TO_TRY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = session.post(url, params={'key': key}, json=body, timeout=60)
            if r.status_code == 200:
                return r.json()['candidates'][0]['content']['parts'][0]['text']
        except: continue
    return "AI is busy."

def predict_winning_price(tender, historical_context):
    key = _api_key()
    if not key: return "API key missing."
    prompt = f"Tender: {tender.bid_title}\nCost: {tender.total_cost}\nHistory: {historical_context}\nSuggest a winning price:"
    body = {'contents': [{'parts': [{'text': prompt}]}]}
    session = requests.Session()
    for model in MODELS_TO_TRY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = session.post(url, params={'key': key}, json=body, timeout=60)
            if r.status_code == 200:
                return r.json()['candidates'][0]['content']['parts'][0]['text']
        except: continue
    return "AI is busy."
