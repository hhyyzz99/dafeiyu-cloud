#!/usr/bin/env bash
set -euo pipefail
umask 077
BASE=/opt/dafeiyu-onboard
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PUBLIC_IP=''
AGREE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --public-ip) PUBLIC_IP=${2:?缺少公网IP}; shift 2 ;;
    --agree-acme) AGREE=1; shift ;;
    *) echo '用法：sudo bash install-web.sh --public-ip 公网IPv4 --agree-acme'; exit 1 ;;
  esac
done
[[ $EUID == 0 && $(uname -s) == Linux ]] || { echo '需要在 Linux 服务器上以管理员身份执行。'; exit 1; }
[[ $AGREE == 1 ]] || { echo '申请免费可信证书前，请阅读 https://letsencrypt.org/repository/ 并通过 --agree-acme 表示同意订户协议。'; exit 1; }
[[ -d /run/systemd/system ]] || { echo '需要 systemd。'; exit 1; }
[[ ! -L $BASE ]] || { echo '安装目录不能为符号链接。'; exit 1; }
[[ ! -e $BASE || -f $BASE/.onboard-owned ]] || { echo '目录已存在且不属于此安装程序，已停止。'; exit 1; }
command -v flock >/dev/null || { echo '请先安装 util-linux。'; exit 1; }
exec 9>/run/lock/dafeiyu-onboard.lock
flock -n 9 || { echo '正在安装，请等待当前任务完成。'; exit 1; }
# A verified repeat deployment should not depend on contacting the certificate CA.
if [[ -f $BASE/installed.ready && -x $BASE/venv/bin/python ]]; then
  "$BASE/venv/bin/python" - "$BASE/state/web-settings.json" "$PUBLIC_IP" <<'PY'
import json,sys
with open(sys.argv[1],encoding='utf-8-sig') as f:
    if json.load(f)['public_ip'] != sys.argv[2]:
        raise SystemExit('已有安装的公网地址不同，已停止；请先处理 IP 变更。')
PY
  if systemctl is-active --quiet dafeiyu-onboard dafeiyu-web-proxy && "$BASE/venv/bin/python" "$BASE/source/provision.py" verify "$PUBLIC_IP" >/dev/null 2>&1; then
    "$BASE/venv/bin/python" "$BASE/source/provision.py" result "$PUBLIC_IP"
    exit 0
  fi
fi
# Refuse unrelated listeners before changing packages/configuration.
if [[ ! -f $BASE/installed.ready ]]; then
  for port in 443 6187 6190; do
    if ss -H -ltn "sport = :$port" | grep -q .; then
      if [[ ! -f $BASE/.onboard-owned ]] || ! systemctl is-active --quiet dafeiyu-web-proxy; then
        echo "端口 $port 已被其他程序占用。请使用空白的同款服务器；已有程序未被停止。"; exit 1
      fi
    fi
  done
fi
mkdir -p "$BASE"
chmod 755 "$BASE"
touch "$BASE/.onboard-owned"
exec > >(tee -a "$BASE/install.log") 2>&1
trap 'echo "安装未完成。按报错处理后重新点击部署即可继续，已有开通资料会保留。"' ERR
echo '1/5 准备运行环境'
if command -v dnf >/dev/null; then
  dnf install -y python3 python3-pip curl nginx openssl ca-certificates tar gzip
elif command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  had_nginx=$(command -v nginx || true)
  apt-get update
  apt-get install -y python3 python3-venv curl nginx openssl ca-certificates tar gzip
  # Debian starts a newly installed nginx automatically. Stop only that new default service.
  if [[ -z $had_nginx ]]; then systemctl disable --now nginx; fi
else
  echo '当前版本支持本次演示的 Alibaba Cloud Linux 4。'; exit 1
fi
python3 - "$PUBLIC_IP" <<'PY'
import ipaddress,sys
try:
    ip=ipaddress.IPv4Address(sys.argv[1])
    assert ip.is_global
except (ValueError,AssertionError):
    raise SystemExit('需要服务器公网 IPv4，不能填写电脑的 IP。')
PY
[[ $(df -Pk "$BASE" | awk 'NR==2 {print $4}') -ge 3145728 ]] || { echo '请保留至少 3 GB 可用空间。'; exit 1; }
if [[ ! -x $BASE/installer/bin/uv ]]; then
  python3 -m venv "$BASE/installer"
  # ACL4's bundled pip cache adapter can fail while decoding an index response.
  "$BASE/installer/bin/python" -m pip install --no-cache-dir --disable-pip-version-check uv -i https://mirrors.aliyun.com/pypi/simple/
