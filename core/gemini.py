import base64
import json
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.conf import settings

MODELS_TO_TRY = ['gemini-2.5-flash', 'gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-pro']

TENDER_PROMPT = """You are reading a tender / bid announcement PDF from Ethiopia.
Extract the following and reply with ONLY a valid JSON object using these exact keys.
Use null for any field you cannot determine. Dates must be YYYY-MM-DD format.
Numbers must be plain numbers (no commas, no currency symbols).

{
  "bid_reference_number": "the official bid/tender reference number",
  "bid_title": "the title of the bid",
  "bid_type": "one of: EGP or DOCUMENT",
  "purchaser_name": "the name of the purchasing organization",
  "organization_type": "one of: GOVERNMENT or NON_GOVERNMENT or PRIVATE",
  "published_date": "date the bid was published",
  "bidder_location": "location where bids are to be submitted",
  "contact_phone": "contact phone numbers",
  "bid_document_cost_amount": 0,
  "bid_document_cost_payment_mode": "one of: CASH or DEPOSIT",
  "account_holder_name": "account holder name if deposit",
  "bank_name": "bank name if deposit",
  "bank_account_number": "bank account number if deposit",
  "cpo_amount": 0,
  "cpo_type": "one of: AMOUNT or PERCENT",
  "bid_submission_date": "deadline for submitting the bid",
  "bid_opening_date": "date bids are opened"
}
"""

ANALYSIS_PROMPT = """You are an expert procurement AI for an Ethiopian import company.
Read the attached bid document and provide a clear, structured summary.
1. Summarize the technical requirements.
2. Suggest specific brands and models that meet the requirements. Split them into "High Quality (Premium)" and "Low Cost (Budget)".
3. List the required bid submission forms (e.g., Bid Submission Sheet, Financial Standing, Workers Info, Compliance Matrix).
Reply in plain text (no markdown tables, just bullet points).
"""

def _api_key():
    key = os.environ.get('GEMINI_API_KEY', '') or getattr(settings, 'GEMINI_API_KEY', '')
    if key in ('', 'PASTE_YOUR_GEMINI_API_KEY_HERE'): return ''
    return key

def _get_file_bytes(file_obj):
    """Safely reads bytes from a File, FieldFile, or string path."""
    try:
        if hasattr(file_obj, 'storage'):
            # It's a Django FieldFile (e.g., Cloudflare R2)
            file_obj.open()
            raw = file_obj.read()
            file_obj.close()
            return raw
        elif hasattr(file_obj, 'read'):
            # It's an uploaded file (InMemoryUploadedFile)
            file_obj.seek(0)
            return file_obj.read()
        else:
            # It's a string path
            with open(file_obj, 'rb') as f:
                return f.read()
    except Exception as e:
        raise Exception(f"Could not read file: {str(e)}")

def _send_to_gemini(file_obj, prompt):
    key = _api_key()
    if not key:
        return "GEMINI_API_KEY not set. Edit Render Environment Variables."
    
    try:
        raw = _get_file_bytes(file_obj)
    except Exception as e:
        return str(e)
        
    b64 = base64.b64encode(raw).decode('ascii')
    body = {
        'contents': [{'parts': [{'text': prompt}, {'inline_data': {'mime_type': 'application/pdf', 'data': b64}}]}]
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
                return r.json()['candidates'][0]['content']['parts'][0]['text']
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
        except:
            pass
    return {'error': result_text}

def analyze_bid_document(file_obj):
    return _send_to_gemini(file_obj, ANALYSIS_PROMPT)
