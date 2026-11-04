#!/usr/bin/env bash
set -euo pipefail
umask 077
BASE=/opt/dafeiyu-kit
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ARGS=("$@")
if [[ ${1:-} == --help ]]; then
  cat <<'HELP'
大肥鱼一键安装包 1.0.0-rc2
用法：sudo bash install.sh [--public-ip 公网IPv4] [--port 6186]
可选：--env-file /root/fish.env  --local-only  --model deepseek-模型名称
--local-only 仅监听本机，配合 SSH 隧道使用；默认使用 HTTPS 公网管理端口。
已有配置会保留；这不是更新/重置脚本。需要联网下载运行环境。
HELP
  exit 0
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port|--public-ip|--env-file|--model)
      [[ $# -ge 2 ]] || { echo "参数 $1 缺少值。"; exit 1; }
      shift 2;;
    --local-only) shift;;
    *) echo "未知参数：$1。运行 --help 查看用法。"; exit 1;;
  esac
done
[[ $EUID -eq 0 ]] || { echo '请使用 sudo bash 运行安装包。'; exit 1; }
[[ $(uname -s) == Linux ]] || { echo '需要 Linux 服务器。'; exit 1; }
[[ -d /run/systemd/system ]] || { echo '需要使用 systemd 的 Linux 服务器。'; exit 1; }
case $(uname -m) in x86_64|aarch64) ;; *) echo '仅支持 x86_64 / aarch64。'; exit 1;; esac
# A distinct install root prevents collision with the creator's running deployment.
[[ ! -L $BASE ]] || { echo '安装目录不能是符号链接。'; exit 1; }
if [[ -d $BASE && ! -f $BASE/.kit-owned ]]; then
  echo '安装目录已存在且不属于本安装包，已停止。'; exit 1
fi
for unit in dafeiyu-kit.service dafeiyu-kit-backup.service dafeiyu-kit-backup.timer; do
  if [[ -e /etc/systemd/system/$unit && ! -f $BASE/.kit-owned ]]; then
    echo "服务 $unit 已存在，已停止。"; exit 1
  fi
done
command -v flock >/dev/null || { echo '系统缺少 flock，请先安装 util-linux。'; exit 1; }
exec 9>/run/lock/dafeiyu-kit.lock
flock -n 9 || { echo '已有安装正在进行，请等待。'; exit 1; }
mkdir -p "$BASE"
chmod 755 "$BASE"
touch "$BASE/.kit-owned"
exec > >(tee -a "$BASE/install.log") 2>&1
trap 'echo "安装暂停。修复报错后用相同参数重新运行；已有资料会保留。日志：/opt/dafeiyu-kit/install.log"' ERR
echo '1/6 检查服务器环境'
if command -v dnf >/dev/null; then
  dnf install -y python3 python3-pip curl openssl ca-certificates tar gzip
elif command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y python3 python3-venv python3-pip curl openssl ca-certificates tar gzip
else
  echo '目前支持 Alibaba Cloud Linux 4，以及 Ubuntu 22.04/24.04。'; exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 8), "需要 Python 3.8 或以上"'
available_kb=$(df -Pk "$BASE" | awk 'NR==2 {print $4}')
[[ $available_kb -ge 3145728 ]] || { echo '请至少保留 3 GB 可用空间。'; exit 1; }
echo '2/6 配置本实例 DeepSeek'
python3 "$SOURCE/configure.py" --prepare-only "${ARGS[@]}"
echo '3/6 安装独立运行环境'
if [[ ! -x $BASE/installer/bin/uv ]]; then
  python3 -m venv "$BASE/installer"
  "$BASE/installer/bin/python" -m pip install --disable-pip-version-check uv -i https://mirrors.aliyun.com/pypi/simple/
fi
export UV_CACHE_DIR="$BASE/cache" UV_PYTHON_INSTALL_DIR="$BASE/python"
if [[ ! -x $BASE/venv/bin/python ]]; then
  "$BASE/installer/bin/uv" venv --python 3.12 "$BASE/venv"
fi
"$BASE/installer/bin/uv" pip install --python "$BASE/venv/bin/python" --index-url https://mirrors.aliyun.com/pypi/simple/ 'AstrBot==4.29.4'
echo '4/6 写入大肥鱼人设、插件和独立账号'
if [[ ! -f $BASE/source/.source-ready ]]; then
  mkdir -p "$BASE/source"
  cp -a "$SOURCE/." "$BASE/source/"
  touch "$BASE/source/.source-ready"
fi
"$BASE/venv/bin/python" "$BASE/source/configure.py"
if ! id dafeiyu-kit >/dev/null 2>&1; then
  useradd --system --home-dir "$BASE/instance" --shell /sbin/nologin dafeiyu-kit
elif [[ $(getent passwd dafeiyu-kit | cut -d: -f6) != "$BASE/instance" ]]; then
  echo '同名系统账号用途不同，已停止。'; exit 1
fi
chmod -R a+rX "$BASE/python" "$BASE/venv"
chmod 700 "$BASE/instance"
chown -R dafeiyu-kit:dafeiyu-kit "$BASE/instance"
echo '5/6 启用服务和每日备份'
install -m 644 "$BASE/source/dafeiyu-kit.service" /etc/systemd/system/dafeiyu-kit.service
install -m 644 "$BASE/source/dafeiyu-kit-backup.service" /etc/systemd/system/dafeiyu-kit-backup.service
install -m 644 "$BASE/source/dafeiyu-kit-backup.timer" /etc/systemd/system/dafeiyu-kit-backup.timer
systemctl daemon-reload
systemctl enable --now dafeiyu-kit.service dafeiyu-kit-backup.timer
echo '6/6 检查控制台'
"$BASE/venv/bin/python" "$BASE/source/manage.py" health --wait
systemctl start dafeiyu-kit-backup.service
"$BASE/venv/bin/python" "$BASE/source/manage.py" info
echo '安装完成。登录后进入「机器人 → 创建机器人 → QQ 官方 WebSocket → 扫码一键创建」。'
echo '查看账号：sudo python3 /opt/dafeiyu-kit/source/manage.py login'
