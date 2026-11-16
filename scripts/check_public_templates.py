"""Offline syntax/configuration checks only: no public ports, DNS or deployment."""
import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nginx-image',required=True,help='Already available reviewed Nginx image ID')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='vidharbor-offline-tls-') as root:
        root=Path(root);root.chmod(0o755)
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-subj','/CN=example.invalid','-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem')],
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        source=(repo/'config/deployment/nginx-https.conf.example').read_text()
        rendered=source.replace('__PUBLIC_HOSTNAME__','example.invalid').replace('__TLS_FULLCHAIN_PATH__','/review/cert.pem').replace('__TLS_PRIVATE_KEY_PATH__','/review/key.pem')
        assert '__' not in rendered
        (root/'https.conf').write_text(rendered)
        (root/'nginx.conf').write_text('pid /tmp/nginx.pid; events {} http { include /review/https.conf; }\n')
        for name in ('https.conf','nginx.conf','cert.pem'):(root/name).chmod(0o644)
        subprocess.run(['docker','run','--rm','--network','none','--read-only','--tmpfs','/tmp',
            '--tmpfs','/var/cache/nginx','--tmpfs','/var/log/nginx',
            '-v',str(root)+':/review:ro','--entrypoint','nginx',args.nginx_image,'-t','-c','/review/nginx.conf'],check=True)
    payload=json.loads((repo/'config/integrations/video-ready.example.json').read_text())
    assert payload['playlist_url'].startswith('https://video.example.invalid/')
    result={'status':'PASS','nginx_syntax':'PASS with temporary synthetic self-signed cert, network none, no ports',
            'public_dns_tls_handshake':'Not Run','real_n8n_sheets':'Not Run','fixture_event_id':payload['event_id']}
    args.report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
