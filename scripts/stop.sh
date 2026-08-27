#!/bin/bash
echo "停止 ComfyUI WebUI 服务..."
systemctl stop webui.service
systemctl stop comfyui-proxy.service
echo ""
echo -n "Proxy (8026): " && systemctl is-active comfyui-proxy.service
echo -n "WebUI (8025): " && systemctl is-active webui.service
