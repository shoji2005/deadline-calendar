"""今の締切カレンダー（job-commit.com）と企業名鑑（https://kigyo-meikan.jp/deadline）の
締切データを統合して、LP用とサンクスページ用の締切カレンダーHTMLを生成する。

使い方:
    py -3 update_deadline.py                 # output/ に生成
    py -3 update_deadline.py --out パス       # LP用の出力先を指定（サンクス用は同じフォルダ）

※ 企業名鑑 編集部の掲載許可に基づいて利用。出典表記は消さないこと。
※ 出力を EXISTING_URL と同じ場所に上書きしないこと（自分の出力を読み込んで二重に数えてしまうため）。
"""
import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

EXISTING_URL = "https://job-commit.com/marketing/shukatsu-ichiba/internship_autumn_winter.html"  # 全件版
FULL_NAME = "internship_autumn_winter_full.html"  # サンクスページ用の出力ファイル名
SOURCE_URL = "https://kigyo-meikan.jp/deadline"
SOURCE_NAME = "企業名鑑"
USER_AGENT = "Mozilla/5.0 (compatible; LP-deadline-updater/1.0)"

JST = timezone(timedelta(hours=9))
WEEKDAYS = "月火水木金土日"

TOP_COUNT = 5        # 通常表示する件数
BLUR_COUNT = 3       # ぼかし表示する件数
PREVIEW_COUNT = 150  # 登録ボタン下のプレビュー表の件数
URGENT_DAYS = 3      # 「締切まであとN日以内」の基準
MAX_DAYS = 300       # これより先の締切は、過ぎた日付が来年扱いされたものとみなして載せない
MIN_MEIKAN = 100     # 企業名鑑の取得件数がこれ未満なら失敗とみなす
MIN_EXISTING = 10    # 既存データの取得件数がこれ未満なら失敗とみなす

# 既存データはプログラム名、企業名鑑は企業概要が入る
SECOND_COL = "プログラム内容・企業概要"

# 企業名鑑の業界名 → 既存HTMLの data-industry の値
INDUSTRY_MAP = {"IT・通信": "IT"}

BADGE_BASE = ("display:inline-block;font-size:11px;font-weight:bold;line-height:1.5;"
              "padding:1px 6px;margin-right:6px;border-radius:4px;white-space:nowrap;"
              "vertical-align:middle;")
BLUR_STYLE = "filter:blur(5px);user-select:none;"
CAPTION_STYLE = ("position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;"
                 "clip:rect(0,0,0,0);white-space:nowrap;border:0;")


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as res:
        return res.read().decode("utf-8")


def fetch_meikan():
    page = get(SOURCE_URL)
    # Next.js のページ内データ（エスケープされたJSON）から企業レコードを抜き出す
    pattern = re.compile(r'\{\\"company\\":.*?\\"deadlineISO\\":\\"[0-9-]+\\"\}')
    records = {}
    for m in pattern.finditer(page):
        # 外側のJS文字列リテラルとしてデコード → 中身のJSONを読む
        rec = json.loads(json.loads('"' + m.group(0) + '"'))
        records[(rec["company"], rec["deadlineISO"])] = {  # 同じデータが複数回出るので重複除去
            "company": rec["company"],
            "text": rec.get("summary") or "",
            "iso": rec["deadlineISO"],
            "industry": INDUSTRY_MAP.get(rec["industry"], rec["industry"] or ""),
        }
    return list(records.values())


def fetch_existing():
    """既存の全件版HTMLから行データを読む"""
    page = get(EXISTING_URL)
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日更新", page)
    base = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))  # data-days の基準日

    row_re = re.compile(
        r'<tr data-industry="([^"]*)" data-days="(-?\d+)"[^>]*>\s*'
        r'<td data-label="企業名"><strong>(.*?)</strong></td>\s*'
        r'<td data-label="[^"]*">(.*?)</td>', re.S)
    records = {}
    for ind, days, company, text in row_re.findall(page):
        rec = {
            "company": html.unescape(company),
            "text": html.unescape(re.sub(r"<[^>]+>", "", text)),
            "iso": (base + timedelta(days=int(days))).isoformat(),
            "industry": html.unescape(ind),
        }
        records[(rec["company"], rec["text"], rec["iso"])] = rec  # 完全に同じ行の重複を除去
    return list(records.values())


