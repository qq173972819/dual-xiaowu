"""理财：推荐股排序与资金管理视图（纯计算，行情注入）。"""
from xiaowu_platform.finance import build_portfolio_view, rank_recommendations


def _q(code, name, price, pct):
    return {"code": code, "name": name, "price": price, "pct": pct, "chg": None, "market": 0}


def test_rank_recommendations_sorted_and_labeled():
    quotes = [
        _q("600519", "贵州茅台", 1500.0, 1.2),
        _q("300750", "宁德时代", 210.0, 6.1),
        _q("600036", "招商银行", 34.0, 0.4),
        _q("000001", "平安银行", 11.0, None),  # 停牌/无效，应被剔除
    ]
    items = rank_recommendations(quotes, top=3)
    assert [i["code"] for i in items] == ["300750", "600519", "600036"]
    assert items[0]["reason"] == "今日强势领涨"
    assert items[1]["reason"] == "温和上行"
    assert len(items) == 3


def test_portfolio_view_pnl_weights_and_advice():
    positions = [
        {"code": "600519", "name": "贵州茅台", "shares": 100, "cost": 1400.0},   # +7.1%
        {"code": "600036", "name": "招商银行", "shares": 1000, "cost": 40.0},    # -15% 边界下
        {"code": "000333", "name": "美的集团", "shares": 200, "cost": 60.0},
    ]
    quotes = [_q("600519", "贵州茅台", 1500.0, 1.2), _q("600036", "招商银行", 33.0, -2.0),
              _q("000333", "美的集团", 62.0, 1.0)]
    view = build_portfolio_view(positions, quotes, cash=5000.0)
    rows = {r["code"]: r for r in view["positions"]}
    # 盈亏：正负号代表涨跌；茅台盈利、招行亏损
    assert rows["600519"]["pnl"] == 10000.0
    assert rows["600036"]["pnl"] == -7000.0
    assert rows["600519"]["pnl_pct"] > 0 > rows["600036"]["pnl_pct"]
    # 权重合计 100
    assert abs(sum(r["weight"] for r in rows.values()) - 100.0) < 0.3
    # 调仓参考非空且带免责声明
    assert all(r["advice"] for r in rows.values())
    assert "不构成投资建议" in view["disclaimer"]
    assert view["summary"]["cash"] == 5000.0


def test_portfolio_view_stale_quote_marks_observe():
    view = build_portfolio_view([{"code": "000858", "name": "五粮液", "shares": 100, "cost": 130.0}], [])
    assert view["positions"][0]["price"] is None
    assert "观察" in view["positions"][0]["advice"]
    assert view["summary"]["market_value"] == 0.0
