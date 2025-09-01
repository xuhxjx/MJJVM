#!/opt/mjjvm/mjjvm-venv/bin/python3
# -*- coding: utf-8 -*-
import requests
from bs4 import BeautifulSoup
import time
import json
import os
import telegram
from telegram.ext import Updater, CommandHandler
import logging
from logging.handlers import RotatingFileHandler
import threading
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import warnings
from dotenv import load_dotenv


# ---------------------------- 配置 ----------------------------
URLS = {
    "香港区": "https://www.mjjvm.com/cart?fid=1&gid=1",
    "美国区": "https://www.mjjvm.com/cart?fid=1&gid=2",
    "欧洲区": "https://www.mjjvm.com/cart?fid=1&gid=3",
    "亚洲区": "https://www.mjjvm.com/cart?fid=1&gid=4",
}

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "max-age=0",
    "Referer": "https://www.mjjvm.com",
    "Sec-CH-UA": '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
}

# 加载 .env 文件
load_dotenv()

TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_IDS = os.getenv("TG_CHAT_IDS", "").split(",")

INTERVAL = 120  # 秒
DATA_FILE = "stock_data.json"
LOG_FILE = "stock_monitor.log"

# ---------------------------- 日志 ----------------------------
warnings.filterwarnings("ignore", category=FutureWarning)
logger = logging.getLogger("StockMonitor")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1*1024*1024, backupCount=1, encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# ---------------------------- 工具函数 ----------------------------
def load_previous_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def group_by_region(all_products):
    """把扁平字典按地区分组为列表"""
    grouped = {}
    for key, info in all_products.items():
        region = info.get("region", "未知地区")
        grouped.setdefault(region, []).append(info)
    return grouped

# ---------------------------- TG 消息 ----------------------------
def send_telegram(messages):
    if not messages:
        return
    bot = telegram.Bot(token=TG_TOKEN)
    for msg in messages:
        html_msg = ""
        delete_delay = None
        reply_markup = None
        region = msg.get("region", "未知地区")

        if msg["type"] == "上架":
            prefix = "🟢"
            html_msg += (
                f"{prefix} <b>{msg['type']} - {region} - {msg['name']}</b>\n"
                f"库存: <b>{msg['stock']}</b>\n"
                f"会员专享: <b>{msg['member_only']}</b>\n"
            )
            if msg.get("config"):
                html_msg += f"配置:\n<pre>{msg['config']}</pre>\n"
            button = InlineKeyboardButton(text="快速进入通道", url=msg['url'])
            reply_markup = InlineKeyboardMarkup([[button]])

        elif msg["type"] == "库存变化":
            prefix = "🟡"
            html_msg += (
                f"{prefix} <b>{msg['type']} - {region} - {msg['name']}</b>\n"
                f"库存: <b>{msg['stock']}</b>\n"
                f"会员专享: <b>{msg['member_only']}</b>\n\n"
            )
            delete_delay = 60

        else:  # 售罄
            prefix = "🔴"
            html_msg += (
                f"{prefix} <b>{msg['type']} - {region} - {msg['name']}</b>\n"
                f"库存: <b>{msg['stock']}</b>\n"
                f"会员专享: <b>{msg['member_only']}</b>\n\n"
            )

        for chat_id in TG_CHAT_IDS:
            try:
                sent_msg = bot.send_message(
                    chat_id=chat_id, text=html_msg,
                    parse_mode=telegram.ParseMode.HTML,
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error("TG 推送失败 %s: %s", chat_id, e)
                continue
            if delete_delay:
                def delete_msg_after(delay, chat_id=chat_id, message_id=sent_msg.message_id):
                    time.sleep(delay)
                    try:
                        bot.delete_message(chat_id=chat_id, message_id=message_id)
                    except:
                        pass
                threading.Thread(target=delete_msg_after, args=(delete_delay,)).start()

# ---------------------------- 页面解析 ----------------------------
def parse_products(html, url, region):
    soup = BeautifulSoup(html, "html.parser")
    products = {}

    for card in soup.select("div.card.cartitem"):
        # 1. 标题
        name_tag = card.find("h4")
        if not name_tag:
            continue
        name = name_tag.get_text(strip=True)

        # 2. 配置参数
        config_items = []
        member_only = False
        for li in card.select("ul.vps-config li"):
            text = li.get_text(" ", strip=True)

            # 严格匹配 “验证 MJJBOX 成员后免费”
            if text.strip() == "🎁 验证 MJJBOX 成员后免费" or text.strip() == "验证 MJJBOX 成员后免费":
                member_only = True
                continue  # 不写入配置

            config_items.append(text)

        config = "\n".join(config_items)

        # 3. 库存
        stock_tag = card.find("p", class_="card-text")
        stock = 0
        if stock_tag:
            try:
                stock = int(stock_tag.get_text(strip=True).split("库存：")[-1])
            except:
                stock = 0

        # 4. 价格
        price_tag = card.select_one("a.cart-num")
        price = price_tag.get_text(strip=True) if price_tag else "未知"

        # 5. pid
        link_tag = card.select_one("div.card-footer a")
        pid = None
        if link_tag and "pid=" in link_tag.get("href", ""):
            pid = link_tag["href"].split("pid=")[-1]

        products[f"{region} - {name}"] = {
            "name": name,
            "config": config,   # 🚫 不包含验证那行
            "stock": stock,
            "price": price,
            "member_only": member_only,  # ✅ 正确标记
            "url": url,
            "pid": pid,
            "region": region
        }

    return products

# ---------------------------- /vps 命令 ----------------------------
REGION_FLAGS = {
    "香港区": "🇭🇰",
    "美国区": "🇺🇸",
    "欧洲区": "🇪🇺",
    "亚洲区": "🌏",
}

def vps_command(update, context):
    """手动查看当前所有地区的商品库存"""
    current_data = load_previous_data()
    if not current_data:
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📦 暂无库存数据，请等待下一次监控刷新。",
            parse_mode=telegram.ParseMode.HTML
        )
        return

    # 按地区分组
    msg_lines = []
    for region, products in current_data.items():
        flag = REGION_FLAGS.get(region, "🌍")
        msg_lines.append(f"{flag} {region}:")
        for p in products:
            stock = p.get("stock", 0)
            if stock == 0:
                status = "🔴"
            elif stock > 0:
                status = "🟢"
            else:
                status = "🟡"
            vip = "会员专享" if p.get("member_only") else "公开"
            msg_lines.append(f"   {status} {p['name']} | 库存: {stock} | {vip}")
        msg_lines.append("")  # 区与区之间空行

    # 发送消息并保存 Message 对象，用于删除
    sent_msg = context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🖥️ VPS库存情况：\n━━━━━━━━━━━━━━━━━━\n" + "\n".join(msg_lines),
        parse_mode=telegram.ParseMode.HTML
    )

    # 3分钟后自动删除
    def delete_msg():
        time.sleep(180)
        try:
            # 先删用户消息
            context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )
        except Exception as e:
            logger.error("删除用户消息失败: %s", e)

        # 等 0.5 秒再删机器人消息
        time.sleep(0.5)
        try:
            context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=sent_msg.message_id
            )
        except Exception as e:
            logger.error("删除机器人消息失败: %s", e)

    threading.Thread(target=delete_msg, daemon=True).start()

