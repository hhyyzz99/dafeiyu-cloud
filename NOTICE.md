# 开源来源与修改说明

本项目基于 AstrBot，不是 AstrBot 或阿里云的官方发行版，也不代表这些项目或服务对本项目背书。

## AstrBot

- 上游项目：https://github.com/AstrBotDevs/AstrBot
- 使用版本：4.29.4
- 对应上游源码：https://github.com/AstrBotDevs/AstrBot/tree/v4.29.4
- 许可证：GNU Affero General Public License v3.0，全文见 `source/licenses/AstrBot-AGPL-3.0.txt`。
- 原作者与贡献者的版权声明保留在上游源码中。AstrBot 及对其代码的修改继续遵守上游许可证。

## 本发行版的修改和新增内容

记录日期：2026-11-05；安装包版本：1.1.0-beta3；大肥鱼插件版本：1.1.1。

- `source/fix_empty_mention.py`：对 AstrBot 4.29.4 的 `astrbot/builtin_stars/astrbot/main.py` 应用补丁，使 QQ 官方接入仅发送 @ 时也能正常触发。补丁保留原文件备份，并检查原文件 SHA-256 后才修改；它不修改其他平台的消息处理。
- `source/plugin/`：大肥鱼聊天辅助、手动记忆、可选自动记忆与状态指令。
- 安装脚本、ROS 模板和网页开通流程：准备环境、配置服务、激活套餐模型与 QQ 扫码绑定。
- `source/persona.md`：随安装包提供的初始人设，不是作者服务器当前的私人配置。

对应补丁、插件、安装脚本及重建说明均在本仓库公开。安装时使用 Python 包索引提供的 AstrBot，并未把用户的运行目录、账户、密钥、聊天或记忆数据打包上传。

## 再分发与网络使用

仅写一句“感谢开源作者”不能替代许可证义务。保留原版权与许可证声明；修改、再分发或以网络服务形式提供修改后的 AGPL 软件时，应按许可证保留修改说明并向相应用户提供对应源码的获取入口。不要删除 AstrBot 自带的开源信息，也不要把本项目表述为从零自研的机器人框架。

其他依赖分别遵循自己的许可证；详情见 `source/THIRD_PARTY.md`。本说明不改变上游许可证，也不为不相关的独立文件自动指定许可证。
