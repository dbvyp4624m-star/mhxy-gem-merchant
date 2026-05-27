#!/usr/bin/env python3
"""梦幻西游宝石商人 - 藏宝阁宝石价格爬虫
每日爬取所有宝石（除神秘石）在再续前缘服务器的价格

依赖: CDP Proxy 运行中 (node cdp-proxy.mjs)
      Chrome 浏览器已登录藏宝阁
"""

import json
import time
import csv
import os
import re
import sys
import subprocess
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError

CDP_PROXY = "http://localhost:3456"
SEARCH_URL = "https://xyq.cbg.163.com/cgi-bin/query.py?act=search_role_equip"
COLLECT_URL = "https://xyq.cbg.163.com/cgi-bin/userinfo.py?act=collect_list"
MONEY_URL = "https://xyq.cbg.163.com/cgi-bin/query.py?act=query"

# 宝石配置: (value, name)
GEMS = [
    ("4002", "太阳石"),
    ("4003", "月亮石"),
    ("4004", "光芒石"),
    # ("4005", "神秘石"),  # 排除
    ("4010", "黑宝石"),
    ("4011", "红玛瑙"),
    ("4012", "舍利子"),
    ("4244", "星辉石"),
    ("4249", "翡翠石"),
]

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# 合成所需1级宝石数量 (来源: hechengguize.xlsx)
SYNTHESIS = {
    "_default": {
        1:1, 2:2, 3:4, 4:8, 5:16, 6:32, 7:64, 8:128, 9:256, 10:512, 11:1024,
        12:2100, 13:4456, 14:9680, 15:21716, 16:51012, 17:123740,
        18:312628, 19:821724, 20:2392444
    },
    "星辉石": {
        1:1, 2:3, 3:9, 4:27, 5:81, 6:243, 7:729, 8:2187, 9:6642, 10:20898, 11:69336
    }
}


def cdp_request(path, method="GET", body=None, timeout=30):
    """发送请求到 CDP Proxy"""
    url = f"{CDP_PROXY}{path}"
    data = body.encode() if body else None
    req = Request(url, data=data, method=method)
    try:
        resp = urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except URLError as e:
        print(f"  CDP 请求失败: {e}")
        return None


def find_or_create_tab():
    """找到已有 tab 或创建新 tab"""
    targets = cdp_request("/targets")
    if not targets:
        return None

    # 先找已存在的 xyq.cbg.163.com tab
    for t in targets:
        url = t.get("url", "")
        if "xyq.cbg.163.com" in url:
            return t["targetId"]

    # 没有则创建新 tab
    result = cdp_request("/new", method="POST",
                         body=SEARCH_URL)
    if result:
        return result.get("targetId")
    return None


def eval_js(target_id, code):
    """在浏览器中执行 JS 并返回结果"""
    result = cdp_request(f"/eval?target={target_id}", method="POST", body=code)
    if result and "value" in result:
        return result["value"]
    if result and "error" in result:
        print(f"  JS 错误: {result['error']}")
    return None


def eval_frame(target_id, frame_url_hint, code):
    """在匹配 URL 的 iframe 中执行 JS"""
    result = cdp_request(f"/evalFrame?target={target_id}&frameUrl={frame_url_hint}", method="POST", body=code)
    if result and "value" in result:
        return result["value"]
    if result and "error" in result:
        print(f"  Frame JS 错误: {result['error']}")
    return None


def check_login(target_id):
    """检查是否已登录"""
    text = eval_js(target_id, "document.body.innerText?.slice(0, 500) || ''")
    if not text:
        return False
    return "退出" in text and "钱包余额" in text


def check_captcha(target_id):
    """检测是否在安全验证页面（易盾验证码 / 手机验证）"""
    text = eval_js(target_id, "document.body.innerText?.slice(0, 300) || ''")
    if text and ("安全验证" in str(text) or "点击完成验证" in str(text)):
        return "yidun"
    necaptcha = eval_js(target_id, "!!document.getElementById('NECaptchaSafeWindow')")
    if necaptcha:
        return "yidun"
    url = eval_js(target_id, "document.location.href") or ""
    if "show_mbauth" in url:
        return "mbauth"
    return None


def do_login(target_id, email=None, password=None, server_id="149", area_id="45"):
    """安全登录：依赖 Chrome 自动填充，不直接注入凭证（防触发验证码）
    仅在 cookie 完全过期时使用，需用户已在 Chrome 中保存过密码。"""
    import time as _time

    # 0. 前置检查：是否已在验证码页面
    if check_captcha(target_id):
        print("  ⚠ 当前在验证码页面，请先在 Chrome 中手动完成验证")
        return False

    # 1. 导航到登录页面（带服务器参数）
    login_url = (
        f"https://xyq.cbg.163.com/cgi-bin/show_login.py"
        f"?act=show_login&area_id={area_id}&area_name=%E8%BF%BD%E5%BF%86"
        f"&server_id={server_id}&server_name=%E5%86%8D%E7%BB%AD%E5%89%8D%E7%BC%98"
    )
    print("  导航到登录页面...")
    cdp_request(f"/navigate?target={target_id}", method="POST", body=login_url)
    _time.sleep(3)

    if check_captcha(target_id):
        print("  ⚠ 登录触发了验证码，请手动完成")
        return False

    if check_login(target_id):
        print("  已登录，跳过登录流程")
        return True

    # 2. 点击"邮箱登录"tab，等待 Chrome 自动填充
    print("  选择邮箱登录（依赖 Chrome 自动填充）...")
    eval_js(target_id, 'document.getElementById("tabBtn2").click(); "done"')
    _time.sleep(2)

    # 检查 iframe 中是否已自动填充
    email_filled = eval_frame(target_id, "dl.reg.163.com",
        'document.querySelector(\'input[name="email"]\')?.value || ""')
    if email_filled and len(str(email_filled)) > 3:
        print(f"  Chrome 已自动填充邮箱: {str(email_filled)[:3]}***")
    else:
        print("  Chrome 未自动填充，请在 Chrome 中手动登录一次以保存密码")
        print("  或确保已在 Chrome 密码管理器中保存 cbg 登录凭证")
        return False

    # 3. 检查登录按钮是否已启用（自动填充后应自动启用）
    btn_disabled = eval_frame(target_id, "dl.reg.163.com",
        'document.getElementById("dologin")?.classList.contains("btndisabled")')
    if btn_disabled:
        print("  登录按钮未启用，可能密码未填充")
        return False

    # 4. 点击登录按钮（仅点击，不注入任何值）
    print("  提交登录...")
    eval_frame(target_id, "dl.reg.163.com",
        'document.getElementById("dologin").click(); "clicked"')
    _time.sleep(5)

    if check_captcha(target_id):
        print("  ⚠ 登录触发了验证码，请手动完成")
        return False

    # 5. 等待角色选择页面
    for attempt in range(10):
        url = eval_js(target_id, "document.location.href") or ""
        if "show_role_select_page" in url:
            break
        if check_login(target_id):
            print("  登录成功（无角色选择页）")
            return True
        _time.sleep(2)
    else:
        print("  警告: 角色选择页未出现，可能已直接登录")
        if check_login(target_id):
            return True

    # 6. 选择第一个角色
    print("  选择角色...")
    first_role_id = eval_js(target_id, '''(() => {
        const panel = document.getElementById('role_list_panel');
        if (!panel) return null;
        const firstLi = panel.querySelector('li[id^="role_el_"]');
        if (!firstLi) return null;
        const img = firstLi.querySelector('img[id^="icon_"]');
        return img ? img.id.replace('icon_', '') : null;
    })()''')

    if first_role_id:
        eval_js(target_id, f'document.getElementById("icon_{first_role_id}").click(); "done"')
        _time.sleep(1)
        nickname = eval_js(target_id,
            'document.getElementById("select_role_nickname")?.innerText?.trim() || "?"')
        print(f"  角色: {nickname}")

    # 7. 点击"进入"
    eval_js(target_id, '''(() => {
        const all = document.querySelectorAll('a');
        for (const a of all) {
            if ((a.innerText || '').trim().replace(/\\s+/g, '') === '进入') {
                a.click(); return 'clicked';
            }
        }
    })()''')
    _time.sleep(5)

    if check_captcha(target_id):
        print("  ⚠ 登录后触发了验证码")
        return False

    return check_login(target_id)


