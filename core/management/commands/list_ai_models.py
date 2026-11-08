import os
import requests
from django.core.management.base import BaseCommand
from django.conf import settings

class Command(BaseCommand):
    help = 'Lists available Gemini models for your API key.'

    def handle(self, *args, **kwargs):
        key = os.environ.get('GEMINI_API_KEY', '') or getattr(settings, 'GEMINI_API_KEY', '')
        if not key:
            self.stdout.write(self.style.ERROR("GEMINI_API_KEY is missing."))
            return
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        try:
            r = requests.get(url)
            if r.status_code == 200:
                models = r.json().get('models', [])
                self.stdout.write(self.style.SUCCESS("Available Models:"))
                for m in models:
                    if 'generateContent' in m.get('supportedGenerationMethods', []):
                        self.stdout.write(f"- {m['name'].replace('models/', '')}")
            else:
                self.stdout.write(self.style.ERROR(f"Error {r.status_code}: {r.text}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Network Error: {e}"))
