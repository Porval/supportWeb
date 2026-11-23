"""Read worksheet facts from the saved, filtered decode of this conversation's file."""
from collections import Counter
import re

COLUMN = re.compile(r'(?<![a-z])([a-z]{1,3})\s*列', re.I)
ROW_PREFIX = re.compile(r'前(\d{1,2}|两|二)行')
VALUE_INTENT = re.compile(r'不空|非空|空白|有值|为空|空的|全空|有没有值|有数据|有内容|没有值|没.*(?:日期|时间)|值丢失|具体值|单元格|时间区间|日期|时间段|哪些值|有哪些|写了什么')
DATE_LABEL = re.compile(r'具体时间|活动周期|活动时间|活动日期')
MAX_SPANS = 8


def cell_request(question):
    words = re.sub(r'\s+', '', question).lower()
    found = COLUMN.search(question)
    label = DATE_LABEL.search(words)
    if found and VALUE_INTENT.search(words):
        return {'column':found[1].upper()}
    if label and ('列' in words or '原表' in words or '原文件' in words):
        return {'header':label[0]}
    first = ROW_PREFIX.search(words)
    if first and ('表' in words or '文件' in words or '附件' in words or first[0] == words.rstrip('。？?')):
        n = int(first[1]) if first[1].isdigit() else 2
        if 1 <= n <= 20:
            return {'firstRows':n}
    return None


def workbook_for_query(attachments, memory, filename, question):
    current = [item for item in attachments if (item.get('document') or {}).get('kind') == 'xlsx']
    saved = [ref for ref in (memory.state.get('attachments',[]) if memory else [])
             if ref['filename'].lower().endswith('.xlsx')]
    active_names = list(dict.fromkeys(item['filename'] for item in current))
    pool = list(dict.fromkeys([*active_names, *(ref['filename'] for ref in saved)]))
    targets = [filename] if filename else [n for n in pool if n in question]
    if not targets:
        targets = active_names or pool
    if len(targets) != 1 or targets[0] not in pool:
        return None, '请指定要核对的已保存附件。' if pool else '当前会话没有可核对的 Excel 原件。'
    name, = targets
    active = {item['sha256']:item for item in current if item['filename']==name}
    if len(active)==1:
        return next(iter(active.values())), None
    if active:
        return None, '当前有多个同名附件，请先选择对应版本。'
    matches = [ref for ref in saved if ref['filename']==name]
    if len(matches)!=1:
        return None, '已保存多个同名附件，请先选择对应版本。'
    context, _ = memory.read_attachment(matches[0])
    if not context or 'document' not in context:
        return None, '已定位附件，但这份原件未成功解析。'
    return context, None


def read_sheet_cells(attachments, memory, question, query):
    source, error = workbook_for_query(attachments, memory, query.get('filename'), question)
    if error:
        return error
    document = source['document']
    sheets = {}
    for row in document['tableRows']:
        name = row['locator'].get('sheet')
        if name is not None:
            sheets.setdefault(name,[]).append(row)
    selected = query.get('sheet')
    if selected is None:
        named = [name for name in sheets if name in question]
        if not named and len(sheets)==1:
            named = list(sheets)
        if len(named)!=1:
            return '请指定要核对的工作表。可读取的表有 ' + '、'.join(sheets) if sheets else '这份附件没有可读取的工作表数据。'
        selected, = named
    if selected not in sheets:
        return '在这份附件的已解析范围内找不到指定工作表。'
    rows = sheets[selected]
    lines = [f'《{source["filename"]}》{selected}']
    if document.get('status') != 'parsed':
        lines.append('原件只完成了部分解析，以下限于已读取的单元格。')
    if 'firstRows' in query:
        limit = query['firstRows']
        for row in rows:
            if row['locator']['row'] > limit:
                continue
            cells = [c['locator']['cell'] + ' ' + ('（公式待核对）' if c['sourceType']=='formula' else c['text'][:160])
                     for c in row['cells'] if c['text'].strip() or c['sourceType']=='formula']
            lines.append('、'.join(cells[:30]) + ('（其余列省略）' if len(cells)>30 else ''))
        if len(rows) and all(row['locator']['row'] > limit for row in rows):
            lines.append(f'前 {limit} 行没有已读到的单元格。')
        return '\n'.join(lines)

    column = query.get('column')
    headers = [head for head in source.get('sheetHeaders',[]) if head['sheet']==selected]
    if 'header' in query:
        cols = {col for head in headers for col,text in head['cells'].items() if text==query['header']}
        if len(cols)!=1:
            return '\n'.join(lines + ['无法唯一定位这个表头，请指定列字母。'])
        column, = cols
    header_rows = {h['row']:h['cells'][column] for h in headers if column in h['cells']}
    counts = Counter()
    runs = []
    formulas = 0
    total_runs = 0
    last_row = last_value = None
    for row in rows:
        n = row['locator']['row']
        if n in header_rows:
            continue
        for cell in row['cells']:
            if cell['column']!=column:
                continue
            if cell['sourceType']=='formula':
                formulas += 1
                continue
            value = cell['text'].strip()
            if not value:
                continue
            counts[value] += 1
            if value!=last_value or last_row!=n-1:
                total_runs += 1
                if total_runs<=MAX_SPANS:
                    runs.append([n,n,value])
            elif total_runs<=MAX_SPANS:
                runs[-1][1]=n
            last_row,last_value=n,value
    if header_rows:
        n,label = next(iter(header_rows.items()))
        lines.append(f'{column}{n} 是「{label}」。')
    lines.append(f'{column} 列已读到 {sum(counts.values())} 个非空值' + ('（已扣除表头）。' if header_rows else '（含未识别的表头，如有）。'))
    for first,last,value in runs:
        address = f'{column}{first}' + (f':{column}{last}' if last!=first else '')
        lines.append(f'{address}　{value[:160]}')
    if total_runs>len(runs):
        lines.append(f'已列出前 {len(runs)} 段，共 {total_runs} 段。')
    if formulas:
        lines.append(f'另有 {formulas} 个公式单元格，值待核对。')
    return '\n'.join(lines)
