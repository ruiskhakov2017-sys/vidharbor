import json
from functools import wraps

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import UserCreationForm
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST


def error(message, status):
    return JsonResponse({"error": message}, status=status)


def csrf_failure(request, reason=""):
    return error("Проверка CSRF не пройдена. Обновите страницу.", 403)


def json_object(request):
    if request.content_type != "application/json":
        return {}
    try:
        value = json.loads(request.body)
    except (ValueError, UnicodeError):
        return {}
    return value if isinstance(value, dict) else {}


def require_author(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return error("Войдите в аккаунт.", 401)
        return view(request, *args, **kwargs)

    return wrapped


def session_data(request):
    return {
        "username": request.user.get_username() if request.user.is_authenticated else None,
        "csrf_token": get_token(request),
    }


@require_GET
@never_cache
@ensure_csrf_cookie
def session(request):
    return JsonResponse(session_data(request))


@require_POST
def register(request):
    data = json_object(request)
    username, password = data.get("username"), data.get("password")
    if not isinstance(username, str) or not isinstance(password, str) or len(password) > 1024:
        return error("Укажите имя и пароль.", 400)
    form = UserCreationForm({"username": username, "password1": password, "password2": password})
    if not form.is_valid():
        return error(next(iter(form.errors.values()))[0], 400)
    try:
        with transaction.atomic():
            user = form.save()
    except IntegrityError:
        return error("Это имя уже используется.", 409)
    return JsonResponse({"username": user.get_username()}, status=201)


@require_POST
@never_cache
def sign_in(request):
    data = json_object(request)
    username, password = data.get("username"), data.get("password")
    if (
        not isinstance(username, str)
        or not isinstance(password, str)
        or not 0 < len(username) <= 150
        or not 0 < len(password) <= 1024
    ):
        return error("Укажите имя и пароль.", 400)
    user = authenticate(request, username=username.strip(), password=password)
    if user is None:
        return error("Неверное имя пользователя или пароль.", 401)
    login(request, user)
    return JsonResponse(session_data(request))


@require_POST
@never_cache
@require_author
def sign_out(request):
    logout(request)
    return JsonResponse(session_data(request))
