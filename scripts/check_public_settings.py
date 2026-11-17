"""Load the disabled public settings template in a disposable Python process."""
import argparse
import importlib.util
import json
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE','vidharbor.settings')
import django
django.setup()
from vidharbor.notifications import validate_payload


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings-file',required=True)
    parser.add_argument('--payload',required=True);args=parser.parse_args()
    with open(args.payload) as source:validate_payload(json.load(source))
    for host in ('example.invalid','*','https://bad.invalid','__PUBLIC_HOSTNAME__','bad.invalid,other.invalid'):
        os.environ['VIDHARBOR_PUBLIC_HOST']=host
        spec=importlib.util.spec_from_file_location('vidharbor.public_settings',args.settings_file)
        module=importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            assert host=='example.invalid'
            assert module.ALLOWED_HOSTS==['example.invalid']
            assert module.CSRF_TRUSTED_ORIGINS==['https://example.invalid']
            assert module.SESSION_COOKIE_SECURE and module.CSRF_COOKIE_SECURE
            assert module.SECURE_PROXY_SSL_HEADER==('HTTP_X_FORWARDED_PROTO','https')
        except ValueError:
            assert host!='example.invalid'
    print('PASS: synthetic S06 payload; exact host/origin/secure cookies/proxy; 4 invalid host inputs rejected; no DNS/HTTP')


if __name__=='__main__':main()
