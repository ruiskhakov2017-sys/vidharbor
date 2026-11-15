from django.conf import settings
from django.http import JsonResponse

from .journal import JournalUnavailable


class SafetyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.RESTORE_MODE and request.path != "/health/live":
            return JsonResponse({"error": "Восстановление: доступ ещё закрыт."}, status=503)
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, JournalUnavailable):
            return JsonResponse({"error": "Журнал недоступен; доступ закрыт."}, status=503)
