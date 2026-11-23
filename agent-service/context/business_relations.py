"""Frozen operator-approved business semantics shared by intake and chat prompts."""
from pathlib import Path

VERSION = 'activity-business-relations/1.0'
DOCUMENT = Path(__file__).with_name('activity-business-relations-v1.md')


def business_policy():
    return (VERSION + '\n' + DOCUMENT.read_text(encoding='utf-8') +
            '\n按以上业务口径识别和回答。本节的示例用于说明关系，具体提报的事实取自该次材料。'
            '现有接口字段 type 表示活动发起方，沿用 brand、platform、jiatan_sourcing。'
            '对外说明活动类型时使用商品折扣或满减活动，具体玩法沿用 activityMethod 已有枚举。'
            '多种补贴用于汇总展示，各券档保留各自的出资方、金额或比例与适用商品。'
            '材料不足时说明待核对。识别回执继续遵循现有字段、枚举及证据格式。')
