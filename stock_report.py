#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股持仓播报 - GitHub Actions 版
查询实时行情 → 生成图片 → 推送企业微信
数据源：新浪财经API（免费，无需认证）
自动推送：交易日 9:30-15:00 每半小时 (GitHub Actions cron)
"""

from PIL import Image, ImageDraw, ImageFont
import base64
import hashlib
import io
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

# ========== 配置区 ==========
# 持仓列表从 holdings.json 读取（修改 holdings.json 即可更新持仓）
def load_holdings():
    """从 holdings.json 加载持仓配置"""
    try:
        with open("holdings.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] 读取 holdings.json 失败: {e}")
        sys.exit(1)

# 企业微信 Webhook（从环境变量读取，也可直接填入）
WECOM_WEBHOOK = os.environ.get("WECOM_WEBHOOK", "")

# 北京时区
BJT = timezone(timedelta(hours=8))


def check_trading_hours():
    """检查当前是否在A股交易时段，返回 (is_trading, time_str)"""
    now = datetime.now(BJT)
    time_str = now.strftime("%Y-%m-%d %H:%M")
    weekday = now.weekday()  # 0=Mon, 6=Sun
    t = now.hour * 100 + now.minute

    if weekday >= 5:  # 周末
        return False, time_str
    if (925 <= t <= 1135) or (1255 <= t <= 1545):
        return True, time_str
    return False, time_str


def query_sina(codes):
    """通过新浪财经API查询实时行情"""
    sina_codes = []
    for c in codes:
        if c.startswith("6"):
            sina_codes.append(f"sh{c}")
        else:
            sina_codes.append(f"sz{c}")

    url = f"http://hq.sinajs.cn/list={','.join(sina_codes)}"
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("gbk", errors="replace")

    results = {}
    for line in raw.strip().split("\n"):
        m = re.match(r'var hq_str_(\w+)="(.*)";', line.strip())
        if not m:
            continue
        raw_code = m.group(1)
        data = m.group(2).split(",")
        if len(data) < 10:
            continue

        stock_code = raw_code[2:]
        results[stock_code] = {
            "name": data[0],
            "open": float(data[1]) if data[1] else 0,
            "yesterday_close": float(data[2]) if data[2] else 0,
            "current": float(data[3]) if data[3] else 0,
            "high": float(data[4]) if data[4] else 0,
            "low": float(data[5]) if data[5] else 0,
        }

    return results


def query_tencent(codes):
    """通过腾讯财经API查询（备用）"""
    qq_codes = []
    for c in codes:
        if c.startswith("6"):
            qq_codes.append(f"sh{c}")
        else:
            qq_codes.append(f"sz{c}")

    url = f"http://qt.gtimg.cn/q={','.join(qq_codes)}"
    headers = {"User-Agent": "Mozilla/5.0"}

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("gbk", errors="replace")

    results = {}
    for line in raw.strip().split("\n"):
        m = re.match(r'v_(\w+)="(.*)";', line.strip())
        if not m:
            continue
        data = m.group(2).split("~")
        if len(data) < 45:
            continue

        stock_code = data[2]
        results[stock_code] = {
            "name": data[1],
            "current": float(data[3]) if data[3] else 0,
            "yesterday_close": float(data[4]) if data[4] else 0,
            "open": float(data[5]) if data[5] else 0,
            "high": float(data[33]) if data[33] else 0,
            "low": float(data[34]) if data[34] else 0,
        }

    return results


def fetch_stock_data(holdings):
    """获取股票数据，新浪优先，腾讯备用"""
    codes = [h["code"] for h in holdings]

    try:
        results = query_sina(codes)
        if results:
            print(f"[OK] 新浪API查询成功，获取 {len(results)} 只股票")
            return results
    except Exception as e:
        print(f"[WARN] 新浪API失败: {e}")

    try:
        results = query_tencent(codes)
        if results:
            print(f"[OK] 腾讯API查询成功（备用），获取 {len(results)} 只股票")
            return results
    except Exception as e:
        print(f"[ERROR] 腾讯API也失败: {e}")

    return None


# ========== 字体加载 ==========
_font_cache = {}

def load_font(size):
    """加载中文字体，优先系统字体"""
    if size in _font_cache:
        return _font_cache[size]

    import platform

    candidates = []

    if platform.system() == "Windows":
        candidates = [
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        ]

    for path in candidates:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[size] = font
                return font
            except Exception:
                continue

    try:
        font = ImageFont.load_default(size=size)
    except TypeError:
        font = ImageFont.load_default()
    _font_cache[size] = font
    return font


def generate_report_image(stocks_data, time_str, is_trading, holdings):
    """生成持仓播报图片"""
    stocks = []
    for h in holdings:
        code = h["code"]
        d = stocks_data.get(code, {})
        price = d.get("current", 0)
        yc = d.get("yesterday_close", 0)
        change_pct = ((price - yc) / yc * 100) if yc > 0 else 0

        stocks.append({
            "code": code,
            "name": h["name"],
            "hold": h["hold"],
            "cost": h["cost"],
            "price": price,
            "change_pct": change_pct,
        })

    total_market_value = 0
    total_cost = 0
    total_profit = 0
    today_profit = 0
    for s in stocks:
        mv = s["price"] * s["hold"]
        s["mv"] = mv
        s["profit_amt"] = (s["price"] - s["cost"]) * s["hold"]
        s["profit_pct"] = (s["price"] - s["cost"]) / s["cost"] * 100
        yesterday = s["price"] / (1 + s["change_pct"] / 100) if s["change_pct"] != 0 else s["price"]
        s["day_profit"] = (s["price"] - yesterday) * s["hold"]
        total_market_value += mv
        total_cost += s["cost"] * s["hold"]
        total_profit += s["profit_amt"]
        today_profit += s["day_profit"]
    total_profit_pct = total_profit / total_cost * 100 if total_cost > 0 else 0

    SCALE = 2
    W_LOGICAL = 720
    W = W_LOGICAL * SCALE
    PAD_L, PAD_R = 16 * SCALE, 16 * SCALE
    TBL_X = PAD_L
    TBL_W = W - PAD_L - PAD_R
    ROW_H = 56 * SCALE
    HEADER_H = 34 * SCALE
    SUMMARY_H = 100 * SCALE
    TITLE_H = 44 * SCALE
    TIME_H = 10 * SCALE
    NOTICE_H = 10 * SCALE
    FOOTER_H = 10 * SCALE
    N = len(stocks)
    H = PAD_L + TITLE_H + NOTICE_H + HEADER_H + ROW_H * N + 12 * SCALE + SUMMARY_H + FOOTER_H + PAD_L

    BG_WHITE = (248, 249, 252)
    BG_CARD = (255, 255, 255)
    HEADER_BG = (58, 78, 128)
    HEADER_TEXT = (255, 255, 255)
    TEXT_MAIN = (30, 32, 48)
    TEXT_SEC = (100, 104, 128)
    RED = (220, 50, 47)
    GREEN = (16, 132, 60)
    GOLD = (218, 165, 32)
    DIVIDER = (230, 232, 238)
    CARD_SHADOW = (215, 218, 228)
    SUMMARY_BG = (240, 244, 252)
    SUMMARY_BORDER = (180, 190, 220)

    img = Image.new("RGB", (W, H), BG_WHITE)
    draw = ImageDraw.Draw(img)

    font_title = load_font(22 * SCALE)
    font_header = load_font(13 * SCALE)
    font_cell = load_font(14 * SCALE)
    font_cell_bold = load_font(15 * SCALE)
    font_cell_name = load_font(12 * SCALE)
    font_small = load_font(12 * SCALE)
    font_summary_label = load_font(14 * SCALE)
    font_summary_val = load_font(18 * SCALE)
    font_summary_val_big = load_font(22 * SCALE)

    y = PAD_L

    draw.rectangle([TBL_X, y + 4 * SCALE, TBL_X + 5 * SCALE, y + TITLE_H - 4 * SCALE], fill=GOLD)
    draw.text((TBL_X + 16 * SCALE, y + TITLE_H // 2), "持仓播报", fill=TEXT_MAIN, font=font_title, anchor="lm")
    draw.text((W - PAD_R, y + TITLE_H // 2), time_str, fill=TEXT_SEC, font=font_cell, anchor="rm")
    y += TITLE_H

    if is_trading:
        draw.text((TBL_X, y + NOTICE_H // 2), "交易时段 · 实时数据", fill=(30, 132, 60), font=font_small, anchor="lm")
    else:
        draw.text((TBL_X, y + NOTICE_H // 2), "非交易时段 · 数据为最近收盘价", fill=(180, 140, 30), font=font_small, anchor="lm")
    y += NOTICE_H + 8 * SCALE

    col_defs = [
        ("code_name", 0.15),
        ("hold",      0.07),
        ("cost",      0.10),
        ("price",     0.10),
        ("mv",        0.16),
        ("chg",       0.10),
        ("day_pnl",   0.14),
        ("total_pnl", 0.18),
    ]
    col_ws = [int(TBL_W * r) for _, r in col_defs]
    col_ws[-1] = TBL_W - sum(col_ws[:-1])
    col_xs = [TBL_X]
    for cw in col_ws[:-1]:
        col_xs.append(col_xs[-1] + cw)

    draw.rounded_rectangle([TBL_X, y, TBL_X + TBL_W, y + HEADER_H], radius=8 * SCALE, fill=HEADER_BG)
    headers_cn = ["代码/名称", "持有", "成本价", "现价", "市值", "涨跌幅", "当日盈亏", "持仓盈亏"]
    for i, (hdr, cx, cw) in enumerate(zip(headers_cn, col_xs, col_ws)):
        draw.text((cx + cw // 2, y + HEADER_H // 2), hdr, fill=HEADER_TEXT, font=font_header, anchor="mm")
    y += HEADER_H

    for idx, s in enumerate(stocks):
        row_y = y
        bg = BG_CARD if idx % 2 == 0 else (243, 244, 248)
        draw.rectangle([TBL_X, row_y, TBL_X + TBL_W, row_y + ROW_H], fill=bg)
        draw.line([(TBL_X, row_y + ROW_H - 1), (TBL_X + TBL_W, row_y + ROW_H - 1)], fill=DIVIDER, width=1)

        cy = row_y + ROW_H // 2

        c_chg = RED if s["change_pct"] > 0 else GREEN
        c_pnl = RED if s["profit_amt"] > 0 else GREEN
        c_day = RED if s["day_profit"] > 0 else GREEN
        s1 = "+" if s["change_pct"] > 0 else ""
        s2 = "+" if s["profit_amt"] > 0 else ""

        def fmt_money(v):
            sign = "+" if v > 0 else ""
            return f"{sign}{v:,.0f}元"

        draw.text((col_xs[0] + 8 * SCALE, cy - 10 * SCALE), s["code"], fill=TEXT_MAIN, font=font_cell, anchor="lm")
        draw.text((col_xs[0] + 8 * SCALE, cy + 10 * SCALE), s["name"], fill=TEXT_SEC, font=font_cell_name, anchor="lm")
        draw.text((col_xs[1] + col_ws[1] // 2, cy), f"{s['hold']}股", fill=TEXT_MAIN, font=font_cell, anchor="mm")
        draw.text((col_xs[2] + col_ws[2] // 2, cy), f"{s['cost']:.2f}", fill=TEXT_MAIN, font=font_cell, anchor="mm")
        draw.text((col_xs[3] + col_ws[3] // 2, cy), f"{s['price']:.2f}", fill=TEXT_MAIN, font=font_cell_bold, anchor="mm")
        draw.text((col_xs[4] + col_ws[4] // 2, cy), f"{s['mv']:,.0f}元", fill=TEXT_MAIN, font=font_cell, anchor="mm")
        draw.text((col_xs[5] + col_ws[5] // 2, cy), f"{s1}{s['change_pct']:.2f}%", fill=c_chg, font=font_cell_bold, anchor="mm")
        draw.text((col_xs[6] + col_ws[6] // 2, cy), fmt_money(s["day_profit"]), fill=c_day, font=font_cell, anchor="mm")
        draw.text((col_xs[7] + col_ws[7] // 2, cy - 10 * SCALE), fmt_money(s["profit_amt"]), fill=c_pnl, font=font_cell_bold, anchor="mm")
        draw.text((col_xs[7] + col_ws[7] // 2, cy + 10 * SCALE), f"({s2}{s['profit_pct']:.2f}%)", fill=c_pnl, font=font_small, anchor="mm")

        y += ROW_H

    y += 8 * SCALE
    card_h = SUMMARY_H
    draw.rounded_rectangle([TBL_X, y, TBL_X + TBL_W, y + card_h], radius=10 * SCALE, fill=SUMMARY_BG, outline=SUMMARY_BORDER, width=2)

    cy_start = y + 18 * SCALE
    s_color = RED if total_profit > 0 else GREEN
    t_color = RED if today_profit > 0 else GREEN
    s_sign = "+" if total_profit > 0 else ""
    t_sign = "+" if today_profit > 0 else ""

    draw.text((TBL_X + 20 * SCALE, cy_start), "今日总盈亏", fill=TEXT_SEC, font=font_summary_label, anchor="lm")
    draw.text((W - PAD_R - 20 * SCALE, cy_start), f"{t_sign}{today_profit:,.0f}元", fill=t_color, font=font_summary_val, anchor="rm")
    cy_start += 26 * SCALE

    draw.text((TBL_X + 20 * SCALE, cy_start), "持仓总盈亏", fill=TEXT_SEC, font=font_summary_label, anchor="lm")
    pnl_full = f"{s_sign}{total_profit:,.0f}元 ({s_sign}{total_profit_pct:.2f}%)"
    draw.text((W - PAD_R - 20 * SCALE, cy_start), pnl_full, fill=s_color, font=font_summary_val_big, anchor="rm")
    cy_start += 26 * SCALE

    draw.text((TBL_X + 20 * SCALE, cy_start), "总市值 / 总成本", fill=TEXT_SEC, font=font_summary_label, anchor="lm")
    draw.text((W - PAD_R - 20 * SCALE, cy_start), f"{total_market_value:,.0f}元 / {total_cost:,.0f}元", fill=TEXT_MAIN, font=font_summary_val, anchor="rm")

    y += card_h + 6 * SCALE

    draw.text((W // 2, y), "Sina Finance", fill=(170, 175, 195), font=font_small, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    img_bytes = buf.read()
    img_b64 = base64.b64encode(img_bytes).decode()
    img_md5 = hashlib.md5(img_bytes).hexdigest()

    print(f"[OK] 图片生成: {W}x{H}, base64={len(img_b64)} bytes")

    try:
        img.save("report.png")
        print("[OK] 已保存 report.png")
    except Exception:
        pass

    return img_b64, img_md5


def push_to_wecom(img_b64, img_md5):
    """推送图片到企业微信"""
    webhook = WECOM_WEBHOOK
    if not webhook:
        print("[ERROR] 未配置企业微信 Webhook（设置环境变量 WECOM_WEBHOOK）")
        return False

    payload = {
        "msgtype": "image",
        "image": {
            "base64": img_b64,
            "md5": img_md5
        }
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("errcode") == 0:
                print("[OK] 企业微信推送成功")
                return True
            else:
                print(f"[FAIL] 推送失败: {result}")
                return False
    except Exception as e:
        print(f"[ERROR] 推送异常: {e}")
        return False


def main():
    print(f"=== A股持仓播报 ===")
    print(f"时间: {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")

    # 0. 加载持仓配置
    holdings = load_holdings()
    print(f"[INFO] 持仓加载成功，共 {len(holdings)} 只股票")

    # 1. 检查交易时段（非交易时段直接退出，不推送）
    is_trading, time_str = check_trading_hours()
    print(f"[INFO] 交易时段检查: {time_str}, is_trading={is_trading}")
    if not is_trading:
        print(f"[INFO] 非交易时段 ({time_str})，跳过推送")
        sys.exit(0)

    # 2. 查询行情
    print("[INFO] 正在查询行情...")
    stocks_data = fetch_stock_data(holdings)
    if not stocks_data:
        print("[ERROR] 行情查询全部失败，终止执行")
        sys.exit(1)

    for h in holdings:
        d = stocks_data.get(h["code"], {})
        print(f"  {h['name']}({h['code']}): 现价={d.get('current', 'N/A')}, 昨收={d.get('yesterday_close', 'N/A')}")

    # 3. 生成图片
    print("[INFO] 正在生成播报图片...")
    try:
        img_b64, img_md5 = generate_report_image(stocks_data, time_str, is_trading, holdings)
    except Exception as e:
        print(f"[ERROR] 图片生成失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 4. 推送
    print("[INFO] 正在推送到企业微信...")
    success = push_to_wecom(img_b64, img_md5)

    if success:
        print("=== 播报完成 ===")
    else:
        print("=== 播报失败 ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
