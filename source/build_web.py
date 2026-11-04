"""Build an allowlisted self-contained source package and pinned ROS deployment template."""
import argparse
import base64
import hashlib
import importlib.util
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlencode, urlsplit

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
VERSION='1.1.0-beta3'
spec=importlib.util.spec_from_file_location('starter_build',HERE/'build.py' if (HERE/'settings.py').exists() else ROOT/'starter/build.py')
starter=importlib.util.module_from_spec(spec);spec.loader.exec_module(starter)
WEB_FILES=['controller.py','server.py','provision.py','install-web.sh','test_onboarding.py','test_memory.py','README.md','build_web.py','certbot_ipv4.py','fix_empty_mention.py','RELEASE_NOTES.md',
           'dafeiyu-onboard.service','dafeiyu-web-proxy.service','dafeiyu-web-renew.service',
           'dafeiyu-web-renew.timer','dafeiyu-web-backup.service','dafeiyu-web-backup.timer',
           'web/index.html','web/app.js','web/style.css','site/index.html','site/start.css','site/start.js']


def template(url, sha):
    script="""#!/bin/bash
set -euo pipefail
umask 077
work=$(mktemp -d /tmp/dafeiyu-download.XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
curl --silent --show-error --fail --location --proto '=https' --tlsv1.2 --retry 3 --connect-timeout 15 --max-time 180 -o "$work/install.run" '__URL__'
echo '__SHA__  '"$work/install.run" | sha256sum -c --status -
if bash "$work/install.run" --public-ip '__IP__' --agree-acme > "$work/install.log" 2>&1; then
  /opt/dafeiyu-onboard/venv/bin/python /opt/dafeiyu-onboard/source/provision.py result '__IP__'
else
  python3 - "$work/install.log" <<'PY'
import json,sys
from pathlib import Path
print(json.dumps({'owner_url':'','status':'安装失败，请查看详情并稍后重新部署。','detail':Path(sys.argv[1]).read_text(errors='replace')[-1500:]},ensure_ascii=False))
PY
  exit 1
fi
""".replace('__URL__',url).replace('__SHA__',sha)
    pieces = script.split('__IP__')
    command = []
    for i, piece in enumerate(pieces):
        if i: command.append({'Fn::GetAtt':['ExistingServer','PublicIpAddress']})
        command.append(piece)
    decoded = {'Fn::Base64Decode':{'Fn::Select':['Output',{'Fn::GetAtt':['InstallDafeiyu','InvokeResult']}]}}
    return {'ROSTemplateFormatVersion':'2015-09-01',
      'Description':{'zh-cn':'大肥鱼网页开通版：在已购买的北京同款轻量服务器上安装；不会新购服务器或重置系统。兼容 Hermes 原有的 80 端口服务，需要公网 80/443 可访问且 443 未被其他网站占用。'},
      'Parameters':{
        'InstanceId':{'Type':'String','Label':{'zh-cn':'你的轻量服务器实例 ID'},'Description':{'zh-cn':'在北京地域的服务器概览中复制实例 ID（32 位字母数字），不是公网 IP。仅选择本次推广的 2 核 6 GB 智能体专用型。'},'AllowedPattern':'[a-f0-9]{32}'},
        'CertificateAgreement':{'Type':'String','Label':{'zh-cn':'免费 HTTPS 证书订户协议'},'Description':{'zh-cn':'将为你的服务器公网 IP 申请免费证书并自动续期。请先阅读 https://letsencrypt.org/repository/ ，同意后选择下方选项。'},'AllowedValues':['我已阅读并同意 Let’s Encrypt 订户协议']}},
      'Resources':{
        'ExistingServer':{'Type':'DATASOURCE::SWAS::Instance','Properties':{'InstanceId':{'Ref':'InstanceId'},'RefreshOptions':'Always'}},
        'InstallDafeiyu':{'Type':'ALIYUN::SWAS::RunCommand','Properties':{
          'InstanceId':{'Ref':'InstanceId'},'Type':'RunShellScript','Name':'dafeiyu-web-'+VERSION,'Sync':True,'Timeout':1800,'WorkingUser':'root',
          'CommandContent':{'Fn::Join':['',command]}}},
        'VerifyInstallation':{'Type':'ALIYUN::ROS::Assert','Properties':{
          'Operation':'Equal','Values':[{'Fn::Select':['ExitCode',{'Fn::GetAtt':['InstallDafeiyu','InvokeResult']}]},0],
          'AbortCreation':True,'ErrorMessage':'大肥鱼安装未完成，请查看输出中的安装结果；处理后可重新部署，已有配置会保留。'}}},
      'Outputs':{
        'OpenYourFish':{'Description':'点击开通你的大肥鱼。首次链接含一次性开通码，仅自己使用，请勿公开。','Value':{'Fn::GetJsonValue':['owner_url',decoded]}},
        'InstallStatus':{'Description':'以此状态和开通链接判断实际安装结果。','Value':{'Fn::GetJsonValue':['status',decoded]}},
        'InstallResult':{'Description':'安装结果（已自动解码）。请勿公开其中的专属开通链接。','Value':decoded}}}


