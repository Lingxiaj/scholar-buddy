#!/bin/bash
# Alibaba Cloud 一键部署脚本
# 使用方法: bash deploy/setup.sh

set -e

echo "=== 1. 安装 Python 依赖 ==="
apt update && apt install -y python3-pip git uuid-runtime
pip3 install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

echo "=== 2. 配置环境变量 ==="
if [ ! -f .env ]; then
    cat > .env << 'EOF'
# ⚠️ 请修改为你的 API Key
OPENAI_API_KEY=sk-你的deepseek或openai密钥
OPENAI_BASE_URL=https://api.deepseek.com/v1
EOF
    echo "  → .env 文件已创建，请编辑填入你的 API Key"
    echo "  → nano .env"
    exit 1
fi
source .env

echo "=== 3. 创建 systemd 服务（开机自启）==="
PROJECT_DIR=$(pwd)
cat > /etc/systemd/system/agent-template.service << EOF
[Unit]
Description=Agent Template Web Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=$(which uvicorn) server.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable agent-template
systemctl start agent-template

echo "=== 4. 放行防火墙（阿里云还需在控制台安全组放行 8000 端口）==="
ufw allow 8000/tcp 2>/dev/null || true

echo ""
echo "✅ 部署完成！"
echo ""
echo "服务器 IP: $(curl -s http://checkip.amazonaws.com 2>/dev/null || curl -s https://api.ipify.org)"
echo "访问地址: http://$(curl -s https://api.ipify.org):8000"
echo ""
echo "⚠️ 如果无法访问，请到阿里云控制台 → 安全组 → 添加入方向规则："
echo "   端口范围: 8000, 授权对象: 0.0.0.0/0"
echo ""
echo "📋 常用命令:"
echo "   systemctl status agent-template   # 查看状态"
echo "   journalctl -u agent-template -f   # 查看日志"
echo "   systemctl restart agent-template  # 重启"
