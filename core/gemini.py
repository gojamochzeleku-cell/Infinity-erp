import base64
import json
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.conf import settings
from .gemini_file_api import upload_to_google_file_api

MODELS_TO_TRY = ['gemini-3.8-flash', 'gemini-2.5-flash', 'gemini-3.5-flash']

TENDER_PROMPT = """You are an expert procurement analyst for an Ethiopian import company.
You are reading a tender/bid document PDF. Your job is to extract the following information with 100% accuracy.

Output ONLY a valid JSON object with these exact keys. Do not include any markdown, explanations, or extra text.

{
  "bid_reference_number": "The official reference number (e.g. EEP/NPCB/04/2019, or the lot reference if it applies to the whole tender)",
  "bid_title": "The full title of the tender",
  "bid_type": "EGP or DOCUMENT (Choose EGP if it is an electronic government procurement, otherwise DOCUMENT)",
  "purchaser_name": "The exact name of the organization buying the goods (e.g. Ethiopian Electric Power, Nib International Bank)",
  "organization_type": "GOVERNMENT, NON_GOVERNMENT, or PRIVATE",
  "published_date": "The date the tender was published in YYYY-MM-DD format",
  "bidder_location": "The physical address/location where the bid must be submitted",
  "contact_phone": "The contact phone numbers",
  "bid_document_cost_amount": 0.00,
  "bid_document_cost_payment_mode": "CASH or DEPOSIT (Choose DEPOSIT if there is a bank account number provided)",
  "account_holder_name": "The account holder name if payment is via deposit, otherwise null",
  "bank_name": "The bank name if payment is via deposit, otherwise null",
  "bank_account_number": "The bank account number if payment is via deposit, otherwise null",
  "lots": [
    {
      "lot_name": "The name of the lot (e.g. Lot 1, Lot 2, or the specific item description if no lots exist)",
      "description": "A brief description of the items in this lot",
      "cpo_amount": 0.00,
      "cpo_type": "AMOUNT or PERCENT",
      "submission_date": "The deadline for this specific lot in YYYY-MM-DD format",
      "opening_date": "The opening date for this specific lot in YYYY-MM-DD format"
    }
  ]
}

Rules:
- If a date is not found, use null.
- If there is only one item and no lots, create ONE lot with the item's description.
- Look deep into the document for the CPO (Bid Security) and the submission dates. They are often on page 2 or 3.
"""

ANALYSIS_PROMPT = """You are an elite Procurement AI for an Ethiopian import company.
Read the attached bid document carefully. You must output text using these EXACT 5 headers, separated by '---'.

--- SUMMARY ---
Provide a detailed summary of the technical requirements and the scope of work.

--- CHECKLIST ---
List the required bid submission forms.

--- BRANDS ---
Identify the required items. For each item, suggest specific brands/models:
- High Quality (Premium): [Brand - Model] (Why it complies)
- Low Cost (Budget): [Brand - Model] (Why it complies)

--- SUPPLIERS ---
Suggest 5 manufacturers or suppliers (Foreign and Local) with contact info.

--- COMPLIANCE_MATRIX ---
Create a CSV formatted table (comma-separated) with the following columns:
Item Description, Required Spec, Proposed Brand/Model, Compliance Status (Compliant/Partially Compliant/Non-Compliant), Remarks.
Do not use markdown formatting for this section, just raw CSV text.
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


import io
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

def compress_pdf_if_needed(file_obj, max_size_mb=3.5):
    if fitz is None:
        return _get_file_bytes(file_obj) # Fallback if PyMuPDF isn't installed

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


def _send_to_gemini(file_obj, prompt):
    key = _api_key()
    if not key:
        return "GEMINI_API_KEY not set. Edit Render Environment Variables."
    
    try:
        # 1. Try to compress the PDF locally
        raw = compress_pdf_if_needed(file_obj, max_size_mb=3.5)
    except Exception as e:
        return str(e)

    # 2. If the file is UNDER 4MB, use inline data
    if len(raw) <= 4 * 1024 * 1024:
        b64 = base64.b64encode(raw).decode('ascii')
        body = {
            'contents': [{'parts': [{'text': prompt}, {'inline_data': {'mime_type': 'application/pdf', 'data': b64}}]}]
        }
    else:
        # 3. If the file is OVER 4MB, use the Google File API
        try:
            uri, error = upload_to_google_file_api(raw, "tender_document.pdf")
            if error:
                return f"File API Error (File too large): {error}"
                
            body = {
                'contents': [{'parts': [{'text': prompt}, {'file_data': {'mime_type': 'application/pdf', 'file_uri': uri}}]}]
            }
        except Exception as e:
            return f"Failed to upload large file to Google: {str(e)}"

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

import datetime

def extract_tender_from_pdf(file_obj):
    key = _api_key()
    if not key:
        return {'error': 'GEMINI_API_KEY not set.'}
        
    try:
        raw = _get_file_bytes(file_obj)
    except Exception as e:
        return {'error': str(e)}
        
    b64 = base64.b64encode(raw).decode('ascii')
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    
    prompt = f"""You are an expert Ethiopian procurement analyst. Today's date is {today_str}.
