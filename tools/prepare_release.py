"""Reproduce the tested installer and generate a GitHub-hosted ROS entry point.

No credentials are required. Run with Python 3.12; this does not publish or install.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from urllib.parse import urlencode
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'source'
RELEASE = json.loads((ROOT / 'release.json').read_text(encoding='utf-8'))


def repository_name(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}', value):
        raise ValueError('Use a GitHub repository name in owner/repository format.')
    return value


def rebuild_installer():
    spec = importlib.util.spec_from_file_location('fish_public_builder', SOURCE / 'build_web.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.VERSION == RELEASE['version']
    files = module.starter.payload_files()
    for name in module.WEB_FILES:
        files[name] = (SOURCE / name).read_bytes().replace(b'\r\n', b'\n')
    hashes = {n:hashlib.sha256(v).hexdigest() for n,v in sorted(files.items())}
    files['MANIFEST.json'] = json.dumps({'version':module.VERSION,'files':hashes},ensure_ascii=False,indent=2).encode()
    for name, data in files.items():
        assert (SOURCE / name).read_bytes().replace(b'\r\n',b'\n') == data, 'Source manifest differs: ' + name
    raw = module.starter.archive(files)
    header = module.starter.HEADER.replace('__SHA256__',hashlib.sha256(raw).hexdigest()).replace('dafeiyu-kit/install.sh','dafeiyu-kit/install-web.sh')
    header = header.replace('[--public-ip 公网IPv4] [--port 6186] [--local-only] [--env-file /root/fish.env]','--public-ip 公网IPv4 --agree-acme')
    installer = header.encode()+base64.encodebytes(raw)
    assert hashlib.sha256(installer).hexdigest() == RELEASE['installer_sha256'], 'Installer differs from the tested release. Do not overwrite this version.'
    return module, files, installer


def prepare(repository, output):
    repository = repository_name(repository)
    module, source_files, installer = rebuild_installer()
    version = RELEASE['version'];tag='v'+version
    base = 'https://github.com/'+repository+'/releases/download/'+tag
    installer_name='dafeiyu-web-'+version+'.run'
    template_name='dafeiyu-ros-'+version+'.json'
    template_url=base+'/'+template_name
    template=module.template(base+'/'+installer_name,RELEASE['installer_sha256'])
    template_bytes=json.dumps(template,ensure_ascii=False,indent=2).encode()
    params={'templateType':'url','templateUrl':template_url,'isSimplified':'true','stackNamePrefix':'Dafeiyu',
            'pageTitle':json.dumps({'zh-cn':'一键部署大肥鱼'},ensure_ascii=False),'productNavBar':'disabled'}
    deploy_url='https://ros.console.aliyun.com/cn-beijing/stacks/create?'+urlencode(params)
    meta={**RELEASE,'repository':repository,'tag':tag,'installer':installer_name,'installer_url':base+'/'+installer_name,
          'template':template_name,'template_url':template_url,'deployment_url':deploy_url,'installer_bytes':len(installer)}
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    deployment=ROOT/'deployment';deployment.mkdir(exist_ok=True)
    for name,data in {template_name:template_bytes,'deployment.json':json.dumps(meta,ensure_ascii=False,indent=2).encode()}.items():
        (deployment/name).write_bytes(data)
        (output/name).write_bytes(data)
    (output/installer_name).write_bytes(installer)
    readme=(ROOT/'README.template.md').read_text(encoding='utf-8').replace('__REPOSITORY__',repository).replace('__DEPLOYMENT_URL__',deploy_url).replace('__TAG__',tag)
    (ROOT/'README.md').write_text(readme,encoding='utf-8',newline='\n')
    public={
        'README.md':readme.encode(),'.gitignore':(ROOT/'.gitignore').read_bytes(),
        'release.json':(ROOT/'release.json').read_bytes(),'NOTICE.md':(ROOT/'NOTICE.md').read_bytes(),
        'README.template.md':(ROOT/'README.template.md').read_bytes(),
        'VALIDATION.md':(ROOT/'VALIDATION.md').read_bytes(),
        'tools/prepare_release.py':Path(__file__).read_bytes(),
        'tools/test_release.py':(ROOT/'tools/test_release.py').read_bytes(),
        'deployment/'+template_name:template_bytes,
        'deployment/deployment.json':(deployment/'deployment.json').read_bytes(),
        **{'source/'+n:d for n,d in source_files.items()}}
    patterns=rb'(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{20,}|LTAI[A-Za-z0-9]{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|#setup=[A-Za-z0-9_-]{24,}'
    for name,data in public.items():
        assert not re.search(patterns,data), 'Possible secret: '+name
    zip_name='dafeiyu-cloud-'+tag+'.zip'
    with zipfile.ZipFile(output/zip_name,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in sorted(public.items()):
            entry=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));entry.create_system=3
            entry.compress_type=zipfile.ZIP_DEFLATED;entry.external_attr=0o100644<<16
            archive.writestr(entry,data)
    artifacts=[installer_name,template_name,'deployment.json',zip_name]
    sums=''.join(hashlib.sha256((output/n).read_bytes()).hexdigest()+'  '+n+'\n' for n in artifacts)
    (output/'SHA256SUMS.txt').write_text(sums,encoding='utf-8',newline='\n')
    # Clean upload tree is generated from this allowlist, never by copying the workspace.
    upload=output/'upload';upload.mkdir(exist_ok=True)
    for name,data in public.items():
        target=upload/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    report={'version':version,'source_files':len(source_files),'public_files':len(public),
            'installer_sha256':RELEASE['installer_sha256'],'installer_matches_tested_release':True,
            'artifacts':artifacts+['SHA256SUMS.txt'],'deployment_url':deploy_url,'output':str(output)}
    (output/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',required=True)
    parser.add_argument('--output',default=str(ROOT/'build'))
    args=parser.parse_args();prepare(args.repository,args.output)
