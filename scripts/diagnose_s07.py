"""Bounded, credential-redacted diagnostics from the isolated CI stack only."""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from smoke_s07 import COMPOSE


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    assert os.getenv('COMPOSE_PROJECT_NAME','').startswith('vidharbor-s07-')
    secrets=[p.read_text().strip() for p in Path(os.environ['VIDHARBOR_SECRETS_DIR']).iterdir() if p.is_file()]
    def command(*parts):
        start=time.monotonic()
        try:
            value=subprocess.run([*COMPOSE,*parts],capture_output=True,text=True,timeout=12)
            result={'returncode':value.returncode,'seconds':time.monotonic()-start,'stdout':value.stdout[:32768],'stderr':value.stderr[:32768]}
        except subprocess.TimeoutExpired:
            result={'timeout':True,'seconds':time.monotonic()-start}
        for key in ('stdout','stderr'):
            for secret in secrets:
                if secret:result[key]=result.get(key,'').replace(secret,'[REDACTED]')
        return result
    meta="""import urllib.request,json
from pathlib import Path
token=Path('/run/secrets/telemetry_metrics_token').read_text().strip()
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with op.open(urllib.request.Request('http://metrics:9100/metrics',headers={'Authorization':'Bearer '+token}),timeout=3) as r:
 print(json.dumps([x for x in r.read().decode().splitlines() if x.startswith('vidharbor_metrics_')]))
"""
    limits="""import json
from pathlib import Path
names=('cpu.max','cpu.stat','memory.max','memory.events')
print(json.dumps({n:(Path('/sys/fs/cgroup')/n).read_text() for n in names if (Path('/sys/fs/cgroup')/n).exists()}))
"""
    result={'note':'Diagnostics only; never overrides a failed assertion',
        'collection_metrics':command('exec','-T','upload','python','-c',meta),
        'cgroup_limits':command('exec','-T','metrics','python','-c',limits),
        'collector_once':command('exec','-T','metrics','python','manage.py','collect_metrics'),
        'collector_logs':command('logs','--no-color','--no-log-prefix','--tail','12','metrics')}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
