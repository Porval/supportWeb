"""Remove format-only rows from an oversized XLSX reading copy."""
import io
from pathlib import PurePosixPath
import re
from xml.parsers import expat
import zipfile

# XLSX can store a styled row for every row in the Excel grid.
# This extra allowance applies only to preprocessing, before the normal parser budget.
MAX_PREPROCESS_BYTES = 64 * 1024 * 1024
WORKSHEET = re.compile(r'xl/worksheets/[A-Za-z0-9_.-]+\.xml')
SHEET_NAMESPACES = {
    'http://schemas.openxmlformats.org/spreadsheetml/2006/main}',
    'http://purl.oclc.org/ooxml/spreadsheetml/main}',
}
ROW_END = re.compile(rb'</(?:[^\s:>]+:)?row\s*>')
ROW_FORMAT = {'r', 's', 'spans', 'customFormat', 'ht', 'customHeight', 'hidden',
              'thickTop', 'thickBot', 'outlineLevel', 'collapsed', 'ph'}
CELL_FORMAT = {'r', 's', 't'}


class WorksheetLimit(ValueError):
    pass


def expanded_over_limit(entries, entry_limit, total_limit):
    return (sum(entry.file_size for entry in entries) > total_limit
            or any(entry.file_size > entry_limit for entry in entries))


def compact_worksheets(archive, entry_limit, total_limit):
    """Return smaller worksheet parts, leaving the original archive untouched."""
    entries = archive.infolist()
    if not expanded_over_limit(entries, entry_limit, total_limit):
        return {}
    if sum(entry.file_size for entry in entries) > MAX_PREPROCESS_BYTES:
        raise WorksheetLimit()
    if len(entries) > 200 or len({entry.filename for entry in entries}) != len(entries):
        raise ValueError('Invalid ZIP entries')

    worksheets = []
    other_size = 0
    for entry in entries:
        name = entry.filename
        if (entry.flag_bits & 1 or name.startswith('/') or '\\' in name
                or '..' in PurePosixPath(name).parts or re.match(r'^[A-Za-z]:', name)
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
                or re.search(r'vbaProject|embeddings/|activeX/', name, re.I)
                or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
            raise ValueError('Unsafe ZIP entry')
        if WORKSHEET.fullmatch(name):
            worksheets.append(entry)
        else:
            other_size += entry.file_size
            if entry.file_size > entry_limit:
                raise WorksheetLimit()

    remaining = total_limit - other_size
    replacements = {}
    for entry in worksheets:
        if remaining < 0:
            raise WorksheetLimit()
        clean = _compact_rows(archive.read(entry), min(entry_limit, remaining))
        replacements[entry.filename] = clean
        remaining -= len(clean)
    if remaining < 0:
        raise WorksheetLimit()
    return replacements


def _compact_rows(data, limit):
    # Slice the original XML to keep prefixes, other worksheet elements, and
    # the attributes, values and coordinates of every retained row.
    if b'\x00' in data:
        raise ValueError('Unsupported worksheet encoding')
    parser = expat.ParserCreate(namespace_separator='}')
    compacted = io.BytesIO()
    cursor = depth = 0
    row_start = None
    sheet_data = row_name = cell_name = None
    in_sheet_data = found_sheet_data = False

    def write_through(end):
        if compacted.tell() + end - cursor > limit:
            raise WorksheetLimit()
        if end > cursor:
            compacted.write(memoryview(data)[cursor:end])

    def start(name, attributes):
        nonlocal depth, sheet_data, row_name, cell_name, row_start, in_sheet_data, found_sheet_data
        depth += 1
        if depth == 1:
            namespace, _, element = name.rpartition('}')
            namespace += '}'
            if element != 'worksheet' or namespace not in SHEET_NAMESPACES:
                raise ValueError('Invalid worksheet root')
            sheet_data, row_name, cell_name = (namespace + key for key in ('sheetData', 'row', 'c'))
        elif depth == 2 and name == sheet_data:
            if found_sheet_data:
                raise ValueError('Duplicate worksheet data')
            in_sheet_data = found_sheet_data = True
        elif in_sheet_data and depth == 3 and name == row_name:
            if 'r' in attributes and ROW_FORMAT.issuperset(attributes):
                row_start = parser.CurrentByteIndex
        elif row_start is not None and (depth != 4 or name != cell_name
                or 'r' not in attributes or not CELL_FORMAT.issuperset(attributes)
                or attributes.get('t', 'n') != 'n'):
            row_start = None

    def end(name):
        nonlocal depth, cursor, row_start, in_sheet_data
        if row_start is not None and depth == 3:
            offset = parser.CurrentByteIndex
            closing = ROW_END.match(data, offset)
            write_through(row_start)
            cursor = closing.end() if closing else offset
            row_start = None
        if depth == 2:
            in_sheet_data = False
        depth -= 1

    def characters(value):
        nonlocal row_start
        if row_start is not None and value.strip():
            row_start = None

    def reject_dtd(*_):
        raise ValueError('Unsafe worksheet declaration')

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.StartDoctypeDeclHandler = reject_dtd
    try:
        parser.Parse(data, True)
    except expat.ExpatError as error:
        raise ValueError('Invalid worksheet XML') from error
    if not found_sheet_data:
        raise ValueError('Missing worksheet data')
    write_through(len(data))
    return compacted.getvalue()
