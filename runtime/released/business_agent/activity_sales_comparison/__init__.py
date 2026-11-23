"""Versioned, read-only activity sales comparison capability."""
from .comparison import UnsupportedScope, compare_sales
from .goods_report import GoodsReportSnapshot
from .history import sales_history
from .online_source import DataAgentGoodsReport, source_from_env

__all__ = ["compare_sales", "sales_history", "GoodsReportSnapshot", "UnsupportedScope",
           "DataAgentGoodsReport", "source_from_env"]
