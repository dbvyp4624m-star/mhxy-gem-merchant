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
        if "xyq.cbg.163.com" in url and "query.py" in url:
            return t["targetId"]

    # 没有则创建新 tab
    result = cdp_request("/new", method="POST",
                         body="https://xyq.cbg.163.com/cgi-bin/query.py?act=search_role_equip")
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


def check_login(target_id):
    """检查是否已登录"""
    text = eval_js(target_id, "document.body.innerText?.slice(0, 500) || ''")
    if not text:
        return False
    return "退出" in text and "钱包余额" in text


def scrape_gem_page(target_id, page_num):
    """抓取当前页面的宝石数据"""
    code = '''(() => {
        const text = document.body.innerText;
        const lines = text.split("\\n");
        const results = [];
        let currentGem = null;
        let i = 0;
        while (i < lines.length) {
            const line = lines[i].trim();
            if (!line) { i++; continue; }
            const levelMatch = line.match(/^(\\d+)级$/);
            const priceMatch = line.match(/^￥([\\d.]+)$/);
            if (levelMatch && currentGem && i > 0) {
                const prevLine = lines[i-1].trim();
                results.push({ gem: prevLine, level: parseInt(levelMatch[1]) });
            }
            if (priceMatch && results.length > 0) {
                results[results.length-1].price = parseFloat(priceMatch[1]);
            }
            i++;
        }
        return JSON.stringify(results.filter(r => r.price));
    })()'''
    return eval_js(target_id, code)


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

        # 提取数据
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
                            price: parseFloat(priceMatch[1])
                        });
                    }
                }
                i++;
            }
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


def save_to_csv(all_results, date_str):
    """保存数据到 CSV"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, f"gem_prices_{date_str}.csv")

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["日期", "宝石", "等级", "价格(元)"])
        for gem_name, data in all_results:
            for item in data:
                writer.writerow([date_str, gem_name, item["level"], item["price"]])

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


def regenerate_dashboard(csv_path, date_str):
    """从 CSV 重新生成 dashboard.html"""
    import re
    from collections import defaultdict

    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

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

    dashboard_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    with open(dashboard_path, "r", encoding="utf-8") as f:
        html = f.read()

    old_start = html.find("const DATA = {")
    old_end = html.find("const GEM_EMOJI")
    new_html = html[:old_start] + f"const DATA = {js_data};\n\n" + html[old_end:]

    new_html = re.sub(r'<span class="date-tag">[^<]*</span>', f'<span class="date-tag">{date_str}</span>', new_html)
    new_html = re.sub(
        r'<div class="stat-value">[\d,]+</div>\s*<div class="stat-detail">再续前缘服务器</div>',
        f'<div class="stat-value">{total:,}</div>\n      <div class="stat-detail">再续前缘服务器</div>',
        new_html
    )
    new_html = re.sub(
        r'<div class="stat-value">[^<]+</div>\s*<div class="stat-detail">\d+ 条在售[^<]*</div>',
        f'<div class="stat-value">{max_market[0]}</div>\n      <div class="stat-detail">{max_market[1]["total"]} 条在售 · {max_market[1]["total"] / total * 100:.1f}%</div>',
        new_html
    )
    new_html = re.sub(
        r'<div class="stat-value">¥[\d,]+</div>\s*<div class="stat-detail">[^<]+·[^<]+级</div>',
        f'<div class="stat-value">¥{float(max_item["价格(元)"]):,}</div>\n      <div class="stat-detail">{max_item["宝石"]} · {max_item["等级"]}级</div>',
        new_html
    )
    new_html = re.sub(r'共 [\d,]+ 条在售', f'共 {data["星辉石"]["total"]} 条在售', new_html)

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

    # 3. 检查登录状态
    if not check_login(target_id):
        print("\n错误: 未登录藏宝阁! 请先在 Chrome 中登录 xyq.cbg.163.com")
        return 1

    print("登录状态: 已登录 ✓")

    # 4. 确保在道具搜索页面
    current_url = eval_js(target_id, "document.location.href")
    if "act=search_role_equip" not in str(current_url):
        print("导航到道具搜索页面...")
        eval_js(target_id, "")
        cdp_request(f"/navigate?target={target_id}", method="POST",
                    body="https://xyq.cbg.163.com/cgi-bin/query.py?act=search_role_equip")
        time.sleep(3)

    # 5. 逐个抓取宝石数据
    all_results = []
    for gem_value, gem_name in GEMS:
        try:
            data = scrape_gem(target_id, gem_value, gem_name)
            all_results.append((gem_name, data))
        except Exception as e:
            print(f"  抓取 {gem_name} 失败: {e}")
            all_results.append((gem_name, []))

    # 6. 保存数据
    save_to_csv(all_results, today)
    save_summary(all_results, today)
    regenerate_dashboard(os.path.join(OUTPUT_DIR, f"gem_prices_{today}.csv"), today)
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
