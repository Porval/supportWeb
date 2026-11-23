"""Validate source-backed amounts before a submitter adopts an Agent result."""
from collections import defaultdict
from decimal import Decimal
import re
from unicodedata import normalize

HEADERS = {
    "系统活动价": "price",
    "拟申请补贴": "subsidy",
    "补贴后单价": "net",
    "商品名称": "name",
}
AMOUNT = re.compile(r"\d{1,12}(?:\.\d{1,2})?")
AMOUNT_FIELDS = ("price", "subsidy", "net")
REQUIRED = set(HEADERS.values())
UNIT_SUFFIX = re.compile(r"(?:[（(]元[）)])$")
CENT = Decimal("0.01")


def header_label(text):
    return UNIT_SUFFIX.sub("", normalize("NFKC", text).splitlines()[0].strip()) if text.strip() else ""


def price_conflicts(documents):
    for document in documents:
        if document["status"] != "parsed":
            continue
        columns_by_table = {}
        for row in document["tableRows"]:
            locator = row["locator"]
            table = (locator.get("sheet"), locator.get("table"))
            headings = defaultdict(list)
            for cell in row["cells"]:
                key = HEADERS.get(header_label(cell["text"]))
                if key:
                    headings[key].append(cell["column"])
            if len(headings) >= 2:
                columns_by_table[table] = {key: values[0] for key, values in headings.items()
                                           if len(values) == 1} if REQUIRED <= headings.keys() else {}
                continue
            columns = columns_by_table.get(table, {})
            if not REQUIRED <= columns.keys():
                continue
            cells = {cell["column"]: cell for cell in row["cells"]}
            values = {key: cells.get(column, {}).get("text", "").strip() for key, column in columns.items()}
            if not values["name"] or not all(AMOUNT.fullmatch(values[key]) for key in AMOUNT_FIELDS):
                continue
            price, subsidy, reported = (Decimal(values[key]) for key in AMOUNT_FIELDS)
            calculated = (price - subsidy).quantize(CENT)
            if calculated == reported:
                continue
            name = values["name"][:100]
            yield {
                "code": "SUBSIDY_PRICE_CONFLICT_SUBMITTER_REVIEW",
                "message": (f"{locator.get('sheet') or '表格'}第 {locator['row']} 行 {name} 的价格待提报人确认。"
                            f"原表系统活动价 {price:.2f} 元、拟申请补贴 {subsidy:.2f} 元、"
                            f"补贴后单价 {reported:.2f} 元，按前两项复算为 {calculated:.2f} 元。"
                            "请提报人确认应更正的字段后补充。"),
                "locator": cells[columns["net"]]["locator"],
            }