# ---------------------------- TG Bot 启动 ----------------------------
def start_telegram_bot():
    updater = Updater(TG_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("vps", vps_command))
    updater.start_polling()

# ---------------------------- 主循环 ----------------------------
consecutive_fail_rounds = 0  # 放在 main_loop() 外部，保持状态

def main_loop():
    global consecutive_fail_rounds
    prev_data_raw = load_previous_data()
    prev_data = {}
    # 如果文件是按地区分组，则展开成扁平字典便于对比
    for region, plist in prev_data_raw.items():
        for p in plist:
            prev_data[f"{region} - {p['name']}"] = p

    logger.info("库存监控启动，每 %s 秒检查一次...", INTERVAL)

    while True:
        logger.info("正在检查库存...")
        all_products = {}
        success_count = 0
        fail_count = 0
        success = False

        for region, url in URLS.items():
            success_this_url = False
            for attempt in range(3):
                try:
                    resp = requests.get(url, headers=HEADERS, timeout=10)
                    resp.raise_for_status()
                    products = parse_products(resp.text, url, region)
                    all_products.update(products)
                    success_this_url = True
                    logger.info("[%s] 请求成功 (第 %d 次尝试)", region, attempt + 1)
                    break
                except Exception as e:
                    logger.warning("[%s] 请求失败 (第 %d 次尝试): %s", region, attempt + 1, e)
                    time.sleep(2)

            if success_this_url:
                success = True
                success_count += 1
            else:
                fail_count += 1
                logger.error("[%s] 请求失败:, 尝试 3 次均失败", region)

        logger.info("本轮请求完成: 成功 %d / %d, 失败 %d", success_count, len(URLS), fail_count)

        # --- 增加连续失败判断 ---
        if success_count == 0:  # 本轮全部失败
            consecutive_fail_rounds += 1
            logger.warning("本轮全部请求失败，连续失败轮数: %d", consecutive_fail_rounds)
        else:
            consecutive_fail_rounds = 0  # 本轮成功，重置计数

        if consecutive_fail_rounds >= 10:
            try:
                bot = telegram.Bot(token=TG_TOKEN)
                alert_msg = f"⚠️ 警告：库存监控请求失败，请检查网络或服务器！"
                for chat_id in TG_CHAT_IDS:
                    bot.send_message(chat_id=chat_id, text=alert_msg)
            except Exception as e:
                logger.error("TG报警发送失败: %s", e)
            consecutive_fail_rounds = 0  # 触发报警后重置

        if not success:
            logger.warning("本轮请求全部失败，跳过数据更新。")
            time.sleep(INTERVAL)
            continue

        messages = []
        for name, info in all_products.items():
            if not info.get("member_only", False):
                continue
            prev_stock = prev_data.get(name, {}).get("stock", 0)
            curr_stock = info["stock"]
            msg_type = None
            if prev_stock == 0 and curr_stock > 0:
                msg_type = "上架"
            elif prev_stock > 0 and curr_stock == 0:
                msg_type = "售罄"
            elif prev_stock != curr_stock:
                msg_type = "库存变化"
            if msg_type:
                msg = {
                    "type": msg_type,
                    "name": info["name"],
                    "stock": curr_stock,
                    "config": info.get('config', ''),
                    "member_only": True,
                    "url": info['url'],
                    "region": info.get("region", "未知地区")
                }
                messages.append(msg)
                logger.info("%s - %s | 库存: %s | 会员专享: True", msg_type, info["name"], curr_stock)

        if messages:
            send_telegram(messages)

        # 保存前转换格式
        grouped_data = group_by_region(all_products)
        save_data(grouped_data)
        prev_data = all_products

        logger.info("当前库存快照:")
        for name, info in all_products.items():
            logger.info("- [%s] %s | 库存: %s | 会员专享: %s", info.get("region", "未知地区"), info["name"], info["stock"], info["member_only"])

        time.sleep(INTERVAL)

# ---------------------------- 启动 ----------------------------
if __name__ == "__main__":
    threading.Thread(target=start_telegram_bot, daemon=True).start()
    main_loop()