Read the attached tender PDF and extract the data into a valid JSON object.
If a date does not include a year, assume the current year ({today_str[:4]}).
Do not leave any fields blank if the information is present in the text.
"""

    schema = {
        "type": "OBJECT",
        "properties": {
            "bid_reference_number": {"type": "STRING"},
            "bid_title": {"type": "STRING"},
            "bid_type": {"type": "STRING", "enum": ["EGP", "DOCUMENT"]},
            "purchaser_name": {"type": "STRING"},
            "organization_type": {"type": "STRING", "enum": ["GOVERNMENT", "NON_GOVERNMENT", "PRIVATE"]},
            "published_date": {"type": "STRING", "description": "YYYY-MM-DD or null"},
            "bidder_location": {"type": "STRING"},
            "contact_phone": {"type": "STRING"},
            "bid_document_cost_amount": {"type": "NUMBER"},
            "bid_document_cost_payment_mode": {"type": "STRING", "enum": ["CASH", "DEPOSIT"]},
            "account_holder_name": {"type": "STRING"},
            "bank_name": {"type": "STRING"},
            "bank_account_number": {"type": "STRING"},
            "lots": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "lot_name": {"type": "STRING"},
                        "description": {"type": "STRING"},
                        "cpo_amount": {"type": "NUMBER"},
                        "cpo_type": {"type": "STRING", "enum": ["AMOUNT", "PERCENT"]},
                        "submission_date": {"type": "STRING", "description": "YYYY-MM-DD or null"},
                        "opening_date": {"type": "STRING", "description": "YYYY-MM-DD or null"}
                    },
                    "required": ["lot_name", "description", "cpo_amount", "cpo_type", "submission_date", "opening_date"]
                }
            }
        },
        "required": [
            "bid_reference_number", "bid_title", "bid_type", "purchaser_name", 
            "organization_type", "published_date", "bidder_location", "contact_phone",
            "bid_document_cost_amount", "bid_document_cost_payment_mode", 
            "account_holder_name", "bank_name", "bank_account_number", "lots"
        ]
    }

    body = {
        'contents': [{'parts': [{'text': prompt}, {'inline_data': {'mime_type': 'application/pdf', 'data': b64}}]}],
        'generationConfig': {
            'response_mime_type': 'application/json',
            'response_schema': schema,
            'temperature': 0.1
        }
    }
    
    session = requests.Session()
    headers = {'User-Agent': 'Mozilla/5.0 (Linux; Android 10)'}
    
    last_err = "Unknown error"
    for model in MODELS_TO_TRY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = session.post(url, params={'key': key}, json=body, headers=headers, timeout=120)
            if r.status_code == 200:
                try:
                    text = r.json()['candidates'][0]['content']['parts'][0]['text']
                    return {'data': json.loads(text)}
                except Exception as e:
                    return {'error': f"Failed to parse AI JSON: {e}"}
            elif r.status_code in (404, 429, 503):
                last_err = f"HTTP {r.status_code}"
                continue
            else:
                return {'error': f"Gemini Error {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            last_err = str(e)
            continue
            
    return {'error': f"All AI models failed. {last_err}"}

def analyze_bid_document(file_obj):
    return _send_to_gemini(file_obj, ANALYSIS_PROMPT)

# --- INFINITY BRAIN (AI DATA ANALYST) ---
def ask_infinity_brain(question, context_data):
    key = _api_key()
    if not key:
        return "GEMINI_API_KEY is missing. Please contact IT."

    prompt = f"""You are the 'Infinity Brain', an elite AI data analyst for the CEO of Infinity Investment Institute.