def wait_for_selector(target_id, selector, timeout=5):
    """轮询直到选择器匹配到至少一个元素，替代固定 time.sleep"""
    interval = 0.2
    elapsed = 0.0
    while elapsed < timeout:
        try:
            count = eval_js(target_id, f"document.querySelectorAll('{selector}').length")
            if count and int(count) > 0:
                return True
        except:
            pass
        time.sleep(interval)
        elapsed += interval
    return False


def scrape_gem_fast(target_id, gem_value, gem_name, max_level=20):
    """快速抓取：按等级遍历，DOM 提取 + 收藏合并一趟完成"""
    print(f"\n{'='*50}")
    print(f"  快速抓取: {gem_name} (value={gem_value})")
    print(f"{'='*50}")

    all_data = []
    empty_streak = 0
    total_collected = 0

    for lv in range(5, max_level + 1):
        # 导航到按等级筛选 + 价格升序 URL
        search_url = (
            f"https://xyq.cbg.163.com/cgi-bin/query.py?act=search_stone"
            f"&server_id=149&areaid=45"
            f"&s_type={gem_value}&equip_level={lv}"
            f"&query_order=price+ASC&page=1"
        )
        nav_ok = cdp_request(f"/navigate?target={target_id}", method="POST", body=search_url)

        if not wait_for_selector(target_id, "#soldList tr", timeout=5):
            empty_streak += 1
            if empty_streak >= 3:
                break
            continue
        empty_streak = 0

        # 获取总页数
        total_pages = 1
        page_match = eval_js(target_id,
            '''(() => { const m = document.body.innerText.match(/共(\\d+)页/); return m ? m[1] : null; })()''')
        if page_match:
            total_pages = int(page_match)

        # 逐页提取（后续页用完整 URL）
        level_items = []
        for page in range(1, total_pages + 1):
            if page > 1:
                page_url = (
                    f"https://xyq.cbg.163.com/cgi-bin/query.py?act=search_stone"
                    f"&server_id=149&areaid=45"
                    f"&s_type={gem_value}&equip_level={lv}"
                    f"&query_order=price+ASC&page={page}"
                )
                cdp_request(f"/navigate?target={target_id}", method="POST", body=page_url)
                if not wait_for_selector(target_id, "#soldList tr", timeout=3):
                    continue

            page_data_json = eval_js(target_id, f'''(() => {{
                const rows = document.querySelectorAll("#soldList tr");
                const items = [];
                rows.forEach(row => {{
                    const cells = row.querySelectorAll("td");
                    if (cells.length < 6) return;
                    const nameText = (cells[1]?.innerText || "").trim();
                    if (!nameText.includes("{gem_name}")) return;
                    const lvMatch = nameText.match(/(\\d+)级/);
                    if (!lvMatch) return;
                    const priceText = (cells[3]?.innerText || cells[2]?.innerText || "").trim();
                    const priceMatch = priceText.match(/[￥¥]([\\d.]+)/);
                    if (!priceMatch) return;
                    const collectSpan = row.querySelector("span.equipListCollect");
                    const orderSn = collectSpan?.getAttribute("data-game_ordersn") || "";
                    items.push({{
                        level: parseInt(lvMatch[1]),
                        price: parseFloat(priceMatch[1]),
                        order_sn: orderSn
                    }});
                }});
                return JSON.stringify(items);
            }})()''')

            if page_data_json:
                try:
                    items = json.loads(page_data_json)
                    level_items.extend(items)
                except json.JSONDecodeError:
                    pass

        all_data.extend(level_items)

        # 8-12 级：收藏最低价前 3 个
        if 8 <= lv <= 12 and level_items:
            lv_sorted = sorted(
                [d for d in level_items if d.get("order_sn")],
                key=lambda x: x["price"]
            )
            seen = set()
            collected = 0
            for item in lv_sorted:
                sn = item["order_sn"]
                if sn in seen:
                    continue
                seen.add(sn)
                code = (f"fetch('/cgi-bin/userinfo.py?act=ajax_add_collect"
                        f"&order_sn={sn}').then(r=>r.json())"
                        f".then(j=>j.status===1?'ok':'fail')")
                ok = eval_js(target_id, code)
                if ok == "ok":
                    collected += 1
                    total_collected += 1
                time.sleep(0.25)
                if collected >= 3:
                    break

        pages_str = f" {total_pages}页" if total_pages > 1 else ""
        fav_str = f" 收藏{min(3, sum(1 for d in level_items if d.get('order_sn')))}个" if 8 <= lv <= 12 else ""
        print(f"  等级{lv}: {len(level_items)}条{pages_str}{fav_str}")

    if total_collected > 0:
        print(f"  {gem_name}: 收藏 8-12 级共 {total_collected} 个 listing")
    print(f"  总计 {gem_name}: {len(all_data)} 条")
    return all_data


