"""Exercise the same json-file bounds on a disposable log-only probe."""
import argparse
import json
import os
import subprocess
import uuid
from pathlib import Path

from smoke_s07 import compose


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    project=os.environ.get('COMPOSE_PROJECT_NAME','');assert project.startswith('vidharbor-s07-')
    config=json.loads(compose('config','--format','json'))
    expected={'max-size':'1m','max-file':'3'}
    for service in ('api','upload','relay','worker','db','rabbitmq','web','tempo','prometheus','grafana'):
        assert config['services'][service]['logging']=={'driver':'json-file','options':expected},service
    image=json.loads(subprocess.check_output(['docker','inspect',compose('ps','-q','api').strip()]))[0]['Image']
    name=project+'-rotation-'+uuid.uuid4().hex[:8]
    cid=None
    try:
        code="import json; [print(json.dumps({'event':'rotation.probe','sequence':i,'padding':'x'*900})) for i in range(6000)]"
        cid=subprocess.check_output(['docker','create','--name',name,'--network','none','--read-only','--log-driver','json-file',
            '--log-opt','max-size=1m','--log-opt','max-file=3',image,'python','-c',code],text=True).strip()
        subprocess.run(['docker','start','-a',cid],check=True,stdout=subprocess.DEVNULL)
        lines=subprocess.check_output(['docker','logs',cid],text=True).splitlines()
        assert 0<len(lines)<6000,'json-file bounds did not rotate/truncate the generated stream'
        info=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
        directory=str(Path(info['LogPath']).parent)
        files=subprocess.check_output(['docker','run','--rm','--network','none','--read-only','--mount',
            'type=bind,source='+directory+',target=/probe,readonly','busybox:1.37','sh','-c',
            'find /probe -maxdepth 1 -name "*-json.log*"'],text=True).splitlines()
        assert 2<=len(files)<=3,files
        result={'status':'PASS','configured_app_bounds':expected,'generated_lines':6000,'retained_lines':len(lines),'retained_files':len(files),
                'scope':'same driver/options on a disposable probe; existing application logs were not flooded'}
        args.report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    finally:
        if cid:subprocess.run(['docker','rm','-f',cid],check=False,stdout=subprocess.DEVNULL)


if __name__=='__main__':main()