Use ONLY the provided company data to answer the CEO's question. 
If the answer is not in the data, say "I do not have that information in the current database context."
Keep your answer concise, professional, and direct. Format numbers with commas.

--- COMPANY DATA ---
{context_data}
--------------------

CEO's Question: {question}
Answer:"""

    body = {
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {'temperature': 0.2, 'maxOutputTokens': 500}
    }

    session = requests.Session()
    headers = {'User-Agent': 'Mozilla/5.0 (Linux; Android 10)'}

    for model in MODELS_TO_TRY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = session.post(url, params={'key': key}, json=body, headers=headers, timeout=60)
            if r.status_code == 200:
                try:
                    return r.json()['candidates'][0]['content']['parts'][0]['text']
                except (KeyError, IndexError):
                    return "AI returned an unexpected format. Please try again." 
            elif r.status_code in (404, 429, 503):
                continue
            else:
                return f"AI Error: {r.status_code}"
        except Exception as e:
            continue
    return "The AI is currently experiencing high demand. Please try again in a minute."




def parse_war_room_output(text):
    result = {'summary': '', 'checklist': '', 'brands': '', 'suppliers': '', 'compliance': ''}
    text = text.replace('```json', '').replace('```', '').strip()
    
    headers = ['SUMMARY', 'CHECKLIST', 'BRANDS', 'SUPPLIERS', 'COMPLIANCE_MATRIX']
    
    for i, header in enumerate(headers):
        key = header.lower()
        if key == 'compliance_matrix': key = 'compliance'
        
        if f'--- {header} ---' in text:
            parts = text.split(f'--- {header} ---')
            if len(parts) > 1:
                content = parts[1]
                # Cut off at the next header if it exists
                for next_header in headers[i+1:]:
                    if f'--- {next_header} ---' in content:
                        content = content.split(f'--- {next_header} ---')[0]
                result[key] = content.strip()

    if not any(result.values()):
        result['summary'] = text
    return result




def predict_winning_price(tender, historical_context):
    """Uses Gemini to suggest a winning price based on history."""
    key = _api_key()
    if not key:
        return "API key missing."

    prompt = f"""You are a strategic pricing AI for Infinity Investment Institute.
Analyze the following tender details and historical company data to suggest a winning bid price.

--- TENDER DETAILS ---
Title: {tender.bid_title}
Purchaser: {tender.purchaser_name}
CPO Amount: {tender.cpo_amount}
Our Total Landed Cost (Estimate): {tender.total_cost}

--- HISTORICAL CONTEXT ---
{historical_context}

--- INSTRUCTIONS ---
Provide a short, professional analysis:
1. Suggest a winning price range.
2. Explain the reasoning based on past wins/losses.
3. Warn if the profit margin is too thin.
"""
    body = {'contents': [{'parts': [{'text': prompt}]}]}
    
    session = requests.Session()
    headers = {'User-Agent': 'Mozilla/5.0 (Linux; Android 10)'}
    
    for model in MODELS_TO_TRY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = session.post(url, params={'key': key}, json=body, headers=headers, timeout=60)
            if r.status_code == 200:
                return r.json()['candidates'][0]['content']['parts'][0]['text']
        except:
            continue
    return "AI is currently busy. Please try again."
