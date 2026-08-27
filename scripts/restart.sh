#!/bin/bash
echo "重启 ComfyUI WebUI 服务..."
systemctl restart comfyui-proxy.service
sleep 1
systemctl restart webui.service
sleep 2
echo ""
echo -n "Proxy (8026): " && systemctl is-active comfyui-proxy.service
echo -n "WebUI (8025): " && systemctl is-active webui.service
