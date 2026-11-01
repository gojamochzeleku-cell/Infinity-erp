import base64
import json
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.conf import settings

MODELS_TO_TRY = ['gemini-2.5-flash', 'gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-2.0-flash']

# --- PART 1: T01 TENDER REGISTRATION EXTRACTION ---
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

# --- PART 2: T09A BID ANALYSIS ---
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

def _send_to_gemini(pdf_path, prompt):
    key = _api_key()
    if not key:
        return "GEMINI_API_KEY not set. Edit ~/infinity/.env and restart."
    try:
        with open(pdf_path, 'rb') as f:
            raw = f.read()
    except Exception as e:
        return f"Could not read PDF: {e}"
        
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
                last_err = f"HTTP {r.status_code} (Server busy or model not found)"
                continue
            else:
                return f"Gemini Error {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err = str(e)
            continue
    return f"All AI models failed. Last error: {last_err}"

def extract_tender_from_pdf(pdf_path):
    # This is for T01
    result_text = _send_to_gemini(pdf_path, TENDER_PROMPT)
    if result_text.startswith("{"):
        try:
            return {'data': json.loads(result_text)}
        except:
            pass
    return {'error': result_text}

def analyze_bid_document(pdf_path):
    # This is for T09A
    return _send_to_gemini(pdf_path, ANALYSIS_PROMPT)
