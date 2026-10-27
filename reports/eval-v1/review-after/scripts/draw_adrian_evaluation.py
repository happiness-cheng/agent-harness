"""生成评估/夜间模式架构SVG；可用现有Edge导出PNG，不下载工具。"""
import argparse
from html import escape
from pathlib import Path
import xml.etree.ElementTree as ET


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);parser.add_argument('--render',action='store_true');args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    colors={'blue':'#2563eb','green':'#16a34a','red':'#dc2626','purple':'#9333ea'}
    nodes=[(60,100,'持久任务队列','优先级 / 依赖 / 恢复 / 10h期限','#eff6ff'),
        (410,100,'预算与分级路由','程序 → DeepSeek → 受控Codex','#eff6ff'),
        (760,100,'审批 / 豆包人工接力','挂起该任务，继续独立任务','#fef2f2'),
        (60,300,'冻结 TaskSpec 与判据','Gold不进入模型输入，不随候选修改','#faf5ff'),
        (410,300,'原 Harness / Runtime','Session / 权限 / Context / Budget','#eff6ff'),
        (760,300,'独立 worktree / Codex','受限候选，不自动合并主分支','#faf5ff'),
        (60,500,'真实状态与执行证据','只读SQLite / Trace / Artifact / 送达','#f0fdf4'),
        (410,500,'独立 Verifier','Outcome / Process / Evidence / Semantic','#f0fdf4'),
        (760,500,'逐题与早晨报告','PASS / FAIL / UNKNOWN / 成本边界','#f0fdf4'),
        (410,700,'人类审阅关键决策','未知副作用 / 权限 / 是否采纳候选','#fff7ed')]
    svg=['<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="900" viewBox="0 0 1100 900">',
        '<style>text{font-family:"Microsoft YaHei",Arial,sans-serif;fill:#111827}</style>',
        '<rect width="1100" height="900" fill="white"/>','<defs>']
    for name,color in colors.items():svg.append(f'<marker id="arrow-{name}" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto"><path d="M0,0 L10,4 L0,8 Z" fill="{color}"/></marker>')
    svg+=['</defs>','<text x="60" y="48" font-size="26" font-weight="600">Adrian · 统一验证与夜间自主工作</text>',
        '<text x="60" y="74" font-size="14" fill="#6b7280">调度与验证连接现有Agent；不新增通用Agent Runtime</text>']
    paths=[('M340 160 H410','blue'),('M690 160 H760','red'),('M550 220 V300','blue'),
        ('M690 185 H725 V275 H900 V300','purple'),('M340 360 H410','blue'),
        ('M410 395 H365 V560 H340','green'),('M340 560 H410','green'),
        ('M200 420 V455 H385 V535 H410','purple'),('M900 420 V455 H715 V585 H690','purple'),
        ('M690 560 H760','green'),('M550 620 V700','blue')]
    for path,color in paths:svg.append(f'<path d="{path}" fill="none" stroke="{colors[color]}" stroke-width="2" marker-end="url(#arrow-{color})"/>')
    for x,y,title,subtitle,fill in nodes:
        svg+=[f'<rect x="{x}" y="{y}" width="280" height="120" rx="8" fill="{fill}" stroke="#d1d5db" stroke-width="1.5"/>',
            f'<text x="{x+18}" y="{y+43}" font-size="19" font-weight="600">{escape(title)}</text>',
            f'<text x="{x+18}" y="{y+76}" font-size="12">{escape(subtitle)}</text>']
    svg+=['<text x="60" y="860" font-size="13">蓝：执行　绿：证据与验证　紫：冻结标准/隔离候选　红：审批边界</text>','</svg>']
    path=args.out/'architecture.svg';path.write_text('\n'.join(svg),encoding='utf-8');ET.parse(path)
    if args.render:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport={'width':1100,'height':900},device_scale_factor=1.5)
            page.goto(path.resolve().as_uri());page.locator('svg').screenshot(path=str(args.out/'architecture.png'));browser.close()
    print(str(path.resolve()))


if __name__=='__main__':main()
