"""Vendor an explicit Cropper.js 1.x release. Review manifest changes and run UI tests."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.request import urlopen

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',default='1.7.0')
    args=parser.parse_args()
    if not re.fullmatch(r'1\.\d+\.\d+',args.version):parser.error('The adapter supports stable 1.x releases only.')
    base=f'https://raw.githubusercontent.com/fengyuanchen/cropperjs/v{args.version}/'
    files={'cropper.min.js':'dist/cropper.min.js','cropper.min.css':'dist/cropper.min.css','LICENSE':'LICENSE'}
    downloaded={name:urlopen(base+source,timeout=30).read() for name,source in files.items()}
    folder=Path(__file__).resolve().parents[1]/'allotment/static/vendor/cropper'
    folder.mkdir(parents=True,exist_ok=True)
    manifest={'name':'cropperjs','version':args.version,'repository':'https://github.com/fengyuanchen/cropperjs',
              'license':'MIT','source':base,'sha256':{name:hashlib.sha256(data).hexdigest() for name,data in downloaded.items()}}
    for name,data in downloaded.items():(folder/name).write_bytes(data)
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(f'Vendored Cropper.js {args.version}. Run tests/test_creation_browser.py before accepting the update.')

if __name__=='__main__':main()
