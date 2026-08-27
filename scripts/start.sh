#!/bin/bash
echo "启动 ComfyUI WebUI 服务..."
systemctl start comfyui-proxy.service
sleep 1
systemctl start webui.service
sleep 2
echo ""
echo -n "Proxy (8026): " && systemctl is-active comfyui-proxy.service
echo -n "WebUI (8025): " && systemctl is-active webui.service
