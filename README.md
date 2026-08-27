# MiniMax H3 WebUI

ComfyUI 视频生成任务的 Web 管理界面，支持通过浏览器提交视频生成任务、实时查看进度、管理历史记录。

## 架构概览

```
用户浏览器
    │
    ▼
┌─────────────────────┐
│  WebUI 服务 (8025)   │  ← 用户界面 + 任务管理
│  server.py           │
└────────┬────────────┘
         │ HTTP
         ▼
┌─────────────────────┐
│  Proxy 服务 (8026)   │  ← ComfyUI 工作流代理
│  comfyui_proxy_      │
│  server.py           │
└────────┬────────────┘
         │ HTTP
         ▼
┌─────────────────────┐
│  ComfyUI (8188)      │  ← AI 视频生成引擎
│  MiniMax_H3 模型     │
└─────────────────────┘
```

## 目录结构

```
MiniMax_H3-webui/
├── README.md                           # 本文档
├── proxy/                              # 代理服务源码
│   └── comfyui_proxy_server.py         # ComfyUI 工作流代理（端口 8026）
├── webui/                              # WebUI 服务源码
│   ├── server.py                       # WebUI 管理服务（端口 8025）
│   ├── version.py                      # 版本信息
│   ├── settings.json.example           # 运行时配置示例
│   ├── config_presets.json.example     # 配置预设示例
│   └── static/                         # 前端静态文件
│       ├── index.html                  # 主界面
│       ├── admin.html                  # 管理界面
│       ├── prompt_guide.md             # 提示词指南
│       ├── prompt_guide.html           # 提示词指南（HTML 版）
│       ├── wechat_qr.jpeg              # 微信二维码
│       └── presets/                    # 预设素材
│           ├── resources.json
│           ├── images/                 # 示例图片
│           ├── audios/                 # 示例音频
│           └── prompts/               # 提示词模板
├── workflow_configs/                   # ComfyUI 工作流配置
│   └── MiniMax_H3.json                 # MiniMax H3 模型工作流
├── deploy/                             # systemd 部署文件
│   ├── webui.service                   # WebUI 服务配置
│   └── comfyui-proxy.service           # Proxy 服务配置
└── scripts/                            # 管理脚本
    ├── install.sh                      # 一键安装
    ├── start.sh                        # 启动服务
    ├── stop.sh                         # 停止服务
    ├── restart.sh                      # 重启服务
    └── status.sh                       # 查看状态
```

## 快速部署

### 前置要求

- Python 3.10+（推荐 conda 环境）
- ComfyUI 已安装并运行（端口 8188）
- MiniMax H3 模型已配置
- 依赖包：`fastapi`, `uvicorn`, `requests`, `pydantic`

### 一键安装

```bash
cd /mnt/storage/MMX/MiniMax_H3-webui
bash scripts/install.sh
```

安装脚本会自动：
1. 检查并安装 Python 依赖
2. 创建工作目录
3. 部署源码到运行位置
4. 安装 systemd 服务并设置开机自启
5. 启动服务

### 手动部署

```bash
# 1. 复制源码
cp proxy/comfyui_proxy_server.py /root/
cp webui/server.py webui/version.py /root/webui/
cp -r webui/static/* /root/webui/static/
cp workflow_configs/*.json /root/workflow_configs/

# 2. 安装 systemd 服务
cp deploy/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable comfyui-proxy.service webui.service

# 3. 启动
systemctl start comfyui-proxy.service
systemctl start webui.service
```

## 服务管理

### 使用脚本

```bash
scripts/start.sh      # 启动
scripts/stop.sh       # 停止
scripts/restart.sh    # 重启
scripts/status.sh     # 查看完整状态
```

### 使用 systemd

```bash
# 查看状态
systemctl status webui
systemctl status comfyui-proxy

# 启动/停止/重启
systemctl start webui
systemctl stop webui
systemctl restart webui

# 查看日志
journalctl -u webui -f
journalctl -u comfyui-proxy -f
```

## 端口说明

