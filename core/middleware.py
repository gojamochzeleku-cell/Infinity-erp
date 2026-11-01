from .models import Company


class CurrentCompanyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.company = None
        cid = request.session.get('company_id')
        if cid:
            request.company = Company.objects.filter(pk=cid).first()
        return self.get_response(request)
