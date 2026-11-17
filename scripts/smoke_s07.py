"""Real small-file HTTP/outbox/RabbitMQ/FFmpeg/Tempo acceptance on an isolated stand."""
import argparse
import base64
import hashlib
from http.cookiejar import CookieJar
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

COMPOSE = ['docker', 'compose', '--env-file', '.env.example', '--profile', 'observability']


def compose(*args):
    return subprocess.check_output([*COMPOSE, *args], text=True)


def database(code):
    raw = compose('exec', '-T', 'upload', 'python', 'manage.py', 'shell', '-c', code)
    return json.loads(raw.splitlines()[-1])


def http(opener, url, *, method='GET', data=None, headers=None, expected=200):
    if isinstance(data, dict):
        data = json.dumps(data).encode(); headers = {'Content-Type':'application/json', **(headers or {})}
    try:
        response = opener.open(Request(url, data=data, headers=headers or {}, method=method), timeout=20)
    except HTTPError as error:
        response = error
    body = response.read()
    assert response.status == expected, (response.status, url, body[:250])
    return body, dict(response.headers.items())


def spans(value, service=None):
    result = []
    if isinstance(value, dict):
        resource = value.get('resource', {})
        for attribute in resource.get('attributes', []):
            if attribute.get('key') == 'service.name':service = attribute['value'].get('stringValue')
        if 'name' in value and 'spanId' in value:
            result.append({**value, 'service':service})
        else:
            for child in value.values():result.extend(spans(child, service))
    elif isinstance(value, list):
        for child in value:result.extend(spans(child, service))
    return result


