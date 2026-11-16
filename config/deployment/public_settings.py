"""OFFLINE TEMPLATE. Loaded only by the separately reviewed public Compose overlay.

The edge proxy must overwrite X-Forwarded-Proto. The web port stays loopback.
Do not use this trust setting when clients can bypass that proxy.
"""
import os
import re

from .settings import *  # noqa: F403 -- intentional overlay of unchanged base settings

host=os.environ['VIDHARBOR_PUBLIC_HOST']
if (len(host)>253 or '.' not in host or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label)
                                         for label in host.split('.'))):
    raise ValueError('Replace the public hostname placeholder with one exact DNS hostname')
ALLOWED_HOSTS=[host]
CSRF_TRUSTED_ORIGINS=['https://'+host]
SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
SESSION_COOKIE_SECURE=CSRF_COOKIE_SECURE=True
