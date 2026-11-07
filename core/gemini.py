import base64
import json
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.conf import settings

MODELS_TO_TRY = ['gemini-3.8-flash', 'gemini-2.5-flash', 'gemini-3.5-flash']

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
  "lots": [
    {
      "lot_name": "e.g. Lot 1, or the specific bid reference if it applies to this lot",
      "description": "description of the item/service for this lot",
      "cpo_amount": 0,
      "submission_date": "YYYY-MM-DD",
      "opening_date": "YYYY-MM-DD"
    }
  ]
}
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

def extract_tender_from_pdf(file_obj):
    result_text = _send_to_gemini(file_obj, TENDER_PROMPT)
    
    # 1. Clean up the string (remove markdown backticks and extra spaces)
    clean_text = result_text.strip()
    if clean_text.startswith("```json"):
        clean_text = clean_text[7:] # Remove ```json
    elif clean_text.startswith("```"):
        clean_text = clean_text[3:] # Remove ```
        
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3] # Remove ending ```
        
    clean_text = clean_text.strip()
    
    # 2. Try to parse the clean JSON
    if clean_text.startswith("{"):
        try:
            return {'data': json.loads(clean_text)}
        except Exception as e:
            return {'error': f"JSON Parsing Error: {str(e)}. Raw text: {clean_text[:100]}"}
            
    return {'error': result_text}

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
    result = {'summary': '', 'checklist': '', 'brands': '', 'suppliers': ''}
    
    # Clean the text
    text = text.replace('```json', '').replace('```', '').strip()
    
    # Simple, robust extraction
    import re
    
    # Try to find SUMMARY
    if '--- SUMMARY ---' in text:
        parts = text.split('--- SUMMARY ---')
        if len(parts) > 1:
            content = parts[1]
            if '--- CHECKLIST ---' in content:
                content = content.split('--- CHECKLIST ---')[0]
            result['summary'] = content.strip()
            
    # Try to find CHECKLIST
    if '--- CHECKLIST ---' in text:
        parts = text.split('--- CHECKLIST ---')
        if len(parts) > 1:
            content = parts[1]
            if '--- BRANDS ---' in content:
                content = content.split('--- BRANDS ---')[0]
            result['checklist'] = content.strip()

    # Try to find BRANDS
    if '--- BRANDS ---' in text:
        parts = text.split('--- BRANDS ---')
        if len(parts) > 1:
            content = parts[1]
            if '--- SUPPLIERS ---' in content:
                content = content.split('--- SUPPLIERS ---')[0]
            result['brands'] = content.strip()

    # Try to find SUPPLIERS
    if '--- SUPPLIERS ---' in text:
        parts = text.split('--- SUPPLIERS ---')
        if len(parts) > 1:
            result['suppliers'] = parts[1].strip()

    # Fallback: If no headers were found, just dump everything into the summary
    if not any(result.values()):
        result['summary'] = text

    return result

