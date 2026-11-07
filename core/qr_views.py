import io
import qrcode
from django.http import HttpResponse
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from .models import Document

@login_required
def generate_qr_code(request, pk):
    doc = Document.objects.filter(pk=pk, company=getattr(request, 'company', None)).first()
    if not doc:
        return redirect('dashboard')

    # Generate QR Code natively
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(doc.doc_id)
    qr.make(fit=True)

    # Create Image
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Save to memory buffer
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return HttpResponse(buffer, content_type="image/png")