def build(origin):
    parts=urlsplit(origin)
    if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/') or not re.fullmatch(r'https://[A-Za-z0-9.:-]+/?',origin):
        raise ValueError('发布地址必须是没有账号、路径或参数的 HTTPS 站点。')
    origin=origin.rstrip('/')
    files=starter.payload_files()
    for name in WEB_FILES: files[name]=(HERE/name).read_bytes().replace(b'\r\n',b'\n')
    manifest={name:hashlib.sha256(value).hexdigest() for name,value in sorted(files.items())}
    files['MANIFEST.json']=json.dumps({'version':VERSION,'files':manifest},ensure_ascii=False,indent=2).encode()
    raw=starter.archive(files)
    header=starter.HEADER.replace('__SHA256__',hashlib.sha256(raw).hexdigest()).replace('dafeiyu-kit/install.sh','dafeiyu-kit/install-web.sh')
    header=header.replace("[--public-ip 公网IPv4] [--port 6186] [--local-only] [--env-file /root/fish.env]",'--public-ip 公网IPv4 --agree-acme')
    run=header.encode()+base64.encodebytes(raw)
    out=ROOT/'dist'/('web-'+VERSION)
    out.mkdir(parents=True,exist_ok=True)
    name='dafeiyu-web-'+VERSION+'.run'
    (out/name).write_bytes(run)
    sha=hashlib.sha256(run).hexdigest()
    ros=template(origin+'/downloads/'+name,sha)
    (out/'dafeiyu-ros.json').write_text(json.dumps(ros,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    versioned_template='dafeiyu-ros-'+VERSION+'.json'
    (out/versioned_template).write_bytes((out/'dafeiyu-ros.json').read_bytes())
    params={'templateType':'url','templateUrl':origin+'/downloads/'+versioned_template,'isSimplified':'true',
            'stackNamePrefix':'Dafeiyu','pageTitle':json.dumps({'zh-cn':'一键部署大肥鱼'},ensure_ascii=False),'productNavBar':'disabled'}
    link='https://ros.console.aliyun.com/cn-beijing/stacks/create?'+urlencode(params)
    (out/'deployment.json').write_text(json.dumps({'version':VERSION,'installer':name,'template':versioned_template,'sha256':sha,'bytes':len(run),'deployment_url':link,'origin':origin},ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    (out/'SHA256SUMS.txt').write_text(sha+'  '+name+'\n',encoding='utf-8',newline='\n')
    with zipfile.ZipFile(out/('大肥鱼网页开通版-'+VERSION+'.zip'),'w',zipfile.ZIP_DEFLATED) as z:
        def put(filename, data):
            entry=zipfile.ZipInfo(filename, date_time=(1980,1,1,0,0,0))
            entry.create_system=3
            entry.compress_type=zipfile.ZIP_DEFLATED
            entry.external_attr=0o100644 << 16
            z.writestr(entry,data)
        for file,data in sorted(files.items()): put('source/'+file,data)
        put(name,run);put('dafeiyu-ros.json',json.dumps(ros,ensure_ascii=False,indent=2));put(versioned_template,json.dumps(ros,ensure_ascii=False,indent=2));put('部署链接.txt',link)
    (out/'start').mkdir(exist_ok=True)
    for file in ['index.html','start.js','start.css']:
        text=(HERE/'site'/file).read_text(encoding='utf-8').replace('__DEPLOYMENT_URL__',link.replace('&','&amp;') if file.endswith('.html') else link)
        (out/'start'/file).write_text(text,encoding='utf-8',newline='\n')
    print(json.dumps({'version':VERSION,'installer_bytes':len(run),'sha256':sha,'output':str(out)},ensure_ascii=False))
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--origin',required=True);args=p.parse_args();build(args.origin)
