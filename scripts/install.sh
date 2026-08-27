#!/bin/bash
# MiniMax H3 WebUI 一键安装脚本
set -e

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONDA_ENV="${CONDA_ENV:-/root/miniconda3/envs/comfyui}"
PYTHON="$CONDA_ENV/bin/python3"

echo "========================================"
echo "  MiniMax H3 WebUI 安装脚本"
echo "========================================"

# 检查 Python
if [ ! -f "$PYTHON" ]; then
    echo "❌ 未找到 Python: $PYTHON"
    echo "   请设置 CONDA_ENV 环境变量指向 conda 环境路径"
    exit 1
fi

# 检查依赖
echo "📦 检查 Python 依赖..."
$PYTHON -c "import fastapi, uvicorn, requests, pydantic" 2>/dev/null || {
    echo "安装缺少的依赖..."
    $PYTHON -m pip install fastapi uvicorn requests pydantic
}

# 创建工作目录
echo "📁 创建工作目录..."
mkdir -p /root/webui/static
mkdir -p /root/workflow_configs
mkdir -p /tmp/comfyui_progress
mkdir -p /mnt/storage/MMX_ComfyUI-input
mkdir -p /mnt/storage/MMX_ComfyUI-output/Haimi

# 复制文件
echo "📋 部署文件..."
cp "$BASE_DIR/webui/server.py" /root/webui/
cp "$BASE_DIR/webui/version.py" /root/webui/
cp -r "$BASE_DIR/webui/static/"* /root/webui/static/
cp "$BASE_DIR/proxy/comfyui_proxy_server.py" /root/
cp "$BASE_DIR/workflow_configs/"*.json /root/workflow_configs/ 2>/dev/null || true

# 安装 systemd 服务
echo "🔧 安装 systemd 服务..."
cp "$BASE_DIR/deploy/comfyui-proxy.service" /etc/systemd/system/
cp "$BASE_DIR/deploy/webui.service" /etc/systemd/system/
systemctl daemon-reload

# 启用开机自启
systemctl enable comfyui-proxy.service
systemctl enable webui.service

# 启动服务
echo "🚀 启动服务..."
systemctl start comfyui-proxy.service
sleep 2
systemctl start webui.service
sleep 2

# 检查状态
echo ""
echo "========================================"
echo "  安装完成！服务状态："
echo "========================================"
echo -n "  Proxy (8026): " && systemctl is-active comfyui-proxy.service
echo -n "  WebUI (8025): " && systemctl is-active webui.service
echo ""
echo "  访问地址: http://$(hostname -I | awk "{print \$1}"):8025"
echo "========================================"
