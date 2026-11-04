# 大肥鱼网页开通版 · 1.1.0-beta2

这是在 1.0.0-rc2 基础上开发的独立部署方案。旧版安装包和运行中的旧服务不会被覆盖。

## 目标操作流程

1. 购买同款：阿里云轻量应用服务器智能体专用型，北京，2 核 6 GB，30 GB ESSD，100 Mbps 峰值带宽，每月 12,500 Credits，Hermes Agent 应用镜像。
2. 在概览复制 32 位实例 ID，打开公开部署链接，在阿里云官方页面填写实例 ID，并阅读、同意免费证书订户协议。当前模板使用实例 ID 输入框，尚未实现服务器下拉选择。
3. 点击创建部署任务，等待完成。模板只查询已有实例并执行安装，不购买、删除、重置服务器。
4. 从部署输出打开专属开通链接。设置自己的管理密码，粘贴该实例的套餐模型 API Key。
5. 手机 QQ 扫码授权。后台自动保存绑定，分别检查保存结果、运行状态，最后由用户确认真实 QQ 回复。

专属链接中的开通码只能用来认领一次，24 小时有效。公开分发的是部署链接，不能分发任何用户的专属开通链接。

## 与旧版的区别

- 安装、首次登录、模型激活与 QQ 绑定分离；安装任务中不提交模型密钥或管理密码。
- 管理页面使用标准 HTTPS 443。申请 Let's Encrypt 公网 IP 证书，不需要域名；证书为短期证书，每天检查续期两次并自动重新加载。证书程序使用 IPv4 连接，保留完整 TLS 验证；失败后最多再重试两次，不修改服务器的全局网络设置。
- 内部端口 6187、6190 只监听本机。QQ 凭据和扫码解密密钥保存在服务器，浏览器只收到二维码图片及状态。
- 主程序使用独立系统用户运行，没有 root 权限；安装和证书续期由独立系统服务执行。
- 管理会话采用 HttpOnly、Secure、SameSite Cookie，校验来源和 CSRF；登录有频率限制。
- 固定安装 AstrBot 4.29.4、Certbot 5.4.0、qrcode 8.2；复用现有大肥鱼人设、工具和记忆插件。

## 运行前提

服务器需要联网，至少 3 GB 可用磁盘。云防火墙及操作系统防火墙需允许 80、443；轻量服务器的默认云规则通常已放行。安装程序不申请云 AccessKey，也不会代改云防火墙。80 用于证书初次签发及续期，不应在安装后关闭。同款 Hermes 镜像的 80 端口已由 nginx 使用：程序会在标准网页目录中创建随机验证文件，确认能读到准确内容后复用该目录，不覆盖原有网页、不重启原有 nginx。若其他网页占用 443、内部端口占用或 80 端口验证目录不匹配，安装停止。

申请证书须由服务器所有者同意 https://letsencrypt.org/repository/ 的订户协议。通过 `--agree-acme` 或部署页面确认后才会申请。失败不会改用不受信任证书，也不会让用户在不安全页面输入密码。

## 制作可发布文件

运行 `python build_web.py --origin https://你的可信发布站点`。输出到项目的 `dist/web-1.1.0-beta2`，包含 `.run`、源码压缩包、固定 SHA-256 的 ROS 模板和入口页。

将 `.run`、`dafeiyu-ros.json`、`SHA256SUMS.txt` 放在站点的 `/downloads/`，将 `start` 目录放在 `/start/`。发布站点须能被观众浏览器、阿里云 ROS 和目标服务器访问。部署链接见 `deployment.json`，其中没有客户凭据。

发布前须用实际 HTTPS 下载地址重新构建，并核对文件哈希与模板内固定值一致。不能把尚未发布或未验证的链接宣传成可用入口。若使用 OSS，需要配置 ROS 模板读取所需的 CORS。

## 服务及数据

- 安装根目录：`/opt/dafeiyu-onboard`；已有 `/opt/dafeiyu-kit` 完全独立。
- 首次开通：`https://服务器公网IP/setup/`；完整 AstrBot 后台：`https://服务器公网IP/`。
- 管理账号：`fish`；密码由用户在网页设置。默认不会保存明文密码。
- 配置和数据：`state/instance/data`；管理员密码哈希：`state/owner.hash`。
- 服务：`dafeiyu-onboard`（向导和机器人）、`dafeiyu-web-proxy`（HTTPS）。
- 证书检查：`dafeiyu-web-renew.timer`；备份：`dafeiyu-web-backup.timer`，北京时间 04:25，保留最近 7 份。
- 备份仍为本机副本，包含模型和 QQ 配置，需要私密保存；不能抵御整台机器被删除。

安装命令：`sudo bash dafeiyu-web-1.1.0-beta2.run --public-ip 你的公网IP --agree-acme`。

失败后以相同包和相同参数重试可续装，不是版本升级命令。修改了公网 IP、已部署其他网站或需要迁移旧数据时，不应直接重复安装。

开通码过期、尚未认领时，管理员可以执行 `sudo /opt/dafeiyu-onboard/venv/bin/python /opt/dafeiyu-onboard/source/provision.py reset-ticket 公网IP`。认领之后不能用开通码覆盖密码。

## 测试状态和发布边界

该版本属于开发测试版。自动化测试覆盖认领、登录、来源检查、CSRF、敏感信息不回显、错误密钥重试、重复激活保护、二维码过期、绑定自动保存失败后的恢复以及真实回复确认的区分。实际云安装、可信证书签发与续期、ROS 输出、QQ 实际授权需要独立验收，不能以本地模拟测试代替。

## 依据

- https://help.aliyun.com/zh/ros/developer-reference/aliyun-swas-runcommand
- https://help.aliyun.com/zh/ros/developer-reference/datasource-swas-instance
- https://help.aliyun.com/zh/ros/developer-reference/create-a-custom-stack-creation-page-in-ros
- https://letsencrypt.org/2026/03/11/shorter-certs-certbot
- AstrBot 4.29.4 随包源码与 AGPL-3.0 许可证，见 THIRD_PARTY.md 和 licenses。


## 2026-11-04 新版同步

参见 RELEASE_NOTES.md。已整合线上记忆插件 v1.1.0 与 QQ 单独 @ 的兼容修复。新建默认人格包含时间、计算、记忆保存、检索、删除五项工具；自动记忆默认关闭。

部署入口优先使用 dafeiyu-ros-1.1.0-beta2.json，兼容入口 dafeiyu-ros.json 也指向同一包。旧 beta1 安装文件与旧哈希保留，避免已打开的旧模板失配。发布本版本不会升级其他人已经安装的实例。