fi
export UV_CACHE_DIR="$BASE/cache" UV_PYTHON_INSTALL_DIR="$BASE/python"
if [[ ! -x $BASE/venv/bin/python ]]; then "$BASE/installer/bin/uv" venv --python 3.12 "$BASE/venv"; fi
"$BASE/installer/bin/uv" pip install --python "$BASE/venv/bin/python" --index-url https://mirrors.aliyun.com/pypi/simple/ 'AstrBot==4.29.4' 'certbot==5.4.0' 'qrcode==8.2'
# Apply the pinned QQ compatibility fix before the first bot process is started.
ASTRBOT_SITE=$("$BASE/venv/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
"$BASE/venv/bin/python" "$SOURCE/fix_empty_mention.py" "$ASTRBOT_SITE/astrbot/builtin_stars/astrbot/main.py" --apply
if ! id dafeiyu-onboard >/dev/null 2>&1; then
  useradd --system --home-dir "$BASE/state" --shell /sbin/nologin dafeiyu-onboard
elif [[ $(getent passwd dafeiyu-onboard | cut -d: -f6) != "$BASE/state" ]]; then
  echo '同名系统用户用于其他程序，已停止。'; exit 1
fi
mkdir -p "$BASE/state/instance" "$BASE/public/.well-known/acme-challenge" "$BASE/logs" "$BASE/nginx-tmp"
chown dafeiyu-onboard:dafeiyu-onboard "$BASE/nginx-tmp"
chmod 700 "$BASE/state"
chmod -R a+rX "$BASE/python" "$BASE/venv"
chmod 755 "$BASE/public" "$BASE/public/.well-known" "$BASE/public/.well-known/acme-challenge"
if [[ ! -f $BASE/source/.source-ready ]]; then
  mkdir -p "$BASE/source"
  cp -a "$SOURCE/." "$BASE/source/"
  chmod -R a+rX "$BASE/source"
  touch "$BASE/source/.source-ready"
fi
echo '2/5 准备开通页面'
"$BASE/venv/bin/python" "$BASE/source/provision.py" init "$PUBLIC_IP"
ACME_ROOT=$("$BASE/venv/bin/python" "$BASE/source/provision.py" webroot "$PUBLIC_IP")
chown -R dafeiyu-onboard:dafeiyu-onboard "$BASE/state"
chmod 700 "$BASE/state"
install -m 644 "$BASE/source/dafeiyu-onboard.service" /etc/systemd/system/dafeiyu-onboard.service
install -m 644 "$BASE/source/dafeiyu-web-proxy.service" /etc/systemd/system/dafeiyu-web-proxy.service
install -m 644 "$BASE/source/dafeiyu-web-renew.service" /etc/systemd/system/dafeiyu-web-renew.service
install -m 644 "$BASE/source/dafeiyu-web-renew.timer" /etc/systemd/system/dafeiyu-web-renew.timer
install -m 644 "$BASE/source/dafeiyu-web-backup.service" /etc/systemd/system/dafeiyu-web-backup.service
install -m 644 "$BASE/source/dafeiyu-web-backup.timer" /etc/systemd/system/dafeiyu-web-backup.timer
echo '3/5 申请可信 HTTPS 证书'
"$BASE/venv/bin/python" "$BASE/source/provision.py" proxy-http "$PUBLIC_IP"
/usr/sbin/nginx -t -c "$BASE/nginx.conf"
systemctl daemon-reload
systemctl enable --now dafeiyu-web-proxy
systemctl reload dafeiyu-web-proxy
certificate_ready=0
for attempt in 1 2 3; do
if "$BASE/venv/bin/python" "$BASE/source/certbot_ipv4.py" certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --preferred-profile shortlived --webroot --webroot-path "$ACME_ROOT" \
  --ip-address "$PUBLIC_IP" --cert-name dafeiyu-web --keep-until-expiring \
  --config-dir "$BASE/acme" --work-dir "$BASE/acme-work" --logs-dir "$BASE/acme-logs" \
  --deploy-hook 'systemctl reload dafeiyu-web-proxy'; then
  certificate_ready=1
  break
fi
echo "证书机构暂时未连接成功（第 $attempt 次），已有配置已保留。"
if [[ $attempt -lt 3 ]]; then sleep 15; fi
done
[[ $certificate_ready == 1 ]] || exit 1
echo '4/5 启动网页和自动维护'
"$BASE/venv/bin/python" "$BASE/source/provision.py" proxy-https "$PUBLIC_IP"
/usr/sbin/nginx -t -c "$BASE/nginx.conf"
systemctl reload dafeiyu-web-proxy
systemctl enable --now dafeiyu-onboard dafeiyu-web-renew.timer dafeiyu-web-backup.timer
echo '5/5 验证并生成开通入口'
"$BASE/venv/bin/python" "$BASE/source/provision.py" verify "$PUBLIC_IP"
touch "$BASE/installed.ready"
# This private result contains a single-use ticket, never a model key or an owner password.
"$BASE/venv/bin/python" "$BASE/source/provision.py" result "$PUBLIC_IP"
