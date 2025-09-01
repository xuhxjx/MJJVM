#!/bin/bash
set -euo pipefail

# 一键安装，创建 venv，安装依赖，注册 systemd
RUNNER_USER=${SUDO_USER:-$USER}

BOT_DIR="/opt/mjjvm"
ENV_FILE="$BOT_DIR/.env"
VENV_DIR="$BOT_DIR/mjjvm-venv"
SERVICE_FILE="/etc/systemd/system/mjjvm.service"
SCRIPT_URL="https://raw.githubusercontent.com/ryty1/MJJVM/main/2.py"
SCRIPT_PATH="$BOT_DIR/2.py"


# 安装前检查 Python 和 curl
check_and_install() {
    # 检查 Python
    if ! command -v python3 >/dev/null 2>&1; then
        echo "❌ 未找到 Python3，正在安装..."
        sudo apt-get update -y  >/dev/null 2>&1
        sudo apt-get install -y python3 python3-pip  >/dev/null 2>&1
        echo "✅ Python3 安装完成"
    else
        echo "✅ Python3 已安装"
    fi

    # 检查 curl
    if ! command -v curl >/dev/null 2>&1; then
        echo "❌ 未找到 curl，正在安装..."
        sudo apt-get install -y curl  >/dev/null 2>&1
        echo "✅ curl 安装完成"
    else
        echo "✅ curl 已安装"
    fi
}

# 选择操作（安装/修改配置/卸载）
echo "请选择操作："
echo "1. 安装 MJJVM 监控"
echo "2. 修改 .env 配置"
echo "3. 卸载 MJJVM 监控"
echo "4. 返回 VIP 工具箱"
read -p "输入选项 [1-4]: " ACTION

case $ACTION in
1)
    # 在开始前检查 Python 和 curl
    check_and_install
    
    echo "安装目录：$BOT_DIR"
    echo "脚本将以用户：$RUNNER_USER 来拥有并运行"

    # 创建目录并设置权限
    sudo mkdir -p "$BOT_DIR"
    sudo chown -R "$RUNNER_USER:$RUNNER_USER" "$BOT_DIR"
    cd "$BOT_DIR" || { echo "无法切换到 $BOT_DIR"; exit 1; }

    # 下载 bot 脚本
    echo "🔽 正在下载 MJJVM 脚本..."
    if command -v curl >/dev/null 2>&1; then
        sudo -u "$RUNNER_USER" curl -fsSL "$SCRIPT_URL" -o "$SCRIPT_PATH"
    elif command -v wget >/dev/null 2>&1; then
        sudo -u "$RUNNER_USER" wget -qO "$SCRIPT_PATH" "$SCRIPT_URL"
    else
        echo "❌ 未找到 curl 或 wget，请先安装其中一个工具。"
        exit 1
    fi

    if [ ! -s "$SCRIPT_PATH" ]; then
        echo "❌ 下载失败或文件为空：$SCRIPT_PATH"
        exit 1
    fi
    sudo chown "$RUNNER_USER:$RUNNER_USER" "$SCRIPT_PATH"
    chmod +x "$SCRIPT_PATH"
    echo "✅ 脚本下载并保存为 $SCRIPT_PATH"

    # 交互式生成 .env
    echo "📝 请按提示输入 ENV 配置（将写入 $ENV_FILE）"
    read -p "TG_TOKEN: " TG_TOKEN
    read -p "用户ID/群号ID (多个英文逗号分隔): " TG_CHAT_IDS


    cat > "$ENV_FILE" <<EOF
