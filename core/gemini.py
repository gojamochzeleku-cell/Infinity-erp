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

MODELS_TO_TRY = ['gemini-flash-latest', 'gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-2.5-flash']

TENDER_PROMPT = """You are an expert procurement AI for an Ethiopian import company.
Read the attached bid document and extract the following information.
Reply with ONLY a valid JSON object. Do not include markdown or explanations.

{
  "bid_reference_number": "the official bid reference number",
  "bid_title": "the title of the bid",
  "bid_type": "one of: EGP or DOCUMENT",
  "purchaser_name": "the name of the purchasing organization",
  "organization_type": "one of: GOVERNMENT or NON_GOVERNMENT or PRIVATE",
  "published_date": "YYYY-MM-DD",
  "bidder_location": "location where bids are to be submitted",
  "contact_phone": "contact phone numbers",
  "bid_document_cost_amount": 0,
  "bid_document_cost_payment_mode": "one of: CASH or DEPOSIT",
  "account_holder_name": "account holder name if deposit",
  "bank_name": "bank name if deposit",
  "bank_account_number": "bank account number if deposit",
  "lots": [
    {
      "lot_name": "e.g. Lot 1",
      "description": "description of items in this lot",
      "cpo_amount": 0,
      "cpo_type": "one of: AMOUNT or PERCENT",
      "submission_date": "YYYY-MM-DD",
      "opening_date": "YYYY-MM-DD"
    }
  ]
}

If a field is not found, use null. Even if there is only one item, include it in the "lots" array.
"""

ANALYSIS_PROMPT = """You are an elite Procurement AI for an Ethiopian import company.
Read the attached bid document carefully. You must output text using these EXACT 4 headers, separated by '---'.

--- SUMMARY ---
Provide a detailed summary of the technical requirements and the scope of work.

--- CHECKLIST ---
List the required bid submission forms (e.g., Bid Security, Technical Proposal, Compliance Matrix, Manufacturer Authorization).

--- BRANDS ---
Identify the required items. For each item, suggest specific brands/models:
- High Quality (Premium): [Brand - Model] (Why it complies)
- Low Cost (Budget): [Brand - Model] (Why it complies)

--- SUPPLIERS ---
Suggest 5 manufacturers or suppliers that can provide these items. 
Include a mix of Foreign (Outside Ethiopia) and Local (Ethiopia) suppliers.
Format each as: 
- Type: [Foreign/Local]
- Name: [Supplier Name]
- Contact: [Website/Email/Phone if known]
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

def analyze_bid_document(file_obj):
    return _send_to_gemini(file_obj, ANALYSIS_PROMPT)

def parse_war_room_output(text):
    result = {'summary': '', 'checklist': '', 'brands': '', 'suppliers': ''}
    text = text.replace('```json', '').replace('```', '').strip()
    headers = ['SUMMARY', 'CHECKLIST', 'BRANDS', 'SUPPLIERS']
    for i, header in enumerate(headers):
        key = header.lower()
        if f'--- {header} ---' in text:
            parts = text.split(f'--- {header} ---')
            if len(parts) > 1:
                content = parts[1]
                for next_header in headers[i+1:]:
                    if f'--- {next_header} ---' in content:
                        content = content.split(f'--- {next_header} ---')[0]
                result[key] = content.strip()
    if not any(result.values()):
        result['summary'] = text
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
