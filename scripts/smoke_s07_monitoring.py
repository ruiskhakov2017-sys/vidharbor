"""Real monitoring assertions; only invoked on the isolated S07 Compose project."""
import base64
import json
import os
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from smoke_s07 import compose, database, http, spans


def wait_for(function,label,seconds=45):
    deadline=time.monotonic()+seconds;last=None
    while time.monotonic()<deadline:
        try:
            result=function()
            if result:return result
        except (AssertionError,URLError,ConnectionError) as error:last=repr(error)
        time.sleep(.5)
    raise AssertionError((label,last))


def start(service):
    # Restart the exact stopped container, preserving its logs and configuration.
    compose('start','--wait','--wait-timeout','120',service)


def verify_monitoring(opener,guest,base,csrf,make_upload,created,output):
    prometheus='http://127.0.0.1:'+os.getenv('VIDHARBOR_PROMETHEUS_PORT','13907')
    grafana='http://127.0.0.1:'+os.getenv('VIDHARBOR_GRAFANA_PORT','13007')
    tempo='http://127.0.0.1:'+os.getenv('VIDHARBOR_TEMPO_PORT','13207')
    secret_root=Path(os.environ['VIDHARBOR_SECRETS_DIR'])
    password=(secret_root/'grafana_admin_password').read_text().strip()
    auth={'Authorization':'Basic '+base64.b64encode(('admin:'+password).encode()).decode()}
    result={}
    def query(expr,missing=False):
        raw,_=http(guest,prometheus+'/api/v1/query?'+urlencode({'query':expr}))
        data=json.loads(raw);assert data['status']=='success'
        values=data['data']['result']
        if not values and missing:return 0
        assert values,('Missing metric',expr)
        return float(values[0]['value'][1])
    wait_for(lambda:query('sum(up{job=~"vidharbor-.*"})')==6,'All six real application targets must be scraped')
    wait_for(lambda:query('vidharbor_metrics_collection_up')==1,'Isolated durable collector must have a fresh snapshot')
    assert json.loads(http(guest,grafana+'/api/health',headers=auth)[0])['database']=='ok'
    http(guest,base+'/internal/metrics',expected=404)
    http(guest,base+'/metrics',expected=404)
    unauthorized=database("import json,urllib.request,urllib.error; r=urllib.request.Request('http://api:8000/internal/metrics');\ntry: urllib.request.urlopen(r,timeout=5)\nexcept urllib.error.HTTPError as e: print(json.dumps({'status':e.code}))")
    assert unauthorized['status']==401
    result['private_metrics']='PASS: web 404, direct API without token 401; six authenticated targets up'

    before=query('sum(vidharbor_http_errors_total{status_class="4xx"})',missing=True)
    http(opener,base+'/api/uploads',method='POST',data={'title':'invalid fixture','size':0},headers=csrf(),expected=400)
    wait_for(lambda:query('sum(vidharbor_http_errors_total{status_class="4xx"})')>=before+1,'Real API failure must increment error metric')
    assert query('sum(vidharbor_http_duration_seconds_count)')>0
    result['http_metrics']='PASS: real requests, failure and histogram observed'

    previous_failed=query('sum(vidharbor_jobs_terminal_total{outcome="failed"})')
    previous_dlq=query('sum(vidharbor_queue_ready_messages{queue="video.dlq"})')
    bad=None
    compose('stop','worker')
    try:
        bad,headers=make_upload('Controlled failure '+uuid.uuid4().hex)
        uuid.UUID(bad['video_id'])
        database("import json,shutil; from vidharbor.models import Video; from vidharbor.storage import original_path; "
            +f"v=Video.objects.get(pk={bad['video_id']!r}); p=original_path(v.upload_id); "
            +"shutil.copyfile(p,p.with_name(p.name+'.s07-backup')); p.write_bytes(b'controlled invalid media fixture'); print(json.dumps({'injected':True}))")
        start('worker')
        wait_for(lambda:database("import json; from vidharbor.models import Job; "
            +f"print(json.dumps(Job.objects.get(pk={bad['job_id']!r}).state))")=='FAILED','Controlled bad original must produce a real terminal failure')
    finally:
        if bad:
            database("import json,os; from vidharbor.models import Video; from vidharbor.storage import original_path; "
                +f"v=Video.objects.get(pk={bad['video_id']!r}); p=original_path(v.upload_id); b=p.with_name(p.name+'.s07-backup'); "
                +"os.replace(b,p) if b.exists() else None; print(json.dumps({'fixture_restored':True}))")
        start('worker')
    wait_for(lambda:query('sum(vidharbor_jobs_terminal_total{outcome="failed"})')>=previous_failed+1,'Real failed-job metric')
    wait_for(lambda:query('sum(vidharbor_queue_ready_messages{queue="video.dlq"})')>=previous_dlq+1,'Real DLQ depth metric')
    assert query('sum(vidharbor_attempt_duration_seconds_count)')>=1
    result['failed_job_and_dlq']={'status':'PASS','job_id':bad['job_id'],'fixture':'intentional corruption of a dedicated fixture; original restored'}

    previous_written=query('sum(vidharbor_webhook_deliveries{state="written"})')
    delivered=database("import json,threading,uuid; from vidharbor.models import ReadyEvent,ReceiverRow; "
        "from vidharbor.notifications import attach_existing_ready,claim,finish,Webhook; from vidharbor.test_receiver import receiver; "
        "attach_existing_ready(); "
        +f"e=ReadyEvent.objects.get(video_id={created['video_id']!r}); "
        +"secret=uuid.uuid4().hex.encode()+b'-test-only'; s=receiver(secret,('127.0.0.1',0)); t=threading.Thread(target=s.serve_forever,daemon=True); t.start(); "
        +"w=Webhook(f'http://127.0.0.1:{s.server_port}/video-ready',['127.0.0.1'],secret,allow_test_loopback=True); "
        +"d=claim(e.pk); assert d is not None; outcome,reason,delay=w.send(e); finish(d,outcome,reason,delay); "
        +"s.shutdown();s.server_close();t.join();assert outcome=='written'; print(json.dumps({'row':e.sheet_row,'event_id':str(e.pk),'records':ReceiverRow.objects.filter(event_id=e.pk).count()}))")
    assert delivered['records']==1
    wait_for(lambda:query('sum(vidharbor_webhook_deliveries{state="written"})')>=previous_written+1,'Real TEST webhook delivery metric')
    result['webhook_metrics']={'status':'PASS','scope':'existing loopback test receiver only',**delivered}

    raw,_=http(guest,grafana+'/api/dashboards/uid/vidharbor-overview',headers=auth)
    dashboard=json.loads(raw)['dashboard'];expr=dashboard['panels'][0]['targets'][0]['expr']
    def panel_query():
        now=int(time.time()*1000)
        raw,_=http(guest,grafana+'/api/ds/query',method='POST',headers=auth,data={'from':str(now-600000),'to':str(now),
            'queries':[{'refId':'A','datasource':{'type':'prometheus','uid':'vidharbor-prometheus'},'expr':expr,'intervalMs':5000,'maxDataPoints':120,'format':'time_series'}]})
        data=json.loads(raw)
        frames=data['results']['A']['frames']
        ready=[]
        for frame in frames:
            for index,field in enumerate(frame['schema']['fields']):
                if field.get('labels',{}).get('state')=='ready':ready.extend(frame['data']['values'][index])
        assert any(value is not None and float(value)>=1 for value in ready), 'Actual dashboard query did not return ready jobs'
        return data
    data=wait_for(panel_query,'Grafana must return actual Prometheus frames')
    (output/'grafana-live-query.json').write_text(json.dumps(data,indent=2)+'\n')
    result['grafana']='PASS: provisioned dashboard expression executed through its real Prometheus datasource'

    def original():
        return database("import json; from vidharbor.models import Job,Outbox; from vidharbor.storage import original_path,file_digest; "
            +f"j=Job.objects.get(pk={created['job_id']!r}); print(json.dumps({{'job':str(j.pk),'state':j.state,'attempts':j.attempts.count(),"
            +"'ready_events':Outbox.objects.filter(job=j,kind='video.ready').count(),'sha256':file_digest(original_path(j.video.upload_id))}))")
    baseline=original();exports_before=query('sum(vidharbor_trace_exports_total{result="failed"})',missing=True)
    ready_before=query('sum(vidharbor_processing_jobs{state="ready"})')
    success_before=query('sum(vidharbor_jobs_terminal_total{outcome="success"})')
    compose('stop','tempo')
    try:
        independent,_=make_upload('Telemetry unavailable '+uuid.uuid4().hex)
        wait_for(lambda:database("import json; from vidharbor.models import Job; "
            +f"print(json.dumps(Job.objects.get(pk={independent['job_id']!r}).state))")=='SUCCEEDED','Video must still become READY while Tempo is stopped')
        wait_for(lambda:query('sum(vidharbor_trace_exports_total{result="failed"})')>exports_before,'Real exporter failure metric')
        wait_for(lambda:query('sum(vidharbor_processing_jobs{state="ready"})')>=ready_before+1,'Ready-state metric must increase')
        wait_for(lambda:query('sum(vidharbor_jobs_terminal_total{outcome="success"})')>=success_before+1,'Successful-job metric must increase')
        assert original()==baseline
    finally:start('tempo')
    _,headers=http(guest,base+'/api/catalog');trace_id={key.lower():value for key,value in headers.items()}['traceparent'].split('-')[1]
    wait_for(lambda:len(spans(json.loads(http(guest,tempo+'/api/traces/'+trace_id,headers={'Accept':'application/json'})[0])))>=1,'Trace export must recover')
    result['tempo_outage']={'status':'PASS','new_job_still_ready':independent['job_id'],'original_unchanged':True,'export_recovered':True}

    compose('stop','prometheus')
    try:
        http(guest,base+'/api/catalog')
        assert original()==baseline
        failed=False
        try:panel_query()
        except (AssertionError,URLError):failed=True
        assert failed,'Grafana must report an unavailable datasource instead of silently inventing data'
    finally:start('prometheus')
    wait_for(panel_query,'Grafana query must recover after Prometheus restart')
    result['prometheus_outage']='PASS: business data unchanged; datasource failure then live recovery'

    compose('stop','rabbitmq')
    try:
        wait_for(lambda:query('sum(vidharbor_backend_up{backend="rabbitmq"})')==0,'Broker outage must be visible')
        def components_unready():
            values=database("import json,urllib.request,urllib.error; result={};\n"
                "for service in ('relay','worker'):\n"
                " for kind in ('live','ready'):\n"
                "  try: response=urllib.request.urlopen(f'http://{service}:9100/health/{kind}',timeout=3); status=response.status; response.close()\n"
                "  except urllib.error.HTTPError as error: status=error.code; error.close()\n"
                "  result[service+'/'+kind]=status\n"
                "print(json.dumps(result))")
            return all(values[service+'/live']==200 and values[service+'/ready']==503 for service in ('relay','worker'))
        wait_for(components_unready,'Relay/worker live=200 and ready=503 during broker outage')
        http(guest,base+'/media/'+created['video_id']+'/master.m3u8')
        assert original()==baseline
        waiting,queued_headers=make_upload('Broker recovery '+uuid.uuid4().hex)
        queued_trace={key.lower():value for key,value in queued_headers.items()}['traceparent'].split('-')[1]
        wait_for(lambda:query('sum(vidharbor_command_outbox_pending)')>=1,'Pending durable outbox must be visible while broker is down')
        def firing():
            data=json.loads(http(guest,prometheus+'/api/v1/alerts')[0])
            return any(alert['labels']['alertname']=='VidHarborDependencyUnavailable' and alert['state']=='firing' for alert in data['data']['alerts'])
        wait_for(firing,'A real dependency alert must fire',seconds=40)
    finally:start('rabbitmq')
    wait_for(lambda:query('sum(vidharbor_backend_up{backend="rabbitmq"})')==1,'Broker health must recover')
    wait_for(lambda:query('min(vidharbor_component_ready)')==1,'Component readiness must recover')
    wait_for(lambda:database("import json; from vidharbor.models import Job; "
        +f"print(json.dumps(Job.objects.get(pk={waiting['job_id']!r}).state))")=='SUCCEEDED','Durable queued work must finish after broker recovery')
    recovered_trace=wait_for(lambda:spans(json.loads(http(guest,tempo+'/api/traces/'+queued_trace,headers={'Accept':'application/json'})[0])),
        'Same trace must survive a broker outage')
    names={span['name'] for span in recovered_trace}
    wait_for(lambda: {'job.create','outbox.publish','worker.process','ffmpeg.encode','video.publish'} <=
        {span['name'] for span in spans(json.loads(http(guest,tempo+'/api/traces/'+queued_trace,headers={'Accept':'application/json'})[0]))},
        'Completed real trace after delayed outbox delivery')
    assert original()==baseline
    result['broker_outage']={'status':'PASS','alert_fired':True,'component_live_200_ready_503':True,'original_unchanged':True,'queued_job':waiting['job_id'],
                            'trace_id':queued_trace,'durable_outbox_observed':True,'trace_context_recovered':True}
    result['real_n8n_sheets']='Not Run'
    return result
