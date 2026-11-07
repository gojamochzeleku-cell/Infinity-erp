import os
import requests
import time
from django.conf import settings

def upload_to_google_file_api(file_bytes, display_name, mime_type="application/pdf"):
    key = os.environ.get('GEMINI_API_KEY', '') or getattr(settings, 'GEMINI_API_KEY', '')
    if not key:
        return None, "API Key missing"

    # 1. Start Resumable Upload
    url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={key}"
    headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(len(file_bytes)),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json"
    }
    metadata = {"file": {"display_name": display_name}}
    
    try:
        res = requests.post(url, headers=headers, json=metadata)
        if res.status_code != 200:
            return None, f"Failed to start upload: {res.text}"
            
        upload_url = res.headers.get("X-Goog-Upload-URL")
        
        # 2. Upload the actual bytes
        headers_upload = {
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
            "Content-Length": str(len(file_bytes))
        }
        res_upload = requests.post(upload_url, headers=headers_upload, data=file_bytes)
        if res_upload.status_code != 200:
            return None, f"Failed to upload bytes: {res_upload.text}"
            
        file_info = res_upload.json()
        file_uri = file_info['file']['uri']
        file_name = file_info['file']['name']
        
        # 3. Wait for processing (Gemini needs a moment to process the file)
        state = file_info['file']['state']
        while state == "PROCESSING":
            time.sleep(2)
            check_url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={key}"
            check_res = requests.get(check_url)
            state = check_res.json().get('state')
            
        return file_uri, None
        
    except Exception as e:
        return None, str(e)
