"""Actual Vue upload: XHR response context must reach completion and the READY trace."""
import argparse
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from playwright.sync_api import expect, sync_playwright

from smoke_s07 import http, normalized_id, spans
from smoke_s07_monitoring import wait_for


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    assert os.getenv('COMPOSE_PROJECT_NAME','').startswith('vidharbor-s07-')
    assert 0<args.fixture.stat().st_size<=16*1024**2
    base='http://127.0.0.1:'+os.getenv('VIDHARBOR_PORT','8120')
    tempo='http://127.0.0.1:'+os.getenv('VIDHARBOR_TEMPO_PORT','13207')
    guest=build_opener(ProxyHandler({}));suffix=uuid.uuid4().hex
    executable=os.getenv('VIDHARBOR_CHROME') or shutil.which('google-chrome') or shutil.which('chromium')
    assert executable
    observed={};started={}
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=executable,args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            page=browser.new_page()
            def request(value):
                if value.method=='PUT' and value.url.endswith('/content'):started['put']=time.monotonic()
            def response(value):
                if value.request.method=='PUT' and value.url.endswith('/content'):
                    observed['put']={'status':value.status,'traceparent':value.headers.get('traceparent'),
                        'wall_ms':(time.monotonic()-started['put'])*1000}
                if value.request.method=='POST' and value.url.endswith('/complete'):
                    observed['complete']={'status':value.status,'sent_parent':value.request.headers.get('traceparent'),
                        'traceparent':value.headers.get('traceparent'),'created':value.json()}
            page.on('request',request);page.on('response',response)
            page.goto(base+'/studio',wait_until='domcontentloaded')
            page.get_by_role('button',name='Создать аккаунт',exact=True).click()
            page.locator('input[name=username]').fill('trace-ui-'+suffix[:10])
            page.locator('input[name=password]').fill('Test-only-'+suffix)
            page.get_by_role('button',name='Зарегистрироваться',exact=True).click()
            expect(page.get_by_role('button',name='Войти',exact=True)).to_be_visible()
            page.locator('input[name=username]').fill('trace-ui-'+suffix[:10])
            page.locator('input[name=password]').fill('Test-only-'+suffix)
            page.get_by_role('button',name='Войти',exact=True).click()
            expect(page.locator('input[name=file]')).to_be_visible()
            page.locator('input[name=title]').fill('Trace UI fixture '+suffix)
            page.locator('input[name=file]').set_input_files(str(args.fixture))
            with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/complete'),timeout=120000):
                page.get_by_role('button',name='Загрузить',exact=True).click()
            assert observed['put']['status']==observed['complete']['status']==200
            parent=observed['put']['traceparent'];assert parent and observed['complete']['sent_parent']==parent
            trace_id=parent.split('-')[1]
            assert observed['complete']['traceparent'].split('-')[1]==trace_id
            def complete_trace():
                trace=json.loads(http(guest,tempo+'/api/traces/'+trace_id,headers={'Accept':'application/json'})[0])
                names={s['name']:s for s in spans(trace)}
                return (trace,names) if {'upload.receive','video.publish','worker.process','job.create'}<=names.keys() else None
            trace,names=wait_for(complete_trace,'Browser byte-transfer to READY trace',seconds=90)
            put=names['HTTP PUT /api/uploads/<uuid:upload_id>/content'];complete=names['HTTP POST /api/uploads/<uuid:upload_id>/complete']
            assert normalized_id(complete['parentSpanId'],8)==normalized_id(put['spanId'],8)
            assert normalized_id(names['upload.receive']['parentSpanId'],8)==normalized_id(put['spanId'],8)
            for span in names.values():assert normalized_id(span['traceId'],16)==trace_id
            args.report.parent.mkdir(parents=True,exist_ok=True)
            args.report.with_suffix('.trace.json').write_text(json.dumps(trace,indent=2)+'\n')
            result={'status':'PASS','browser':browser.version,'trace_id':trace_id,'requests':observed,
                'receive_ms':(int(names['upload.receive']['endTimeUnixNano'])-int(names['upload.receive']['startTimeUnixNano']))/1e6,
                'span_count':len(names),'frontend_propagated_parent':True}
            args.report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
        finally:browser.close()


if __name__=='__main__':main()
