# 梦幻西游宝石商人

藏宝阁「再续前缘」服务器宝石价格每日追踪系统。

## 目录结构

```
梦幻西游宝石商人/
├── scrape_gems.py       # 主爬虫脚本
├── data/                # 数据输出目录
│   ├── gem_prices_YYYY-MM-DD.csv   # 每日明细
│   └── summary_YYYY-MM-DD.txt      # 每日汇总
└── README.md
```

## 快速开始

### 1. 环境准备

```bash
# 确保 CDP Proxy 运行中
node ~/.claude/skills/web-access/scripts/cdp-proxy.mjs &

# Chrome 中登录藏宝阁
open https://xyq.cbg.163.com/cgi-bin/show_login.py?act=show_login
```

### 2. 运行爬虫

```bash
python3 /Users/donghongliang/梦幻西游宝石商人/scrape_gems.py
```

### 3. 通过 Claude Code Skill

直接说「运行宝石商人」或「爬取宝石价格」即可触发。

## 抓取范围

| 宝石 | CBG Code | 
|------|----------|
| 太阳石 | 4002 |
| 月亮石 | 4003 |
| 光芒石 | 4004 |
| 黑宝石 | 4010 |
| 红玛瑙 | 4011 |
| 舍利子 | 4012 |
| 星辉石 | 4244 |
| 翡翠石 | 4249 |

> 排除：神秘石 (4005)

## 定时任务

- **Claude Code Cron**: 每天 8:57 AM（7天有效期，需续期）
- **手动运行**: `python3 scrape_gems.py`

## 数据格式

### CSV 列
`日期, 宝石, 等级, 价格(元)`

### 汇总 TXT
按宝石/等级统计最低价、最高价、均价、在售数量