def run_self_check(all_results, rate_info=None):
    """采集后自检：条数合理性、异常价格、数据污染"""
    print(f"\n{'='*50}")
    print(f"  数据质量自检")
    print(f"{'='*50}")
    issues = []

    for gem_name, data in all_results:
        if not data:
            issues.append(f"⚠ {gem_name}: 0条数据!")
            continue

        by_level = {}
        for d in data:
            lv = d["level"]
            by_level[lv] = by_level.get(lv, []) + [d["price"]]

        levels = sorted(by_level.keys())

        # 检查等级连续性
        if len(levels) >= 2:
            for i in range(len(levels) - 1):
                if levels[i + 1] - levels[i] > 1:
                    issues.append(f"⚠ {gem_name}: 等级不连续 {levels[i]}→{levels[i+1]}")

        # 检查异常价格 (等级6不应是¥10)
        for lv, prices in by_level.items():
            avg_p = sum(prices) / len(prices)
            min_p = min(prices)
            # 6级以上宝石价格不应低于 ¥10.00
            if lv >= 7 and min_p < 11:
                issues.append(f"⚠ {gem_name} {lv}级: 最低价过低 ¥{min_p:.2f}")
            # 跨宝石污染检测：红玛瑙/黑宝石/舍利子6级均价不会 <15
            if gem_name in ("红玛瑙", "黑宝石", "舍利子") and lv >= 6 and avg_p < 15:
                issues.append(f"⚠ {gem_name} {lv}级: 均价异常低 ¥{avg_p:.2f} (疑似数据污染)")

        print(f"  {gem_name}: {len(data)}条, {len(levels)}个等级 ({levels[0]}-{levels[-1]}级)")

    if rate_info:
        print(f"  汇率: 1RMB={rate_info.get('mh_per_rmb', '?')}MH")

    if issues:
        print(f"\n  发现问题 {len(issues)} 个:")
        for issue in issues:
            print(f"    {issue}")
    else:
        print(f"  自检通过 ✓")

    return len(issues) == 0


def scrape_gem(target_id, gem_value, gem_name):
    """抓取单个宝石的所有页面数据"""
    print(f"\n{'='*50}")
    print(f"  抓取: {gem_name} (value={gem_value})")
    print(f"{'='*50}")

    # 选中宝石分类并搜索
    code = f'''(() => {{
        const stoneRadio = [...document.querySelectorAll("input[name=equip_kind]")]
            .find(r => r.value === "search_stone");
        if (!stoneRadio) return "no stone radio";
        stoneRadio.checked = true;
        stoneRadio.dispatchEvent(new Event("change", {{bubbles: true}}));
        document.getElementById("s_stone_type").value = "{gem_value}";
        document.getElementById("s_stone_level").value = "";
        search_equip(1);
        return "searching...";
    }})()'''

    result = eval_js(target_id, code)
    if not result:
        print("  搜索提交失败")
        return []

    time.sleep(3)

    # 获取总页数
    page_info = eval_js(target_id, '''(() => {
        const text = document.body.innerText;
        const m = text.match(/< (\\d+)\\//) || text.match(/(\\d+)\\/(\\d+)/);
        return m ? m[0] : "?";
    })()''')
    print(f"  分页信息: {page_info}")

    total_pages = 1
    page_match = eval_js(target_id, '''(() => {
        const text = document.body.innerText;
        const m = text.match(/共(\\d+)页/);
        return m ? m[1] : null;
    })()''')
    if page_match:
        total_pages = int(page_match)

    all_data = []

    for page in range(1, total_pages + 1):
        print(f"  抓取第 {page}/{total_pages} 页...", end=" ")

        if page > 1:
            eval_js(target_id, f"goto({page});")
            time.sleep(2)

        # 提取数据（含 order_sn 用于收藏）
        # 提取数据（innerText 解析 + DOM 提取 order_sn）
        page_data_json = eval_js(target_id, '''(() => {
            const text = document.body.innerText;
            const lines = text.split("\\n");
            const results = [];
            let i = 0;
            while (i < lines.length) {
                if (lines[i].trim() === "''' + gem_name + '''") {
                    const nextLine = lines[i+1] ? lines[i+1].trim() : "";
                    const levelMatch = nextLine.match(/^(\\d+)级$/);
                    let priceLine = lines[i+2] ? lines[i+2].trim() : "";
                    if (!priceLine.includes("￥")) {
                        priceLine = lines[i+3] ? lines[i+3].trim() : "";
                    }
                    const priceMatch = priceLine.match(/^￥([\\d.]+)$/);
                    if (levelMatch && priceMatch) {
                        results.push({
                            level: parseInt(levelMatch[1]),
                            price: parseFloat(priceMatch[1]),
                            order_sn: ""
                        });
                    }
                }
                i++;
            }
            // 补充 order_sn：从 DOM 提取
            const rows = document.querySelectorAll("#soldList tr");
            let ri = 0;
            rows.forEach(row => {
                const cells = row.querySelectorAll("td");
                if (cells.length < 6) return;
                const nameText = (cells[1]?.innerText || "").trim();
                if (!nameText.includes("''' + gem_name + '''")) return;
                const lvMatch = nameText.match(/(\\d+)级/);
                if (!lvMatch) return;
                const lv = parseInt(lvMatch[1]);
                const collectSpan = row.querySelector("span.equipListCollect");
                const orderSn = collectSpan?.getAttribute("data-game_ordersn") || "";
                if (orderSn && ri < results.length) {
                    // 匹配 level
                    for (let j = ri; j < results.length; j++) {
                        if (results[j].level === lv && !results[j].order_sn) {
                            results[j].order_sn = orderSn;
                            break;
                        }
                    }
                }
                ri++;
            });
            return JSON.stringify(results);
        })()''')

        if page_data_json:
            try:
                page_data = json.loads(page_data_json) if isinstance(page_data_json, str) else page_data_json
                all_data.extend(page_data)
                print(f"获取 {len(page_data)} 条")
            except json.JSONDecodeError:
                print(f"JSON 解析失败: {page_data_json[:100]}")

    print(f"  总计 {gem_name}: {len(all_data)} 条")
    return all_data


