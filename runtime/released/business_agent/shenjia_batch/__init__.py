"""Split explicitly identified Meituan Shen Jia activity workbooks."""
from .splitter import CONTRACT, BatchError, split_workbook

__all__ = ["CONTRACT", "BatchError", "split_workbook"]