TG_TOKEN=$TG_TOKEN
TG_CHAT_IDS=$TG_CHAT_IDS
EOF

    sudo chown "$RUNNER_USER:$RUNNER_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "✅ 已生成 $ENV_FILE (权限 600)"

    # 创建虚拟环境
    if [ ! -d "$VENV_DIR" ]; then
        echo "🔧 创建虚拟环境..."
        sudo -u "$RUNNER_USER" python3 -m venv "$VENV_DIR"
        echo "✅ 虚拟环境已创建：$VENV_DIR"
    fi

    # 安装依赖
    echo "📦 在 venv 中安装依赖..."
    "$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null 2>&1

    REQUIRED_PKG=("python-telegram-bot==13.14" "python-dotenv" "requests" "beautifulsoup4")
    for pkg in "${REQUIRED_PKG[@]}"; do
        PKG_NAME="${pkg%%=*}"
        if ! "$VENV_DIR/bin/python" -m pip show "$PKG_NAME" >/dev/null 2>&1; then
            echo "安装 $pkg ..."
            "$VENV_DIR/bin/python" -m pip install "$pkg" >/dev/null 2>&1
        else
            echo "已安装: $PKG_NAME （跳过）"
        fi
    done
    echo "✅ 依赖安装完成（均安装在 $VENV_DIR）"

    # 创建 systemd 服务文件
    echo "⚙️ 写入 systemd 服务：$SERVICE_FILE"
    sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=MJJVM Stock Monitor
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/mjjvm
ExecStart=/opt/mjjvm/mjjvm-venv/bin/python /opt/mjjvm/2.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload >/dev/null 2>&1
    sudo systemctl enable mjjvm >/dev/null 2>&1
    sudo systemctl restart mjjvm >/dev/null 2>&1

    echo "✅ 安装完成，服务已启动：mjjvm 监控"
    echo "查看状态： sudo systemctl status mjjvm"
    echo "查看日志： sudo journalctl -u mjjvm -f"
    ;;
2)
    if [ ! -f "$ENV_FILE" ]; then
        echo "❌ 未找到 .env 文件，请先安装 mjjvm 监控！"
        exit 1
    fi

    echo "📝 请按提示修改 Bot 配置（当前配置存储在 $ENV_FILE）"
    source "$ENV_FILE"

    CHANGED=0

    declare -A VAR_LABELS=(
        ["TG_TOKEN"]="TG_TOKEN"
        ["TG_CHAT_IDS"]="用户ID/群号ID"

    )

    update_var() {
        local var_name=$1
        local label=${VAR_LABELS[$var_name]:-$var_name}
        local current_value=${!var_name}
        echo -e "\n当前 $label = $current_value"
        read -p "是否修改 $label? (y/n): " choice
        if [[ "$choice" == "y" ]]; then
            read -p "请输入新的 $label: " new_value
            echo "$var_name=$new_value" >> "$BOT_DIR/.env.tmp"
            CHANGED=1
        else
            echo "$var_name=$current_value" >> "$BOT_DIR/.env.tmp"
        fi
    }

    rm -f "$BOT_DIR/.env.tmp"
    update_var "TG_TOKEN"
    update_var "TG_CHAT_IDS"

    mv "$BOT_DIR/.env.tmp" "$ENV_FILE"
    echo "✅ 配置已保存：$ENV_FILE"

    if [[ $CHANGED -eq 1 ]]; then
        sudo systemctl restart mjjvm >/dev/null 2>&1
        echo "✅ 服务已重启：mjjvm 监控"
    else
        echo "ℹ️ 配置未修改，服务无需重启"
    fi

    echo "查看状态： sudo systemctl status mjjvm"
    echo "查看日志： sudo journalctl -u mjjvm -f"
    ;;

3)
    echo "⚠️ 警告：此操作会删除 mjjvm 监控 服务和相关文件，请确认！"
    read -p "是否继续卸载 mjjvm 监控? (y/n): " choice

    if [[ "$choice" != "y" ]]; then
        echo "❌ 已取消卸载"
        exit 1
    fi

    if [ -f "$SERVICE_FILE" ]; then
        echo "🛑 停止 mjjvm 监控 服务..."
        sudo systemctl stop mjjvm >/dev/null 2>&1
        sudo systemctl disable mjjvm >/dev/null 2>&1
    else
        echo "✅ 未检测到 mjjvm 服务"
    fi

    echo "🗑 删除虚拟环境和 mjjvm 监控脚本..."
    sudo rm -rf "$BOT_DIR" >/dev/null 2>&1

    echo "🗑 删除 mjjvm 监控 服务文件..."
    sudo rm -f "$SERVICE_FILE" >/dev/null 2>&1

    echo "🔄 重新加载 systemd..."
    sudo systemctl daemon-reload >/dev/null 2>&1

    echo "✅ 卸载完成，已删除所有相关文件"
    ;;
4)
    bash <(curl -Ls https://raw.githubusercontent.com/ryty1/Checkin/refs/heads/main/vip.sh)
    ;;
*)
    echo "❌ 无效选项"
    exit 1
    ;;
esac
