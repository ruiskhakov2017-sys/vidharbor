"""Render the real provisioned dashboard in the cloud's existing Chrome."""
import argparse
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    assert os.environ.get('COMPOSE_PROJECT_NAME','').startswith('vidharbor-s07-')
    password=(Path(os.environ['VIDHARBOR_SECRETS_DIR'])/'grafana_admin_password').read_text().strip()
    executable=os.getenv('VIDHARBOR_CHROME') or shutil.which('google-chrome') or shutil.which('chromium')
    assert executable,'No cloud Chrome installed; this check must not silently skip'
    port=os.getenv('VIDHARBOR_GRAFANA_PORT','13007');base='http://127.0.0.1:'+port
    errors=[];queries=[];external=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=executable,args=['--no-sandbox','--disable-dev-shm-usage'])
        context=browser.new_context(http_credentials={'username':'admin','password':password},viewport={'width':1440,'height':1000})
        page=context.new_page()
        def response(value):
            if urlsplit(value.url).path == '/api/ds/query':
                queries.append(value.status)
                if value.status!=200:errors.append('datasource_http_'+str(value.status))
                else:
                    for result in value.json().get('results',{}).values():
                        if result.get('error'):errors.append('datasource_query_error')
        page.on('response',response)
        page.on('request',lambda request: external.append(urlsplit(request.url).hostname)
                if urlsplit(request.url).scheme in ('http','https') and urlsplit(request.url).hostname not in ('127.0.0.1','localhost') else None)
        page.goto(base+'/login',wait_until='domcontentloaded')
        page.get_by_role('textbox',name='Email or username').fill('admin')
        page.get_by_role('textbox',name='Password',exact=True).fill(password)
        page.get_by_role('button',name='Log in',exact=True).click()
        page.wait_for_url(lambda url: '/login' not in url,timeout=30000)
        page.goto(base+'/d/vidharbor-overview/vidharbor-observability?from=now-15m&to=now',wait_until='domcontentloaded')
        expect(page.get_by_text('VidHarbor observability',exact=True).filter(visible=True).first).to_be_visible(timeout=60000)
        expect(page.get_by_text('Current processing states',exact=True).filter(visible=True).first).to_be_visible(timeout=45000)
        page.wait_for_timeout(7000)
        assert queries and not errors,(queries,errors)
        panel=page.get_by_text('Current processing states',exact=True).filter(visible=True).first
        expect(panel).to_be_visible(timeout=15000)
        canvas=page.locator('canvas').filter(visible=True).first
        expect(canvas).to_be_visible(timeout=15000)
        box=canvas.bounding_box();assert box and box['width']>100 and box['height']>50,'Graph canvas has no visible area'
        assert not external,('Unexpected external browser request',external)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        # Grafana virtualizes panels when a full-page capture resizes the viewport.
        # Capture the verified visible viewport instead of an empty virtual grid.
        page.screenshot(path=str(args.report.with_suffix('.png')),full_page=False,animations='disabled')
        args.report.write_text(json.dumps({'status':'PASS','browser':browser.version,'datasource_queries':len(queries),
            'query_errors':errors,'external_requests':external,'dashboard':'vidharbor-overview','visible_canvas':box},indent=2)+'\n')
        print(args.report.read_text());browser.close()


if __name__=='__main__':main()