def norm(name: str) -> str:
    """重複判定用に企業名をそろえる（括弧書き・株式会社・空白を除く）"""
    name = re.sub(r"[（(].*?[）)]", "", name)
    name = name.replace("株式会社", "").replace("（株）", "")
    return re.sub(r"[\s　]", "", name).lower()


def merge(existing, meikan, today: date):
    seen = {(norm(r["company"]), r["iso"]) for r in existing}
    added = [r for r in meikan if (norm(r["company"]), r["iso"]) not in seen]
    # 締切を過ぎたものと、MAX_DAYSより先のもの（過ぎた日付が来年扱いされたもの）は載せない
    first, last = today.isoformat(), (today + timedelta(days=MAX_DAYS)).isoformat()
    rows = sorted((r for r in existing + added if first <= r["iso"] <= last),
                  key=lambda r: (r["iso"], r["company"]))
    return rows, len(added)


def date_text(d: date) -> str:
    return f"{d.month}月{d.day}日({WEEKDAYS[d.weekday()]})"


def deadline_cell(d: date, days: int) -> str:
    if days == 0:
        badge = f'<span class="dl-badge today" style="{BADGE_BASE}background:#e53935;color:#fff;">本日締切</span>'
    elif days <= URGENT_DAYS:
        badge = f'<span class="dl-badge soon" style="{BADGE_BASE}background:#fb8c00;color:#fff;">あと{days}日</span>'
    else:
        badge = ""
    return f'{badge}<span class="deadline-emphasis" style="white-space:nowrap;">{date_text(d)}</span>'


def row(rec, today: date) -> str:
    d = date.fromisoformat(rec["iso"])
    days = (d - today).days
    attrs = f'data-industry="{html.escape(rec["industry"])}" data-days="{days}"'
    if days <= URGENT_DAYS:
        attrs += ' class="row-urgent" style="background:#fff5f5;"'
    return (f'<tr {attrs}>\n'
            f'            <td data-label="企業名"><strong>{html.escape(rec["company"])}</strong></td>\n'
            f'            <td data-label="{SECOND_COL}">{html.escape(rec["text"])}</td>\n'
            f'            <td data-label="エントリー締切">{deadline_cell(d, days)}</td>\n'
            f'        </tr>')


def blurred_row(rec) -> str:
    return (f'<tr class="blurred-row">'
            f'<td data-label="企業名"><span class="blur" style="{BLUR_STYLE}">{html.escape(rec["company"])}</span></td>'
            f'<td data-label="{SECOND_COL}"><span class="blur" style="{BLUR_STYLE}">{html.escape(rec["text"])}</span></td>'
            f'<td data-label="エントリー締切"><span style="white-space:nowrap;">{date_text(date.fromisoformat(rec["iso"]))}</span></td></tr>')


def thead() -> str:
    return (f'<thead><tr><th scope="col">企業名</th><th scope="col">{SECOND_COL}</th>'
            f'<th scope="col">エントリー締切</th></tr></thead>')


