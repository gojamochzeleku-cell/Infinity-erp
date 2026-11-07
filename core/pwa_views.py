from django.http import HttpResponse, JsonResponse
from django.shortcuts import render

def manifest_view(request):
    manifest = {
        "name": "Infinity Investment Institute",
        "short_name": "Infinity ERP",
        "description": "Tender & Import Management System",
        "start_url": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#0f1115",
        "theme_color": "#e8b64c",
        "icons": [
            {
                "src": "https://api.qrserver.com/v1/create-qr-code/?size=192x192&data=Infinity",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable"
            },
            {
                "src": "https://api.qrserver.com/v1/create-qr-code/?size=512x512&data=Infinity",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable"
            }
        ]
    }
    return JsonResponse(manifest, content_type='application/manifest+json')

def sw_view(request):
    sw_code = """
    const CACHE_NAME = 'infinity-cache-v1';
    const urlsToCache = [
        '/',
        '/login/',
        '/offline/'
    ];

    self.addEventListener('install', event => {
        event.waitUntil(
            caches.open(CACHE_NAME)
                .then(cache => cache.addAll(urlsToCache))
        );
    });

    self.addEventListener('fetch', event => {
        if (event.request.method !== 'GET') return;
        event.respondWith(
            fetch(event.request).catch(() => {
                return caches.match(event.request).then(response => {
                    if (response) return response;
                    return caches.match('/offline/');
                });
            })
        );
    });
    """
    return HttpResponse(sw_code, content_type='application/javascript')

def offline_view(request):
    return render(request, 'offline.html')