| 端口 | 服务 | 说明 |
|------|------|------|
| 8025 | WebUI | 用户界面，浏览器访问 |
| 8026 | Proxy | ComfyUI 工作流代理 API |
| 8188 | ComfyUI | ComfyUI 原生界面（需单独部署） |

## 目录说明

| 路径 | 说明 |
|------|------|
| `/root/webui/` | WebUI 运行目录 |
| `/root/webui/server.py` | WebUI 服务主程序 |
| `/root/webui/static/` | 前端静态文件 |
| `/root/webui/settings.json` | 运行时配置（保存路径等） |
| `/root/comfyui_proxy_server.py` | Proxy 服务主程序 |
| `/root/workflow_configs/` | 工作流配置目录 |
| `/tmp/webui_tasks.db` | SQLite 任务数据库 |
| `/tmp/comfyui_progress/` | 任务进度文件目录 |
| `/mnt/storage/MMX_ComfyUI-input/` | ComfyUI 输入文件目录 |
| `/mnt/storage/MMX_ComfyUI-output/` | ComfyUI 输出文件目录 |
| `/mnt/storage/MMX_ComfyUI-output/Haimi/` | WebUI 生成的视频保存目录 |

## 功能特性

- **视频生成**：通过 Web 界面提交视频生成任务，支持图片/音频/视频参考
- **实时进度**：显示 ComfyUI 节点级别的执行进度百分比
- **任务队列**：支持多任务排队，自动提交到 ComfyUI 队列
- **任务状态**：待提交 → 排队中 → 处理中 → 完成/失败
- **中文支持**：完整支持中文文件名和中文提示词
- **文件浏览器**：内置文件浏览器，可选择服务器上的素材
- **历史记录**：自动保存生成的视频，支持目录浏览
- **配置预设**：保存和加载常用配置组合
- **提示词模板**：内置提示词模板库
- **ComfyUI 同步**：可从 ComfyUI 原生界面同步任务到 WebUI
- **OOM 检测**：自动检测显存不足错误并给出优化建议

## 依赖

```
fastapi
uvicorn
requests
pydantic
```

Python 3.10+ 环境，推荐使用 conda：

```bash
conda create -n comfyui python=3.12
conda activate comfyui
pip install fastapi uvicorn requests pydantic
```

## systemd 服务配置

### webui.service

```ini
[Unit]
Description=ComfyUI WebUI Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/webui
Environment=PYTHONUNBUFFERED=1
ExecStart=/root/miniconda3/envs/comfyui/bin/python3 /root/webui/server.py
Restart=always
RestartSec=10
StandardOutput=append:/root/webui/server.log
StandardError=append:/root/webui/server.log

[Install]
WantedBy=multi-user.target
```

### comfyui-proxy.service

```ini
[Unit]
Description=ComfyUI Multi-Workflow API Proxy Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root
ExecStart=/root/miniconda3/envs/comfyui/bin/python3 /root/comfyui_proxy_server.py
Restart=always
RestartSec=10
StandardOutput=append:/root/proxy_server.log
StandardError=append:/root/proxy_server.log

[Install]
WantedBy=multi-user.target
```

## 故障排查

### 端口被占用

```bash
# 查看占用端口的进程
lsof -i :8025
lsof -i :8026

# 杀掉占用进程
kill -9 <PID>

# 重启服务
systemctl restart webui
systemctl restart comfyui-proxy
```

### 服务无法启动

```bash
# 查看详细日志
journalctl -u webui --no-pager -n 50
journalctl -u comfyui-proxy --no-pager -n 50

# 手动运行测试
/root/miniconda3/envs/comfyui/bin/python3 /root/webui/server.py
/root/miniconda3/envs/comfyui/bin/python3 /root/comfyui_proxy_server.py
```

### 任务卡住

```bash
# 查看任务状态
bash scripts/status.sh

# 检查 ComfyUI 队列
curl -s http://localhost:8188/queue | python3 -m json.tool

# 检查进度文件
ls -la /tmp/comfyui_progress/
```

### 显存不足 (OOM)

降低以下参数：
- 分辨率质量：建议 0.3~0.5
- 视频时长：建议 ≤10 秒
- 参考图片数量：尽量减少
- 画面比例：使用较小的比例如 1:1 或 4:3
