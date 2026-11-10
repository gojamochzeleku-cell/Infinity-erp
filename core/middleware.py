from .models import CompanyMembership


class CurrentCompanyMiddleware:
    """
    Resolve the active company only through a valid employee membership.
    The session company ID is never trusted on its own.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.company = None
        request.membership = None

        if request.user.is_authenticated and request.user.is_active:
            company_id = request.session.get('company_id')

            if company_id:
                try:
                    membership = (
                        CompanyMembership.objects
                        .select_related('company', 'employee')
                        .filter(
                            employee__user=request.user,
                            employee__is_active=True,
                            is_active=True,
                            company__is_active=True,
                            company_id=company_id,
                        )
                        .first()
                    )
                except (ValueError, TypeError):
                    membership = None

                if membership is not None:
                    request.membership = membership
                    request.company = membership.company
                else:
                    request.session.pop('company_id', None)

        return self.get_response(request)