def build_html(rows, total, today: date) -> str:
    top = rows[:TOP_COUNT]
    blur = rows[TOP_COUNT:TOP_COUNT + BLUR_COUNT]
    rest = rows[TOP_COUNT:]
    rest_total = total - len(top)
    urgent_limit = (today + timedelta(days=URGENT_DAYS)).isoformat()
    urgent_rest = sum(1 for r in rest if r["iso"] <= urgent_limit)
    updated = f"{today.year}年{today.month}月{today.day}日"

    parts = [
        '<table>\n        '
        f'<caption style="{CAPTION_STYLE}">秋冬インターン締切一覧：締切が近い{len(top)}社（{updated}更新）</caption>'
        f'{thead()}\n        <tbody>' + "".join(row(r, today) for r in top) + '</tbody>\n    </table>',
        '<table class="blur-teaser" style="opacity:.85;"><tbody>' + "".join(blurred_row(r) for r in blur) + '</tbody></table>',
        f'<p style="text-align: center; margin-top: 30px; font-size: 1.1em; color: #3a5671; font-weight: bold;">※この他に{rest_total}件の締切情報があります</p>',
    ]
    if urgent_rest:
        parts.append(f'\n    <p style="text-align:center;margin-top:8px;font-size:1.05em;font-weight:bold;color:#d9534f;">'
                     f'このうち締切まであと{URGENT_DAYS}日以内が{urgent_rest}件あります</p>')
    parts += [
        '\n    <div class="register-prompt-section"><div class="white-block">\n'
        f'        <h3>締切間近の企業をすべて見る｜あと{rest_total}件</h3>\n'
        '        <p>志望業界の締切も、まだ間に合う企業も全部チェック。登録はカンタン1分。</p>\n'
        '        <a href="#" id="promptRegisterLink" class="button">締切情報をすべて見る（無料）</a>\n'
        '    </div></div>',
        f'<table class="prev_table" style="pointer-events:none;">\n        {thead()}\n        <tbody>'
        + "".join(row(r, today) for r in rest[:PREVIEW_COUNT]) + '</tbody>\n    </table>',
        f'\n    <p style="text-align:right;margin-top:8px;font-size:10px;color:#888;">'
        f'出典の一部：<a href="{SOURCE_URL}" target="_blank" rel="noopener">{SOURCE_NAME}</a>（{updated}取得）</p>',
    ]
    return "".join(parts)


def build_full_html(rows, today: date) -> str:
    """サンクスページ用：全件を1つの表で表示"""
    updated = f"{today.year}年{today.month}月{today.day}日"
    return ('<table>\n        '
            f'<caption style="{CAPTION_STYLE}">秋冬インターン締切一覧｜{updated}更新・{len(rows)}件（締切日順）</caption>'
            f'{thead()}\n        <tbody>' + "".join(row(r, today) for r in rows) + '</tbody>\n    </table>'
            f'\n    <p style="text-align:right;margin-top:8px;font-size:10px;color:#888;">'
            f'出典の一部：<a href="{SOURCE_URL}" target="_blank" rel="noopener">{SOURCE_NAME}</a>（{updated}取得）</p>')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path(__file__).parent / "output" / "internship_autumn_winter_merged.html"),
                        help="LP用の出力先。サンクスページ用の全件版は同じフォルダに FULL_NAME で出力する")
    args = parser.parse_args()

    # どちらかの取得に失敗したら、古いファイルを壊さないよう出力せずに中止する
    existing = fetch_existing()
    if len(existing) < MIN_EXISTING:
        sys.exit(f"既存データの取得件数が少なすぎます（{len(existing)}件）。中止しました。")
    meikan = fetch_meikan()
    if len(meikan) < MIN_MEIKAN:
        sys.exit(f"企業名鑑の取得件数が少なすぎます（{len(meikan)}件）。サイトの構造が変わった可能性があるため中止しました。")

    today = datetime.now(JST).date()
    rows, added = merge(existing, meikan, today)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(rows, len(rows), today), encoding="utf-8")
    full = out.parent / FULL_NAME
    full.write_text(build_full_html(rows, today), encoding="utf-8")
    print(f"既存 {len(existing)}件 + 企業名鑑の追加 {added}件 → 掲載 {len(rows)}件（過去・{MAX_DAYS}日超を除外）\n"
          f"  LP用: {out}\n  サンクス用: {full}")


if __name__ == "__main__":
    main()
