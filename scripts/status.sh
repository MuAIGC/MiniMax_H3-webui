#!/bin/bash
echo "========================================"
echo "  MiniMax H3 WebUI 服务状态"
echo "========================================"
echo ""
echo "--- 端口监听 ---"
echo -n "8025 (WebUI): " && (lsof -i :8025 -sTCP:LISTEN 2>/dev/null | grep -q python3 && echo "✅ 正常" || echo "❌ 未监听")
echo -n "8026 (Proxy): " && (lsof -i :8026 -sTCP:LISTEN 2>/dev/null | grep -q python3 && echo "✅ 正常" || echo "❌ 未监听")
echo ""
echo "--- systemd 服务 ---"
echo -n "webui.service:       " && systemctl is-active webui.service
echo -n "comfyui-proxy.service: " && systemctl is-active comfyui-proxy.service
echo ""
echo "--- 开机自启 ---"
echo -n "webui.service:       " && systemctl is-enabled webui.service
echo -n "comfyui-proxy.service: " && systemctl is-enabled comfyui-proxy.service
echo ""
echo "--- API 测试 ---"
echo -n "WebUI API: " && (curl -s -m 3 http://localhost:8025/api/version > /dev/null && echo "✅ 正常" || echo "❌ 无响应")
echo -n "Proxy API: " && (curl -s -m 3 http://localhost:8026/ > /dev/null && echo "✅ 正常" || echo "❌ 无响应")
echo ""
echo "--- 资源使用 ---"
ps -eo pid,%cpu,%mem,rss,comm | grep -E "python3" | grep -v grep | while read pid cpu mem rss comm; do
    cmdline=$(cat /proc/$pid/cmdline 2>/dev/null | tr "\0" " " | head -c 80)
    echo "  PID=$pid  CPU=${cpu}%  MEM=${mem}%  RSS=${rss}KB  $cmdline"
done
echo ""
echo "--- 任务统计 ---"
python3 -c "
import sqlite3
conn = sqlite3.connect(\"/tmp/webui_tasks.db\")
cur = conn.cursor()
cur.execute(\"SELECT status, COUNT(*) FROM tasks GROUP BY status\")
for r in cur.fetchall():
    print(f\"  {r[0]:12} {r[1]}\")
conn.close()
" 2>/dev/null || echo "  (无数据库)"
echo "========================================"
