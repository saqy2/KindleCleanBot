"""Shared ad detection patterns — single source of truth for scanner and cleaner.

Each pattern has:
  re       — compiled regex for precision verification
  triggers — keyword list for Aho-Corasick fast scanning
  label    — human-readable Chinese label
  level    — confidence level (L1=almost certainly ad, L2=contextual, L3=common in novels)
"""

import re

AD_PATTERNS = {
    "http_links": {
        "re": re.compile(r"https?://[^\s）\)】]+"),
        "triggers": ["http://", "https://"],
        "label": "HTTP链接",
        "level": "L1",
    },
    "qq_groups": {
        "re": re.compile(r"(?:QQ群|qq群|QQ裙|qq裙|企鹅群|扣扣群|蔻蔻群|q群|Q群)\s*[：:：]?\s*\d{4,12}"),
        "triggers": ["QQ群", "qq群", "QQ裙", "qq裙", "企鹅群", "扣扣群", "蔻蔻群", "q群:", "q群：", "Q群:", "Q群："],
        "label": "QQ群号",
        "level": "L1",
    },
    "email": {
        "re": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
        "triggers": ["@gmail", "@qq.", "@163.", "@126.", "@sina.", "@sohu.", "@hotmail", "@outlook", "@foxmail"],
        "label": "邮箱",
        "level": "L1",
    },
    "wechat": {
        "re": re.compile(r"(?:微信[：:：]\s*[a-zA-Z0-9_-]{5,20}|公众号[：:：]\s*\S{2,20}|加微信\S{2,20}|威信[：:：])"),
        "triggers": ["微信", "公众号", "加微信", "威信:", "威信："],
        "label": "微信/公众号",
        "level": "L2",
    },
    "group_invite": {
        "re": re.compile(r"(?:欢迎加入|加群|入群|进群|群号|书友群|读者群|粉丝群|交流群)"),
        "triggers": ["欢迎加入", "加群", "入群", "进群", "群号", "书友群", "读者群", "粉丝群", "交流群"],
        "label": "群引导",
        "level": "L2",
    },
    "promo": {
        "re": re.compile(r"(?:网址|链接|福利|更新提醒|订阅提醒|关注公众号|扫码|购买|付费|VIP.{0,5}群|分享到|转发)"),
        "triggers": ["网址", "链接", "福利", "更新提醒", "订阅提醒", "关注公众号", "扫码", "购买", "付费", "VIP群", "分享到", "转发"],
        "label": "推广用语",
        "level": "L2",
    },
    "begging": {
        "re": re.compile(r"(?:求订阅|求收藏|求票|求月票|求打赏|求推荐|求鲜花|求评价|求追读|各种求)"),
        "triggers": ["求订阅", "求收藏", "求票", "求月票", "求打赏", "求推荐", "求鲜花", "求评价", "求追读", "各种求"],
        "label": "求票/求订阅",
        "level": "L3",
    },
}
