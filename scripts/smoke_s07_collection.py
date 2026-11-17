"""Isolated real SQL stall: cached metrics and the public catalogue stay responsive."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from smoke_s07 import COMPOSE, compose, database, http
from smoke_s07_monitoring import wait_for


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    project=os.getenv('COMPOSE_PROJECT_NAME','');assert project.startswith('vidharbor-s07-')
    base='http://127.0.0.1:'+os.getenv('VIDHARBOR_PORT','8120')
    tag='s07-stall-'+uuid.uuid4().hex[:12]
    # Deliberately remove the inner SQL timeout ONLY in this disposable fault
    # child, so the production sampler's outer deadline must kill it.
    slow=("from django.db import connection; c=connection.cursor(); "
        +f"c.execute(\"SET application_name = '{tag}'\"); "
        +"c.execute('SET statement_timeout = 0'); c.execute('SELECT pg_sleep(30)')")
    command=("import sys; from vidharbor.metrics_sampler import SnapshotSampler,serve; "
        +f"serve(SnapshotSampler([sys.executable,'manage.py','shell','-c',{slow!r}]))")
    internal=("import json,time,urllib.request; from pathlib import Path; "
        "token=Path('/run/secrets/telemetry_metrics_token').read_text().strip(); "
        "op=urllib.request.build_opener(urllib.request.ProxyHandler({})); started=time.monotonic(); "
        "r=op.open(urllib.request.Request('http://metrics:9100/metrics',headers={'Authorization':'Bearer '+token}),timeout=3); "
        "body=r.read().decode(); print(json.dumps({'seconds':time.monotonic()-started,'body':body}))")
    def sample():return database(internal)
    def value(body,name):
        return float(next(line.split()[-1] for line in body.splitlines() if line.startswith(name+' ') or line.startswith(name+'{')))
    def baseline():
        return database("import json; from vidharbor.models import Job,Attempt,Outbox; "
            "print(json.dumps({'jobs':list(Job.objects.order_by('id').values_list('id','state')),'attempts':Attempt.objects.count(),'outbox':Outbox.objects.count()},default=str))")
    before=baseline();original_files=os.getenv('COMPOSE_FILE','compose.yaml:compose.observability.yaml')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='s07-collector-fault-') as temp:
        override=Path(temp)/'fault.json';override.write_text(json.dumps({'services':{'metrics':{'command':['python','manage.py','shell','-c',command]}}}))
        env={**os.environ,'COMPOSE_FILE':original_files+':'+str(override)}
        try:
            subprocess.run([*COMPOSE,'up','-d','--no-deps','--wait','--wait-timeout','90','metrics'],env=env,check=True)
            stalled=wait_for(lambda: (s if value((s:=sample())['body'],'vidharbor_metrics_collection_failures_total')>=1 else None),
                'A real pg_sleep collector must hit the outer deadline',seconds=30)
            assert value(stalled['body'],'vidharbor_metrics_collection_up')==0
            assert value(stalled['body'],'vidharbor_metrics_collection_duration_seconds')<3
            assert 'reason="timeout"' in stalled['body']
            def catalog(_):
                guest=build_opener(ProxyHandler({}));latencies=[]
                for _ in range(8):
                    started=time.monotonic();http(guest,base+'/api/catalog');latencies.append(time.monotonic()-started);time.sleep(1)
                return latencies
            def scrapes():
                code="""import json,time,urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
token=Path('/run/secrets/telemetry_metrics_token').read_text().strip()
def check(index):
 results=[]
 for _ in range(8):
  for url in ('http://api:8000/internal/metrics','http://metrics:9100/metrics'):
   op=urllib.request.build_opener(urllib.request.ProxyHandler({}));start=time.monotonic()
   with op.open(urllib.request.Request(url,headers={'Authorization':'Bearer '+token}),timeout=3) as response: response.read();assert response.status==200
   results.append(time.monotonic()-start)
  time.sleep(1)
 return results
with ThreadPoolExecutor(max_workers=4) as pool: result=list(pool.map(check,range(4)))
print(json.dumps(result))
"""
                return database(code)
            with ThreadPoolExecutor(max_workers=5) as pool:
                metrics_future=pool.submit(scrapes)
                catalog_latencies=[x for values in pool.map(catalog,range(4)) for x in values]
                scrape_latencies=[x for values in metrics_future.result() for x in values]
            assert max(catalog_latencies)<2,('Catalogue delayed by collection',max(catalog_latencies))
            assert max(scrape_latencies)<1,('Cached scrape waited on collection',max(scrape_latencies))
            final=sample();assert value(final['body'],'vidharbor_metrics_collection_failures_total')>=2
            assert baseline()==before
            result={'status':'PASS','fault':'real PostgreSQL pg_sleep(30) in isolated collector; inner SQL timeout disabled only for this fault',
                'collector_deadline_seconds':2,'last_collection_seconds':value(final['body'],'vidharbor_metrics_collection_duration_seconds'),
                'catalog_requests':len(catalog_latencies),'catalog_max_seconds':max(catalog_latencies),
                'parallel_scrapes':len(scrape_latencies),'scrape_max_seconds':max(scrape_latencies),'business_records_unchanged':True}
        finally:
            subprocess.run([*COMPOSE,'up','-d','--no-deps','--wait','--wait-timeout','90','metrics'],check=True)
            # Only this fixture's tagged SQL sessions may survive a client kill.
            database("import json; from django.db import connection; "
                +f"c=connection.cursor(); c.execute(\"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name=%s AND pid<>pg_backend_pid()\",[{tag!r}]); print(json.dumps({{'terminated_test_sessions':c.rowcount}}))")
        wait_for(lambda:value(sample()['body'],'vidharbor_metrics_collection_up')==1,'Normal collection must recover')
        result['collection_recovered']=True
        args.report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