def normalized_id(value, size):
    if re.fullmatch(r'[0-9a-fA-F]{'+str(size*2)+'}',value):return value.lower()
    result = base64.b64decode(value);assert len(result)==size
    return result.hex()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--monitoring', action='store_true', help='Also exercise real metrics, Grafana and isolated outages')
    args = parser.parse_args()
    project = os.environ.get('COMPOSE_PROJECT_NAME','')
    assert project.startswith('vidharbor-s07-'), 'Only a dedicated S07 test project is allowed'
    base = 'http://127.0.0.1:' + os.getenv('VIDHARBOR_PORT','8120')
    tempo = 'http://127.0.0.1:' + os.getenv('VIDHARBOR_TEMPO_PORT','13207')
    assert 0 < args.fixture.stat().st_size <= 16*1024**2, 'Use a small valid media fixture'
    args.report.parent.mkdir(parents=True,exist_ok=True)
    jar = CookieJar(); opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))
    guest = build_opener(ProxyHandler({}))
    report = {'status':'RUNNING','project':project,'base':base,'github_ci':
        'Running in GitHub Actions; final result belongs to the job' if os.getenv('GITHUB_ACTIONS')=='true'
        else 'Not Run (no push/PR authorized)'}
    try:
        suffix = uuid.uuid4().hex
        username = 's07-' + suffix[:10]; password = 'Test-only-' + suffix
        title = 'Private title marker ' + suffix
        sensitive = [username, password, title]
        def csrf():
            raw,_=http(opener,base+'/api/auth/session');token=json.loads(raw)['csrf_token'];sensitive.append(token)
            return {'X-CSRFToken':token}
        http(opener,base+'/api/auth/register',method='POST',data={'username':username,'password':password},headers=csrf(),expected=201)
        http(opener,base+'/api/auth/login',method='POST',data={'username':username,'password':password},headers=csrf())
        transfers={}
        def make_upload(value):
            sensitive.append(value)
            raw,_=http(opener,base+'/api/uploads',method='POST',data={'title':value,'size':args.fixture.stat().st_size},headers=csrf(),expected=201)
            upload_id=json.loads(raw)['id'];uuid.UUID(upload_id)
            put_headers={**csrf(),'Content-Type':'application/octet-stream'}
            content=args.fixture.read_bytes();started=time.monotonic()
            _,put_headers=http(opener,base+f'/api/uploads/{upload_id}/content',method='PUT',data=content,headers=put_headers)
            parent={key.lower():value for key,value in put_headers.items()}['traceparent']
            transfers[upload_id]={'parent':parent,'wall_ms':(time.monotonic()-started)*1000}
            raw,headers=http(opener,base+f'/api/uploads/{upload_id}/complete',method='POST',headers={**csrf(),'traceparent':parent})
            assert {key.lower():value for key,value in headers.items()}['traceparent'].split('-')[1]==parent.split('-')[1]
            return json.loads(raw),headers
        created,headers=make_upload(title);report['created']=created
        upload_id={key.lower():value for key,value in headers.items()}['x-correlation-id']
        lower={key.lower():value for key,value in headers.items()}
        assert lower['x-correlation-id']==upload_id
        trace_id=lower['traceparent'].split('-')[1];assert re.fullmatch('[0-9a-f]{32}',trace_id)
        report['correlation_id']=upload_id;report['trace_id']=trace_id
        deadline=time.monotonic()+120
        while True:
            try:
                raw,_=http(guest,base+'/api/catalog/'+created['video_id']);detail=json.loads(raw);break
            except AssertionError as error:
                assert error.args[0][0]==404 and time.monotonic()<deadline,error
                time.sleep(.25)
        http(guest,base+detail['playlist_url'])
        deadline=time.monotonic()+30
        while True:
            try:
                raw,_=http(guest,tempo+'/api/traces/'+trace_id,headers={'Accept':'application/json'})
                trace=json.loads(raw);found=spans(trace)
                names={span['name']:span for span in found}
                required={'upload.receive','job.create','outbox.publish','worker.process','ffmpeg.encode','video.publish',
                    'HTTP PUT /api/uploads/<uuid:upload_id>/content','HTTP POST /api/uploads/<uuid:upload_id>/complete'}
                if required <= names.keys():break
            except (AssertionError, URLError):
                pass
            assert time.monotonic()<deadline,'A complete real distributed trace did not arrive'
            time.sleep(.5)
        for span in found:
            assert normalized_id(span['traceId'],16)==trace_id
            assert int(span['endTimeUnixNano'])>int(span['startTimeUnixNano'])
        put='HTTP PUT /api/uploads/<uuid:upload_id>/content';complete='HTTP POST /api/uploads/<uuid:upload_id>/complete'
        for child,parent in (('upload.receive',put),(complete,put),('job.create',complete),('outbox.publish','job.create'),('worker.process','outbox.publish'),('ffmpeg.encode','worker.process'),('video.publish','worker.process')):
            assert normalized_id(names[child]['parentSpanId'],8)==normalized_id(names[parent]['spanId'],8),(child,parent)
        receive=names['upload.receive'];transfer=transfers[upload_id]
        assert normalized_id(names[put]['spanId'],8)==transfer['parent'].split('-')[2]
        assert int(names[put]['startTimeUnixNano'])<=int(receive['startTimeUnixNano'])<int(receive['endTimeUnixNano'])<=int(names[put]['endTimeUnixNano'])
        assert (int(names[put]['endTimeUnixNano'])-int(names[put]['startTimeUnixNano']))/1e6 <= transfer['wall_ms']+100
        report['upload_transfer']={**transfer,'receive_ms':(int(receive['endTimeUnixNano'])-int(receive['startTimeUnixNano']))/1e6,'same_trace_through_ready':True}
        assert {names[name]['service'] for name in required} >= {'upload','relay','worker'}
        persisted=database('import json; from vidharbor.models import Job,Outbox; from vidharbor.storage import original_path,file_digest; '
            +f'j=Job.objects.get(pk={created["job_id"]!r}); m=Outbox.objects.get(pk={created["message_id"]!r}); '
            +"print(json.dumps({'state':j.state,'carrier':m.payload['trace_context'],'sent':m.sent_at is not None,'attempts':j.attempts.count(),"
            +"'ready_events':Outbox.objects.filter(job=j,kind='video.ready').count(),'sha256':file_digest(original_path(j.video.upload_id))}))")
        assert persisted['state']=='SUCCEEDED' and persisted['sent']
        assert persisted['attempts']==persisted['ready_events']==1
        assert persisted['sha256']==hashlib.sha256(args.fixture.read_bytes()).hexdigest()
        assert persisted['carrier']['traceparent'].split('-')[1]==trace_id
        # Keep evidence before any fault scenario can replace/rotate a container.
        prior_logs=compose('logs','--no-color','--no-log-prefix','api','upload','relay','worker')
        if args.monitoring:
            from smoke_s07_monitoring import verify_monitoring
            report['monitoring']=verify_monitoring(opener,guest,base,csrf,make_upload,created,args.report.parent)
        current_logs=compose('logs','--no-color','--no-log-prefix','api','upload','relay','worker')
        logs='\n'.join(dict.fromkeys((prior_logs+'\n'+current_logs).splitlines()))
        sensitive.extend(cookie.value for cookie in jar)
        for value in sensitive:assert value not in logs, 'Sensitive test data leaked into diagnostics'
        records=[json.loads(line) for line in logs.splitlines() if line.strip()]
        assert records,'No structured application logs'
        for record in records:assert {'timestamp','level','service','event','error_code'} <= record.keys()
        linked=[record for record in records if record.get('trace_id')==trace_id]
        assert {'job.created','outbox.published','worker.received','video.ready'} <= {record['event'] for record in linked}
        assert all(record.get('correlation_id')==upload_id for record in linked)
        args.report.with_suffix('.trace.json').write_text(json.dumps(trace,indent=2)+'\n')
        args.report.with_suffix('.logs.jsonl').write_text('\n'.join(json.dumps(record) for record in records)+'\n')
        report.update(status='PASS',trace_spans=[{'name':span['name'],'service':span['service'],
            'duration_ms':(int(span['endTimeUnixNano'])-int(span['startTimeUnixNano']))/1e6} for span in found],
            persisted=persisted,linked_log_records=len(linked),json_log_records=len(records),privacy='PASS: no title/user/password/cookie/CSRF values')
    except Exception as error:
        report.update(status='FAIL',error=repr(error));raise
    finally:
        args.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)


if __name__=='__main__':main()
