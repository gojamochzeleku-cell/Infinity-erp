import base64
import json
import os
import io
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
You are Infinity ERP's professional procurement and tender analyst.
Analyze the uploaded tender document carefully. Never invent missing requirements.
Return these sections using the exact headings shown:

--- SUMMARY ---
Tender title, procuring entity, reference number, submission deadline,
bid validity, bid security, delivery requirements and critical conditions.

--- CHECKLIST ---
A practical submission checklist. Identify mandatory documents,
forms, signatures, stamps, eligibility and submission instructions.

--- COMPLIANCE CSV ---
Return CSV only, with columns:
Requirement, Tender Specification, Mandatory, Evidence Required,
Supplier Response, Compliance Status, Remarks.
Include every identifiable technical and administrative requirement.
Use UNKNOWN where the document does not provide enough information.

--- COMMERCIAL ---
Currency, quantities, delivery terms, payment terms, taxes, duties,
price schedules, guarantees, penalties, delivery period and commercial risks.
Flag unclear or conflicting terms.

--- BRANDS ---
Required brands, approved manufacturers, acceptable equivalents,
country of origin and manufacturer authorization requirements.

--- SUPPLIERS ---
Supplier qualifications, manufacturer/distributor requirements,
authorization documents, warranty, after-sales service and local support.

--- SOURCES ---
List document page numbers or section references supporting key findings.
Clearly identify missing, unreadable or ambiguous information.

Be precise, concise and commercially practical.
Do not assume that a requirement is compliant without evidence.
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
        return "GEMINI_API_KEY not set. Edit Render Environment Variables."
    
    try:
        raw = compress_pdf_if_needed(file_obj, max_size_mb=3.5)
    except Exception as e:
        return str(e)

    # Use File API for large files
    if len(raw) > 4 * 1024 * 1024:
        try:
            uri, error = upload_to_google_file_api(raw, "tender_document.pdf")
            if error:
                return f"File API Error: {error}"
            body = {
                'contents': [{'parts': [{'text': prompt}, {'file_data': {'mime_type': 'application/pdf', 'file_uri': uri}}]}]
            }
        except Exception as e:
            return f"Failed to upload large file: {str(e)}"
    else:
        b64 = base64.b64encode(raw).decode('ascii')
        body = {
            'contents': [{'parts': [{'text': prompt}, {'inline_data': {'mime_type': 'application/pdf', 'data': b64}}]}],
            'generationConfig': {'response_mime_type': 'application/json'}
        }

    session = requests.Session()
    retry = Retry(connect=3, read=3, backoff_factor=1, status_forcelist=[502, 504])
    session.mount('https://', adapter := HTTPAdapter(max_retries=retry))
    headers = {'User-Agent': 'Mozilla/5.0 (Linux; Android 10)'}

    last_err = "Unknown error"
    for model in MODELS_TO_TRY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = session.post(url, params={'key': key}, json=body, headers=headers, timeout=120)
            if r.status_code == 200:
                text = r.json()['candidates'][0]['content']['parts'][0]['text']
                # Clean up markdown if AI added it
                if text.startswith('```json'):
                    text = text[7:]
                if text.endswith('```'):
                    text = text[:-3]
                return text.strip()
            elif r.status_code in (404, 429, 503):
                last_err = f"HTTP {r.status_code}"
                continue
            else:
                return f"Gemini Error {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err = str(e)
            continue
    return f"All AI models failed. Last error: {last_err}"

def extract_tender_from_pdf(file_obj):
    result_text = _send_to_gemini(file_obj, TENDER_PROMPT)
    if result_text.startswith("{"):
        try:
            return {'data': json.loads(result_text)}
        except Exception as e:
            return {'error': f"JSON parse error: {e}. Raw: {result_text[:100]}"}
    return {'error': result_text}

def analyze_bid_document(file_obj, supplier_context=''):
    prompt = ANALYSIS_PROMPT
    if supplier_context:
        prompt += (
            "\n\nREGISTERED COMPANY SUPPLIERS (REFERENCE DATA ONLY):\n"
            + supplier_context
            + "\nAssess potential relevance, but do not claim eligibility, "
              "authorization, availability or compliance without evidence."
        )
    return _send_to_gemini(file_obj, prompt)

def parse_war_room_output(text):
    import re

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
    if not text:
        return result

    cleaned = str(text).strip()
    cleaned = re.sub(r"^```(?:json|text)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    current = None
    collected = {key: [] for key in keys.values()}

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
        result["summary"] = cleaned

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
