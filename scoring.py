import json
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# ===== 評分規則 =====
SCORE_RULES = {
    "foreign_consecutive_buy_5": 20,   # 外資連買5天
    "trust_consecutive_buy_5": 20,     # 投信連買5天
    "is_60d_high": 15,                 # 突破60日新高
    "above_ma60": 10,                  # 站上季線
    "volume_ratio_200": 20,            # 量增200%
    "margin_decrease": 10,             # 融資減少 (籌碼安定)
    "foreign_consecutive_buy_3": 10,   # 外資連買3天 (加分)
    "trust_consecutive_buy_3": 10,     # 投信連買3天 (加分)
    "above_ma20": 5,                   # 站上月線
    "dealer_net_positive": 5,          # 自營商買超
    "dual_institution_resonance": 15,  # 法人共振 (外資+投信同步連買)
}


@dataclass
class ScoreBreakdown:
    foreign_consecutive_buy_5: int = 0
    trust_consecutive_buy_5: int = 0
    is_60d_high: int = 0
    above_ma60: int = 0
    volume_ratio_200: int = 0
    margin_decrease: int = 0
    foreign_consecutive_buy_3: int = 0
    trust_consecutive_buy_3: int = 0
    above_ma20: int = 0
    dealer_net_positive: int = 0
    dual_institution_resonance: int = 0
    total: int = 0
    reasons: List[str] = None

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []


def calculate_score(data: Dict) -> Tuple[int, str]:
    """
    計算股票評分
    data: dict containing all indicators for a stock on a given date
    Returns: (total_score, json_breakdown)
    """
    breakdown = ScoreBreakdown()
    reasons = []

    # 外資連買5天 +20
    foreign_days = data.get("foreign_consecutive_buy", 0) or 0
    if foreign_days >= 5:
        breakdown.foreign_consecutive_buy_5 = SCORE_RULES["foreign_consecutive_buy_5"]
        reasons.append(f"外資連買{foreign_days}天 (+{breakdown.foreign_consecutive_buy_5})")
    elif foreign_days >= 3:
        breakdown.foreign_consecutive_buy_3 = SCORE_RULES["foreign_consecutive_buy_3"]
        reasons.append(f"外資連買{foreign_days}天 (+{breakdown.foreign_consecutive_buy_3})")

    # 投信連買5天 +20
    trust_days = data.get("trust_consecutive_buy", 0) or 0
    if trust_days >= 5:
        breakdown.trust_consecutive_buy_5 = SCORE_RULES["trust_consecutive_buy_5"]
        reasons.append(f"投信連買{trust_days}天 (+{breakdown.trust_consecutive_buy_5})")
    elif trust_days >= 3:
        breakdown.trust_consecutive_buy_3 = SCORE_RULES["trust_consecutive_buy_3"]
        reasons.append(f"投信連買{trust_days}天 (+{breakdown.trust_consecutive_buy_3})")

    # 突破60日新高 +15
    if data.get("is_60d_high", False):
        breakdown.is_60d_high = SCORE_RULES["is_60d_high"]
        reasons.append(f"創60日新高 (+{breakdown.is_60d_high})")

    # 站上季線 (60MA) +10
    close = data.get("close_price", 0) or 0
    ma60 = data.get("ma60", 0) or 0
    if close > 0 and ma60 > 0 and close > ma60:
        breakdown.above_ma60 = SCORE_RULES["above_ma60"]
        reasons.append(f"站上季線 (+{breakdown.above_ma60})")

    # 站上月線 (20MA) +5
    ma20 = data.get("ma20", 0) or 0
    if close > 0 and ma20 > 0 and close > ma20:
        breakdown.above_ma20 = SCORE_RULES["above_ma20"]
        reasons.append(f"站上月線 (+{breakdown.above_ma20})")

    # 量增200% +20 (量比 >= 2)
    volume_ratio = data.get("volume_ratio", 0) or 0
    if volume_ratio >= 2.0:
        breakdown.volume_ratio_200 = SCORE_RULES["volume_ratio_200"]
        reasons.append(f"量增{volume_ratio:.0%} (+{breakdown.volume_ratio_200})")

    # 融資減少 +10
    margin_change = data.get("margin_change", 0) or 0
    if margin_change < 0:
        breakdown.margin_decrease = SCORE_RULES["margin_decrease"]
        reasons.append(f"融資減少{abs(margin_change):.0f}張 (+{breakdown.margin_decrease})")

    # 自營商買超 +5
    dealer_net = data.get("dealer_net", 0) or 0
    if dealer_net > 0:
        breakdown.dealer_net_positive = SCORE_RULES["dealer_net_positive"]
        reasons.append(f"自營商買超{dealer_net:.0f}張 (+{breakdown.dealer_net_positive})")

    # 法人共振 +15 (外資與投信同步連買3天以上，雙重訊號強化)
    if foreign_days >= 3 and trust_days >= 3:
        breakdown.dual_institution_resonance = SCORE_RULES["dual_institution_resonance"]
        reasons.append(f"法人共振 (+{breakdown.dual_institution_resonance})")

    # 計算總分
    total = (
        breakdown.foreign_consecutive_buy_5 +
        breakdown.trust_consecutive_buy_5 +
        breakdown.is_60d_high +
        breakdown.above_ma60 +
        breakdown.volume_ratio_200 +
        breakdown.margin_decrease +
        breakdown.foreign_consecutive_buy_3 +
        breakdown.trust_consecutive_buy_3 +
        breakdown.above_ma20 +
        breakdown.dealer_net_positive +
        breakdown.dual_institution_resonance
    )
    breakdown.total = min(total, 100)  # 最高100分
    breakdown.reasons = reasons

    return breakdown.total, json.dumps(asdict(breakdown), ensure_ascii=False)


def calculate_moving_average(prices: List[float], period: int) -> Optional[float]:
    """計算移動平均"""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calculate_consecutive_buy(net_values: List[float]) -> int:
    """計算連買天數 (從最新開始往回數)"""
    if not net_values:
        return 0
    count = 0
    for v in reversed(net_values):
        if v > 0:
            count += 1
        else:
            break
    return count


def is_60day_high(close: float, historical_closes: List[float]) -> bool:
    """判斷是否創60日新高"""
    if len(historical_closes) < 60:
        return False
    return close >= max(historical_closes[-60:])
