"""Opt-in native Chrome checks on local fixtures; never click Send or press Enter.

Run: python tests/desktop_preparation_practice.py
The old desktop_practice.py publishes locally; do not use it for this workflow.
"""
from pathlib import Path
import shutil
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from allotment.preparation import preparation_context, run_recipe
from allotment.storage import Store,write_yaml
from allotment.test_sample import SAMPLE_TEXT,SAMPLE_IMAGE
from preparation_fixture import account_for,fixture_recipe,serve_fixture


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--platform',action='append',choices=['twitter','discord','telegram','whatsapp'])
    parser.add_argument('--prepare',action='store_true',help='Also exercise provisional draft steps on the local mocks')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    parent=root/'.local/roboclick-walkthrough'/('no-send-'+uuid.uuid4().hex[:8])
    parent.mkdir(parents=True)
    store=Store(parent/'studio')
    results={}
    for platform in args.platform or ['twitter','discord','telegram','whatsapp']:
        folder=parent/platform;folder.mkdir()
        shutil.copyfile(SAMPLE_IMAGE,folder/'image.png')
        post={'path':'.','text':SAMPLE_TEXT+f'\n\n[Allotment test {platform}]',
              'attachments':[{'type':'image','file':'image.png'}]}
        server,sends=serve_fixture(platform)
        base=f'http://127.0.0.1:{server.server_port}'
        recipe,account=fixture_recipe(platform,base),account_for(platform,base)
        try:
            with preparation_context(store,folder) as desktop:
                inspection=run_recipe(desktop,recipe,account,post,folder,mode='inspect')
                assert inspection['inspected'] and inspection['sent'] is False
                result=run_recipe(desktop,recipe,account,post,folder,
                                  on_step=lambda i,s,p:print(platform,i+1,s['command'],p,flush=True)) if args.prepare else inspection
            if args.prepare: assert result['prepared'] and result['text_checked'] and result['image_checked']
            assert result['sent'] is False and not sends, f'Unexpected Send/Enter on {platform}: {sends}'
            results[platform]={'result':result,'send_attempts':len(sends),'inspection':inspection}
            write_yaml(parent/'results.yaml',results)
            print(platform,'PASS:', 'draft checked;' if args.prepare else 'inspection captured;',
                  'HTML:',inspection['html_captured'],'zero send attempts.',flush=True)
        finally:
            server.shutdown()
            server.server_close()
    print('Selected draft-only workflows passed:' if args.prepare else 'Selected no-send inspections completed:',parent,flush=True)


if __name__=='__main__':
    main()