def scrape_money_rate(target_id):
    """抓取梦幻币最低单价，计算 RMB/MH 汇率"""
    print(f"\n{'='*50}")
    print(f"  抓取: 梦幻币汇率")
    print(f"{'='*50}")

    cdp_request(f"/navigate?target={target_id}", method="POST",
                body=MONEY_URL)
    time.sleep(2)
    eval_js(target_id, "search_by_kind(23, 2);")
    time.sleep(2)

    parse_js = '''(() => {
        const lines = document.body.innerText.split("\\n");
        const results = [];
        for (const line of lines) {
            const parts = line.trim().split("\\t");
            const unitIdx = parts.findIndex(p => p.includes("元/万两"));
            if (unitIdx === -1) continue;
            const priceIdx = parts.findIndex(p => p.startsWith("￥"));
            if (priceIdx === -1) continue;
            const unit = parseFloat(parts[unitIdx]);
            const price = parseFloat(parts[priceIdx].replace("￥", ""));
            if (!isNaN(unit) && !isNaN(price)) results.push({unit: unit, price: price});
        }
        return JSON.stringify(results);
    })()'''

    all_units = []
    for page in range(1, 4):
        if page > 1:
            eval_js(target_id, f"goto({page});")
            time.sleep(2)
        data_json = eval_js(target_id, parse_js)
        if data_json:
            try:
                items = json.loads(data_json) if isinstance(data_json, str) else data_json
                units = [i["unit"] for i in items if i.get("unit")]
                all_units.extend(units)
                print(f"  第{page}页: {len(items)} 条, 单价范围 {min(units):.4f}-{max(units):.4f}")
            except json.JSONDecodeError:
                pass

    if not all_units:
        print("  梦幻币抓取失败")
        return None

    all_units.sort()
    lowest = all_units[:5]
    avg_unit = round(sum(lowest) / len(lowest), 4)
    effective = round(avg_unit * 0.93, 4)
    mh_per_rmb = round(10000 / effective)

    print(f"  最低5单均价: {avg_unit} 元/万两")
    print(f"  实际汇率(×0.93): {effective} 元/万两")
    print(f"  1 RMB = {mh_per_rmb} 梦幻币")

    return {"unit_price": avg_unit, "effective_rate": effective, "mh_per_rmb": mh_per_rmb}


def compute_suggested_buy(data, rate_info):
    """计算每种宝石的一级建议收购价(梦幻币)，含10%利润。
    优先使用最低的"健康"等级：该等级最低价 > ¥11 且与相邻等级
    价格比符合合成倍率(±12%)，说明定价自然、未被地板价扭曲。
    若无健康等级，回退到 l1_rmb 最低的等级。
    对所有等级计算收购价，标注薄利等级(cost >= 卖价×0.95)。
    """
    if not rate_info:
        return {}
    mh_per_rmb = rate_info["mh_per_rmb"]

    suggested = {}
    for gem_name, gem_data in data.items():
        synth = SYNTHESIS.get(gem_name, SYNTHESIS["_default"])
        expected_ratio = 3 if gem_name == "星辉石" else 2
        tolerance = 0.12

        # 收集所有 min > 11 的等级，按等级升序
        valid_levels = []
        for lv_str, lv_data in gem_data["levels"].items():
            lv = int(lv_str)
            if lv not in synth:
                continue
            if lv_data["min"] <= 11:
                continue
            l1_rmb = lv_data["min"] / synth[lv]
            valid_levels.append((lv, lv_data["min"], l1_rmb))
        valid_levels.sort()

        if not valid_levels:
            suggested[gem_name] = {"l1_mh": 0, "ref_level": 0, "levels": {}}
            continue

        # 找最低的"健康"等级：与相邻等级价格比符合合成倍率
        ref_level = None
        best_l1_rmb = None
        for i, (lv, min_p, l1_rmb) in enumerate(valid_levels):
            if i + 1 < len(valid_levels):
                next_lv, next_min, _ = valid_levels[i + 1]
                levels_gap = next_lv - lv
                expected = expected_ratio ** levels_gap
                actual = next_min / min_p
                if abs(actual - expected) / expected <= tolerance:
                    ref_level = lv
                    best_l1_rmb = l1_rmb
                    break  # 找到第一个健康等级即停止

        # 回退：无健康等级时用 l1_rmb 最低的等级
        if ref_level is None:
            best = min(valid_levels, key=lambda x: x[2])
            ref_level = best[0]
            best_l1_rmb = best[2]

        l1_mh = round(best_l1_rmb * mh_per_rmb * 0.9)  # 留10%利润
        levels_out = {}
        has_cbg = gem_data.get("levels", {})

        for lv in range(1, max(synth.keys()) + 1):
            if lv not in synth:
                continue
            buy_mh = round(l1_mh * synth[lv])
            lv_entry = {"buy_mh": buy_mh, "flag": False}
            lv_str = str(lv)
            if lv_str in has_cbg and has_cbg[lv_str]["min"] > 11:
                sell_price = has_cbg[lv_str]["min"]
                cost_rmb = buy_mh / mh_per_rmb
                if cost_rmb >= sell_price * 0.95:
                    lv_entry["flag"] = True
            levels_out[lv_str] = lv_entry

        suggested[gem_name] = {"l1_mh": l1_mh, "ref_level": ref_level, "levels": levels_out}
        print(f"  {gem_name}: 建议收购 {l1_mh} MH/级 (参考{ref_level}级, 已留10%利润)")

    return suggested


