#!/bin/bash
# 全量抓取脚本 - 通过 CDP Proxy 逐宝石抓取
# 用法: bash scrape_all.sh

TARGET="07AC185D670533F7A99FB8477991874A"
PROXY="http://localhost:3456"
DATA_DIR="/Users/donghongliang/梦幻西游宝石商人/data"
TODAY=$(date +%Y-%m-%d)

GEMS_JSON='[
  {"v":"4002","n":"太阳石"},
  {"v":"4003","n":"月亮石"},
  {"v":"4004","n":"光芒石"},
  {"v":"4010","n":"黑宝石"},
  {"v":"4011","n":"红玛瑙"},
  {"v":"4012","n":"舍利子"},
  {"v":"4244","n":"星辉石"},
  {"v":"4249","n":"翡翠石"}
]'

echo "=== 梦幻西游宝石商人 - 全量抓取 ==="
echo "日期: $TODAY"
echo "目标: 8种宝石 (排除神秘石)"
echo ""

ALL_CSV="$DATA_DIR/gem_prices_${TODAY}.csv"
echo "日期,宝石,等级,价格(元)" > "$ALL_CSV"
TOTAL_ALL=0

# 用 Python 解析 JSON 并循环
echo "$GEMS_JSON" | python3 -c "
import json, sys, time, subprocess, os

gems = json.load(sys.stdin)
target = '$TARGET'
proxy = '$PROXY'
csv_path = '$ALL_CSV'
total_all = 0

def eval_js(code):
    import urllib.request
    url = f'{proxy}/eval?target={target}'
    data = code.encode()
    req = urllib.request.Request(url, data=data, method='POST')
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        return result.get('value')
    except Exception as e:
        print(f'  Eval error: {e}')
        return None

for gem in gems:
    name = gem['n']
    value = gem['v']
    print(f'\n### {name} (value={value}) ###')
    
    # Select gem and search
    code = f'''
    (() => {{
        const r = [...document.querySelectorAll(\"input[name=equip_kind]\")]
            .find(r => r.value === \"search_stone\");
        if (r) {{ r.checked = true; r.dispatchEvent(new Event(\"change\", {{bubbles: true}})); }}
        document.getElementById(\"s_stone_type\").value = \"{value}\";
        document.getElementById(\"s_stone_level\").value = \"\";
        search_equip(1);
        return \"ok\";
    }})()'''
    result = eval_js(code)
    if not result:
        print(f'  FAILED to start search')
        continue
    
    time.sleep(3)
    
    # Get total pages
    code2 = '''(() => { const m = document.body.innerText.match(/共(\\d+)页/); return m ? m[1] : \"1\"; })()'''
    total_pages = int(eval_js(code2) or '1')
    print(f'  总页数: {total_pages}')
    
    gem_count = 0
    for page in range(1, total_pages + 1):
        if page > 1:
            eval_js(f'goto({page});')
            time.sleep(2)
        
        # Extract data
        code3 = f'''
        (() => {{
            const text = document.body.innerText;
            const lines = text.split(\"\\n\");
            const results = [];
            let i = 0;
            while (i < lines.length) {{
                if (lines[i].trim() === \"{name}\") {{
                    const nl = lines[i+1] ? lines[i+1].trim() : \"\";
                    const lm = nl.match(/^(\\d+)级$/);
                    let pl = lines[i+2] ? lines[i+2].trim() : \"\";
                    if (!pl.includes(\"￥\")) pl = lines[i+3] ? lines[i+3].trim() : \"\";
                    const pm2 = pl.match(/^￥([\\d.]+)$/);
                    if (lm && pm2) results.push({{l:parseInt(lm[1]), p:parseFloat(pm2[1])}});
                }}
                i++;
            }}
            return JSON.stringify(results);
        }})()'''
        
        data_json = eval_js(code3)
        if data_json:
            try:
                items = json.loads(data_json)
                for item in items:
                    with open(csv_path, 'a', encoding='utf-8') as f:
                        f.write(f\"{os.environ.get('TODAY', '$TODAY')},{name},{item['l']},{item['p']}\\n\")
                    gem_count += 1
                    total_all += 1
            except:
                pass
        
        if total_pages > 1:
            print(f'  页 {page}/{total_pages}: {gem_count} 条累计', end='\\r')
    
    print(f'  {name}: {gem_count} 条完成')

print(f'\n=== 总计: {total_all} 条记录 ===')
print(f'CSV: {csv_path}')
"
