import pika
from django.conf import settings
from django.db import DatabaseError, connection
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def live(request):
    return Response({"status": "live"})


def postgres_available() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except DatabaseError:
        return False


def rabbitmq_available() -> bool:
    credentials = pika.PlainCredentials(settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD)
    params = pika.ConnectionParameters(
        host=settings.RABBITMQ_HOST,
        credentials=credentials,
        connection_attempts=1,
        socket_timeout=1,
        stack_timeout=2,
        blocked_connection_timeout=1,
    )
    try:
        with pika.BlockingConnection(params) as broker:
            broker.channel()
        return True
    except (pika.exceptions.AMQPError, OSError):
        return False


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def ready(request):
    checks = {"postgres": postgres_available(), "rabbitmq": rabbitmq_available()}
    is_ready = all(checks.values())
    return Response(
        {"status": "ready" if is_ready else "not_ready", "checks": checks},
        status=status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE,
    )