def build_suggested_buy_history():
    """从所有历史 CSV + 汇率数据重建每日 SUGGESTED_BUY，提取 l1_mh 趋势"""
    import glob
    from collections import defaultdict

    csv_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "gem_prices_*.csv")))
    rate_path = os.path.join(OUTPUT_DIR, "money_rate.csv")
    rates_by_date = {}
    if os.path.exists(rate_path):
        with open(rate_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rates_by_date[row["日期"]] = {
                    "unit_price": float(row["最低单价(元/万两)"]),
                    "effective_rate": float(row["实际汇率(×0.93)"]),
                    "mh_per_rmb": int(row["1RMB=MH"]),
                }

    gem_names = [name for _, name in GEMS]
    dates = []
    gem_series = defaultdict(lambda: [])

    for fpath in csv_files:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(fpath))
        if not date_match:
            continue
        date_str = date_match.group(1)
        rate_info = rates_by_date.get(date_str)
        if not rate_info:
            continue

        with open(fpath, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        gem_data = defaultdict(lambda: defaultdict(list))
        for r in rows:
            gem_data[r["宝石"]][int(r["等级"])].append(float(r["价格(元)"]))
        data = {}
        for name in gem_names:
            levels = gem_data.get(name, {})
            lv_info = {}
            for lv, prices in sorted(levels.items()):
                lv_info[str(lv)] = {
                    "min": round(min(prices), 2),
                    "max": round(max(prices), 2),
                    "avg": round(sum(prices) / len(prices), 2),
                    "count": len(prices),
                }
            data[name] = {"levels": lv_info}

        suggested = compute_suggested_buy(data, rate_info)
        dates.append(date_str)
        for name in gem_names:
            gem_series[name].append(suggested.get(name, {}).get("l1_mh", 0))

    return {"dates": dates, "gems": dict(gem_series)}


def check_transactions(target_id, today):
    """检查收藏列表中"买家取走"的 item，记录成交价并删除收藏"""
    transactions = []
    print(f"\n{'='*50}")
    print(f"  检查收藏列表成交状态...")
    print(f"{'='*50}")

    # 导航到收藏列表
    cdp_request(f"/navigate?target={target_id}", method="POST",
                body=COLLECT_URL)
    time.sleep(2)

    page = 1
    while True:
        # 解析当前页
        code = '''(() => {
            const rows = document.querySelectorAll('#soldList tr');
            const items = [];
            rows.forEach(row => {
                const cells = row.querySelectorAll('td');
                if (cells.length < 8) return;
                const nameText = (cells[1]?.innerText || '').trim();
                const lvMatch = nameText.match(/(\\d+)级/);
                const gemName = nameText.replace(/\\s*\\d+级.*/, '').trim();
                const statusText = (cells[4]?.innerText || '').trim();
                const priceText = (cells[3]?.innerText || '').trim();
                const priceMatch = priceText.match(/[￥¥]([\\d.]+)/);
                const delLink = cells[7]?.querySelector('a[href*=\"delete_collect\"]');
                const href = delLink?.getAttribute('href') || '';
                const snMatch = href.match(/order_sn=([\\d_]+)/);
                if (lvMatch && priceMatch && snMatch) {
                    items.push({
                        gem: gemName,
                        level: parseInt(lvMatch[1]),
                        price: parseFloat(priceMatch[1]),
                        status: statusText,
                        order_sn: snMatch[1]
                    });
                }
            });
            // 检查是否有下一页
            const hasNext = document.querySelector('.pages a[href*=\"page=' + ''' + str(page + 1) + ''' + '\"]') !== null;
            const totalPages = (() => { const m = document.body.innerText.match(/共(\\d+)页/); return m ? parseInt(m[1]) : 1; })();
            return JSON.stringify({items: items, hasNext: hasNext, totalPages: totalPages});
        })()'''
        result = eval_js(target_id, code)
        if not result:
            break
        try:
            data = json.loads(result)
        except:
            break

        page_items = data.get("items", [])
        total_pages = data.get("totalPages", 1)

        for item in page_items:
            status = item["status"]
            if "买家取走" in status:
                transactions.append(item)
                print(f"  成交: {item['gem']} {item['level']}级 ¥{item['price']}")
                # 删除已成交收藏
                del_code = f"fetch('/cgi-bin/userinfo.py?act=ajax_del_collect&order_sn={item['order_sn']}')"
                eval_js(target_id, del_code)
                time.sleep(0.3)
            elif "已失效" in status:
                del_code = f"fetch('/cgi-bin/userinfo.py?act=ajax_del_collect&order_sn={item['order_sn']}')"
                eval_js(target_id, del_code)
                time.sleep(0.3)

        if page >= total_pages:
            break
        page += 1
        eval_js(target_id, f"goto_page({page})")
        time.sleep(2)

    print(f"  共发现 {len(transactions)} 笔成交")
    return transactions


def favorite_top3(target_id, gem_value, gem_name):
    """对每种宝石 8-12 级，逐级筛选→价格从低到高排序→收藏前3个"""
    count = 0

    for lv in range(8, 13):
        # 搜索指定宝石+等级，加上价格升序参数
        search_url = (
            f"https://xyq.cbg.163.com/cgi-bin/query.py?act=search_stone"
            f"&server_id=149&areaid=45"
            f"&s_type={gem_value}&equip_level={lv}"
            f"&query_order=price+ASC&page=1"
        )
        cdp_request(f"/navigate?target={target_id}", method="POST", body=search_url)
        time.sleep(2)

        # 从当前页提取前3个未收藏的 order_sn
        result = eval_js(target_id, '''(() => {
            const spans = document.querySelectorAll("span.equipListCollect[data-game_ordersn]:not(.on)");
            const sns = [];
            for (const span of spans) {
                const sn = span.getAttribute("data-game_ordersn");
                if (sn) sns.push(sn);
                if (sns.length >= 3) break;
            }
            return JSON.stringify(sns);
        })()''')

        if result:
            try:
                sns = json.loads(result)
            except:
                sns = []
            for sn in sns:
                code = f"fetch('/cgi-bin/userinfo.py?act=ajax_add_collect&order_sn={sn}').then(r => r.json()).then(j => j.status === 1 ? 'ok' : 'fail')"
                ok = eval_js(target_id, code)
                if ok == "ok":
                    count += 1
                time.sleep(0.3)

    if count > 0:
        print(f"  {gem_name}: 收藏 8-12 级共 {count} 个 listing")
    return count


def build_transactions_history():
    """从 transactions.csv 构建历史成交数据，用于 dashboard 注入"""
    tx_path = os.path.join(OUTPUT_DIR, "transactions.csv")
    if not os.path.exists(tx_path):
        return None

    from collections import defaultdict
    gem_names = [name for _, name in GEMS]
    dates_set = set()
    # gem -> level -> date -> price
    gem_lv_date_price = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    with open(tx_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = row.get("成交日期", "")
            gem = row.get("宝石", "")
            level = row.get("等级", "")
            price = float(row.get("成交价(元)", 0))
            if date_str and gem and level:
                dates_set.add(date_str)
                gem_lv_date_price[gem][level][date_str].append(price)

    dates = sorted(dates_set)
    if len(dates) < 1:
        return None

    result = {"dates": dates, "gems": {}}
    for gem in gem_names:
        result["gems"][gem] = {}
        for lv in ["8", "9", "10", "11", "12"]:
            prices_by_date = []
            for d in dates:
                day_prices = gem_lv_date_price.get(gem, {}).get(lv, {}).get(d, [])
                if day_prices:
                    prices_by_date.append(round(min(day_prices), 2))  # 最低成交价
                else:
                    prices_by_date.append(None)
            result["gems"][gem][lv] = prices_by_date

    return result


def save_to_csv(all_results, date_str):
    """保存数据到 CSV"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, f"gem_prices_{date_str}.csv")

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["日期", "宝石", "等级", "价格(元)", "order_sn"])
        for gem_name, data in all_results:
            for item in data:
                writer.writerow([date_str, gem_name, item["level"], item["price"], item.get("order_sn", "")])

    print(f"\n数据已保存至: {filepath}")
    return filepath


def save_summary(all_results, date_str):
    """保存汇总统计"""
    filepath = os.path.join(OUTPUT_DIR, f"summary_{date_str}.txt")

    lines = []
    lines.append(f"梦幻西游藏宝阁 - 再续前缘 宝石价格日报")
    lines.append(f"抓取日期: {date_str}")
    lines.append(f"抓取时间: {datetime.now().strftime('%H:%M:%S')}")
    lines.append("=" * 60)

    for gem_name, data in all_results:
        if not data:
            lines.append(f"\n{gem_name}: 无在售记录")
            continue

        levels = {}
        for item in data:
            lv = item["level"]
            if lv not in levels:
                levels[lv] = []
            levels[lv].append(item["price"])

        lines.append(f"\n{gem_name} (共{len(data)}条):")
        lines.append(f"  {'等级':<6} {'数量':<6} {'最低价':<12} {'最高价':<12} {'均价':<12}")
        lines.append(f"  {'-'*48}")
        for lv in sorted(levels.keys()):
            prices = levels[lv]
            avg = sum(prices) / len(prices)
            lines.append(f"  {lv}级{'':<3} {len(prices):<6} ¥{min(prices):<10.2f} ¥{max(prices):<10.2f} ¥{avg:<10.2f}")

    lines.append("\n" + "=" * 60)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"汇总已保存至: {filepath}")


def build_history():
    """从所有历史 CSV 构建时间序列数据"""
    import glob
    from collections import defaultdict

    csv_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "gem_prices_*.csv")))
    if len(csv_files) < 2:
        return None

    gem_names = [name for _, name in GEMS]
    dates = []
    gem_series = defaultdict(lambda: defaultdict(list))

    for fpath in csv_files:
        fname = os.path.basename(fpath)
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
        if not date_match:
            continue
        date_str = date_match.group(1)
        dates.append(date_str)

        with open(fpath, encoding="utf-8") as f:
            frows = list(csv.DictReader(f))

        gems = defaultdict(lambda: defaultdict(list))
        for r in frows:
            gems[r["宝石"]][int(r["等级"])].append(float(r["价格(元)"]))

        for name in gem_names:
            levels = gems.get(name, {})
            lv5 = levels.get(5, [])
            all_prices = [p for ps in levels.values() for p in ps]
            gem_series[name]["lv5_avg"].append(round(sum(lv5) / len(lv5), 2) if lv5 else 0)
            gem_series[name]["lv5_min"].append(round(min(lv5), 2) if lv5 else 0)
            gem_series[name]["lv5_max"].append(round(max(lv5), 2) if lv5 else 0)
            gem_series[name]["total"].append(len(all_prices))

    return {"dates": dates, "gems": dict(gem_series)}


def build_level_history():
    """从所有历史 CSV 构建每级最低价时间序列。
    返回: {"dates": [...], "levels": {"5": {"太阳石": [10.0,...], ...}, "6": {...}}}
    某日某等级无数据时填 null，前端折线断点显示。"""
    import glob
    from collections import defaultdict

    csv_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "gem_prices_*.csv")))
    if len(csv_files) < 2:
        return None

    gem_names = [name for _, name in GEMS]
    dates = []
    # levels[level_str][gem_name] = [min_price_day1, min_price_day2, ...]
    levels = defaultdict(lambda: defaultdict(list))

    # 先收集所有出现过的等级
    all_levels = set()

    for fpath in csv_files:
        fname = os.path.basename(fpath)
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
        if not date_match:
            continue
        date_str = date_match.group(1)
        dates.append(date_str)

        with open(fpath, encoding="utf-8") as f:
            frows = list(csv.DictReader(f))

        # 当天 (gem, level) -> min_price
        day_min = defaultdict(lambda: {})
        for r in frows:
            gem = r["宝石"]
            lv = int(r["等级"])
            price = float(r["价格(元)"])
            if lv not in day_min[gem] or price < day_min[gem][lv]:
                day_min[gem][lv] = price
            all_levels.add(str(lv))

        for name in gem_names:
            gem_day = day_min.get(name, {})
            for lv_str in sorted(all_levels, key=int):
                lv = int(lv_str)
                levels[lv_str][name].append(round(gem_day[lv], 2) if lv in gem_day else None)

    # 按等级排序
    sorted_levels = {lv: dict(levels[lv]) for lv in sorted(levels.keys(), key=int)}
    return {"dates": dates, "levels": sorted_levels}


def regenerate_dashboard(csv_path, date_str, rate_info=None, suggested=None):
    """从 CSV 重新生成 dashboard.html"""
    import glob
    from collections import defaultdict

    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("  无数据，跳过仪表盘更新")
        return

    gems = defaultdict(lambda: defaultdict(list))
    for r in rows:
        gems[r["宝石"]][int(r["等级"])].append(float(r["价格(元)"]))

    data = {}
    for name, levels in gems.items():
        lv5_prices = levels.get(5, [])
        all_prices = [p for ps in levels.values() for p in ps]
        lv_data = {}
        for lv, prices in sorted(levels.items()):
            lv_data[str(lv)] = {
                "count": len(prices), "min": round(min(prices), 2),
                "max": round(max(prices), 2), "avg": round(sum(prices) / len(prices), 2),
            }
        data[name] = {
            "total": len(all_prices),
            "lv5_avg": round(sum(lv5_prices) / len(lv5_prices), 2) if lv5_prices else 0,
            "lv5_min": round(min(lv5_prices), 2) if lv5_prices else 0,
            "lv5_max": round(max(lv5_prices), 2) if lv5_prices else 0,
            "level_range": f"{min(levels.keys())}-{max(levels.keys())}",
            "max_price": max(all_prices),
            "levels": lv_data,
        }

    js_data = json.dumps(data, ensure_ascii=False, indent=2)
    total = len(rows)
    max_item = max(rows, key=lambda r: float(r["价格(元)"]))
    max_market = max(data.items(), key=lambda x: x[1]["total"])

    # 历史趋势数据
    history = build_history()
    js_history = json.dumps(history, ensure_ascii=False) if history else "{}"

    # 较昨日变化
    gem_names = [name for _, name in GEMS]
    dod_total = 0
    dod_total_str = "--"
    dod_max_str = "--"
    if history and len(history["dates"]) >= 2:
        yesterday_total = sum(history["gems"][n]["total"][-2] for n in gem_names)
        dod_total = total - yesterday_total
        dod_total_str = f"{'+' if dod_total >= 0 else ''}{dod_total}"
        yesterday_csv = sorted(glob.glob(os.path.join(OUTPUT_DIR, "gem_prices_*.csv")))[-2]
        with open(yesterday_csv, encoding="utf-8") as f:
            yrows = list(csv.DictReader(f))
        if yrows:
            ymax = max(float(r["价格(元)"]) for r in yrows)
            dod_max = float(max_item["价格(元)"]) - ymax
            dod_max_str = f"{'+' if dod_max >= 0 else ''}{dod_max:.0f}"
        else:
            dod_max_str = "--"

    dashboard_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    with open(dashboard_path, "r", encoding="utf-8") as f:
        html = f.read()

    old_start = html.find("const DATA = {")
    old_end = html.find("const GEM_EMOJI")
    new_html = html[:old_start] + f"const DATA = {js_data};\n\n" + html[old_end:]

    # 替换 HISTORY / DOD (支持重复运行)
    new_html = re.sub(r'const HISTORY = \{.*?\};', f'const HISTORY = {js_history};', new_html)
    dod_obj = {"total": dod_total_str, "max_price": dod_max_str}
    js_dod = json.dumps(dod_obj, ensure_ascii=False)
    new_html = re.sub(r'const DOD = \{.*?\};', f'const DOD = {js_dod};', new_html)

    # 替换 RATE / SYNTHESIS / SUGGESTED_BUY
    if rate_info:
        js_rate = json.dumps(rate_info, ensure_ascii=False)
        new_html = re.sub(r'const RATE = \{.*?\};', f'const RATE = {js_rate};', new_html)
        js_synth = json.dumps(SYNTHESIS, ensure_ascii=False)
        new_html = re.sub(r'const SYNTHESIS = \{.*?\};', f'const SYNTHESIS = {js_synth};', new_html)
        js_buy = json.dumps(suggested if suggested else {}, ensure_ascii=False)
        new_html = re.sub(r'const SUGGESTED_BUY = \{.*?\};', f'const SUGGESTED_BUY = {js_buy};', new_html)

    # 注入 SUGGESTED_BUY_HISTORY
    buy_history = build_suggested_buy_history()
    js_buy_hist = json.dumps(buy_history, ensure_ascii=False) if buy_history else "{}"
    new_html = re.sub(
        r'const SUGGESTED_BUY_HISTORY = \{.*?\};',
        f'const SUGGESTED_BUY_HISTORY = {js_buy_hist};',
        new_html,
    )

    # 注入 TRANSACTIONS
    tx_history = build_transactions_history()
    js_tx = json.dumps(tx_history, ensure_ascii=False) if tx_history else "{}"
    new_html = re.sub(
        r'const TRANSACTIONS = \{.*?\};',
        f'const TRANSACTIONS = {js_tx};',
        new_html,
    )

    # 注入 LEVEL_HISTORY
    level_history = build_level_history()
    js_level_hist = json.dumps(level_history, ensure_ascii=False) if level_history else "{}"
    new_html = re.sub(
        r'const LEVEL_HISTORY = \{.*?\};',
        f'const LEVEL_HISTORY = {js_level_hist};',
        new_html,
    )

    new_html = re.sub(r'<span class="date-tag">[^<]*</span>', f'<span class="date-tag">{date_str}</span>', new_html)
    starstone_total = data.get("星辉石", {}).get("total", 0)
    new_html = re.sub(r'共 [\d,]+ 条在售', f'共 {starstone_total} 条在售', new_html)

    with open(dashboard_path, "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"仪表盘已更新: {dashboard_path}")


def git_push(date_str):
    """推送更新到 GitHub Pages"""
    import subprocess
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run(["git", "-C", repo_dir, "add", "dashboard.html", "index.html"], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo_dir, "add", "data/"], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-m", f"每日更新: {date_str}"], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo_dir, "push", "origin", "main"], check=True, capture_output=True)
        print("已推送到 GitHub Pages")
    except subprocess.CalledProcessError as e:
        print(f"Git push 失败: {e.stderr.decode() if e.stderr else e}")


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"╔══════════════════════════════════════════════╗")
    print(f"║    梦幻西游宝石商人 - 每日价格爬取          ║")
    print(f"║    日期: {today}                             ║")
    print(f"║    服务器: 再续前缘                          ║")
    print(f"╚══════════════════════════════════════════════╝")

    # 1. 检查 CDP Proxy
    health = cdp_request("/health")
    if not health:
        print("\n错误: CDP Proxy 未运行!")
        print("请先启动: node ~/.claude/skills/web-access/scripts/cdp-proxy.mjs &")
        return 1

    # 2. 找到或创建 tab
    target_id = find_or_create_tab()
    if not target_id:
        print("\n错误: 无法创建浏览器 tab!")
        return 1

    print(f"\n使用 tab: {target_id}")

    # 3. 登录状态检查（依赖 Chrome 持久化 cookie，不触发自动登录）
    # 先检测验证码（若有则阻断）
    captcha_type = check_captcha(target_id)
    if captcha_type:
        print(f"\n⚠ 检测到安全验证页面 ({captcha_type})!")
        print("  请在 Chrome 中手动完成验证，完成后 cookie 自动持久化，无需重复操作。")
        return 1

    # 导航到首页让 cookie 生效
    cdp_request(f"/navigate?target={target_id}", method="POST",
                body="https://xyq.cbg.163.com/?server_id=149&areaid=45")
    time.sleep(2)

    captcha_type = check_captcha(target_id)
    if captcha_type:
        print(f"\n⚠ 检测到安全验证页面 ({captcha_type})!")
        print("  请在 Chrome 中手动完成验证后重试。")
        return 1

    if not check_login(target_id):
        # 再试一次：导航到搜索页
        cdp_request(f"/navigate?target={target_id}", method="POST", body=SEARCH_URL)
        time.sleep(3)
        captcha_type = check_captcha(target_id)
        if captcha_type:
            print(f"\n⚠ 检测到安全验证页面 ({captcha_type})!")
            print("  请在 Chrome 中手动完成验证后重试。")
            return 1
        if not check_login(target_id):
            print("\n未登录! 请在 Chrome 中手动登录一次 (cookie 持久化后无需重复):")
            print("  1. 打开 https://xyq.cbg.163.com")
            print("  2. 追忆 → 再续前缘 → 邮箱登录 → 选角色 902丨享受")
            return 1

    print("登录状态: 已登录 ✓")

    # 4. 确保在道具搜索页面
    current_url = eval_js(target_id, "document.location.href")
    if "act=search_role_equip" not in str(current_url):
        print("导航到道具搜索页面...")
        cdp_request(f"/navigate?target={target_id}", method="POST",
                    body=SEARCH_URL)
        wait_for_selector(target_id, "#soldList tr,#search_box,.qFilter", timeout=5)

    # 4.5 检查收藏列表中的成交
    transactions = check_transactions(target_id, today)
    if transactions:
        tx_path = os.path.join(OUTPUT_DIR, "transactions.csv")
        tx_exists = os.path.exists(tx_path)
        with open(tx_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not tx_exists:
                writer.writerow(["成交日期", "宝石", "等级", "成交价(元)", "order_sn"])
            for tx in transactions:
                writer.writerow([today, tx["gem"], tx["level"], tx["price"], tx["order_sn"]])
        print(f"  成交数据已保存: {len(transactions)} 笔")

    # 回到道具搜索页面
    cdp_request(f"/navigate?target={target_id}", method="POST",
                body=SEARCH_URL)
    wait_for_selector(target_id, "#soldList tr,#search_box,.qFilter", timeout=5)

    # 5. 逐个抓取宝石数据（快速模式：按等级遍历 + 收藏合并一趟完成）
    # 每 3 个宝石换新 tab，防止浏览器状态退化
    all_results = []
    for i, (gem_value, gem_name) in enumerate(GEMS):
        if i > 0 and i % 3 == 0:
            # 换新 tab
            new_tab = cdp_request("/new", method="POST", body=SEARCH_URL)
            if new_tab:
                target_id = new_tab.get("targetId", target_id)
                wait_for_selector(target_id, "#soldList tr,#search_box,.qFilter", timeout=5)
                print(f"\n  切换到新 tab: {target_id}")

        try:
            data = scrape_gem_fast(target_id, gem_value, gem_name)
            all_results.append((gem_name, data))
        except Exception as e:
            print(f"  抓取 {gem_name} 失败: {e}")
            # 失败时强制换 tab
            new_tab = cdp_request("/new", method="POST", body=SEARCH_URL)
            if new_tab:
                target_id = new_tab.get("targetId", target_id)
                wait_for_selector(target_id, "#soldList tr,#search_box,.qFilter", timeout=5)
            all_results.append((gem_name, []))

    # 6. 抓取梦幻币汇率
    rate_info = scrape_money_rate(target_id)

    # 6.5 数据自检
    if not run_self_check(all_results, rate_info):
        print("\n  ⚠ 自检未通过，请核查数据后重试")

    # 7. 保存宝石数据并计算建议收购价
    save_csv_path = os.path.join(OUTPUT_DIR, f"gem_prices_{today}.csv")
    save_to_csv(all_results, today)
    save_summary(all_results, today)

    suggested = None
    if rate_info:
        rate_csv = os.path.join(OUTPUT_DIR, "money_rate.csv")
        existing_rows = []
        if os.path.exists(rate_csv):
            with open(rate_csv, encoding="utf-8") as f:
                existing_rows = list(csv.reader(f))
        new_rows = [existing_rows[0]] if existing_rows and existing_rows[0][0] == "日期" else [["日期", "最低单价(元/万两)", "实际汇率(×0.93)", "1RMB=MH"]]
        for row in existing_rows[1:]:
            if row and row[0] != today:
                new_rows.append(row)
        new_rows.append([today, rate_info["unit_price"], rate_info["effective_rate"], rate_info["mh_per_rmb"]])
        with open(rate_csv, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(new_rows)
        gem_data = {}
        for gem_name, items in all_results:
            lv_map = {}
            for item in items:
                lv = str(item["level"])
                if lv not in lv_map:
                    lv_map[lv] = []
                lv_map[lv].append(item["price"])
            lv_data = {lv: {"min": min(ps), "max": max(ps)} for lv, ps in sorted(lv_map.items(), key=lambda x: int(x[0]))}
            gem_data[gem_name] = {"levels": lv_data}
        suggested = compute_suggested_buy(gem_data, rate_info)

    regenerate_dashboard(save_csv_path, today, rate_info, suggested)
    git_push(today)

    # 7. 输出总览
    print(f"\n{'='*60}")
    print(f"  抓取完成! 共 {len(GEMS)} 种宝石")
    total_items = sum(len(d) for _, d in all_results)
    print(f"  总记录数: {total_items}")
    print(f"{'='*60}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
