def pct(value):
    return f'{value:+.2f}%'


def price(value):
    return f'{value:.8g}'


def live_ranking_text(regime,trades,rejected,model_ready=True,top=3):
    lines=['🚀 LIVE OPPORTUNITY RANKING','',f'Market regime: {regime}','']
    if not model_ready:
        lines.extend(['MODEL NOT READY','NO TRADE'])
    elif not trades:
        lines.append('NO TRADE')
    for index,item in enumerate(trades[:top],1):
        lines.extend(['',f"#{index} {item['symbol'].replace('TRY','/TRY')}",f"Price: {price(item['price'])}",
            f"Opportunity Score: {item['opportunity_score']:.1f}",f"Momentum Score: {item['momentum_score']:.1f}",
            f"Predicted 60m: {pct(item['predicted_return_60m'])}",f"P(positive): {item['probability_positive_60m']:.0%}",
            f"P(+2% before -1.5%): {item['probability_tp_2_sl_1_5']:.0%}",
            f"Expected MFE: {pct(item['expected_mfe_60m'])}",f"Expected MAE: {pct(item['expected_mae_60m'])}",
            f"Gross expected return: {pct(item['gross_expected_return_pct'])}",
            f"Estimated round-trip cost: {pct(item['estimated_round_trip_cost_pct'])}",
            f"Net expectancy: {pct(item['net_expectancy_pct'])}",f"Confidence: {item['confidence']}",
            'Entry: MARKET',f"TP1: {price(item['tp1'])}",f"TP2: {price(item['tp2'])}",
            f"Stop: {price(item['stop'])}",f"Max Hold: {item['max_hold_minutes']}m"])
        if item.get('position_size_try') is not None:
            lines.append(f"Max position: {item['position_size_try']:.2f} TRY")
    if rejected:
        lines.extend(['','Closest opportunities:'])
        for index,item in enumerate(rejected[:3],1):
            reason=', '.join(item['rejection_reasons'][:2])
            lines.append(f"{index}. {item['symbol'].replace('TRY','/TRY')} — {item['opportunity_score']:.1f} — {reason}")
    return '\n'.join(lines)


def shadow_opportunity_text(item):
    why=', '.join(item.get('why_now') or ['model-ranked current feature alignment'])
    risks=', '.join(item.get('risks') or ['model uncertainty and market risk'])
    return '\n'.join(['🧪 SHADOW OPPORTUNITY','',f"COIN: {item['symbol'].replace('TRY','/TRY')}",
        f"PRICE: {price(item['price'])}",'',f"Opportunity: {item['opportunity_score']:.1f}/100",
        f"Momentum: {item['momentum_score']:.1f}/100",'',f"Predicted 60m: {pct(item['predicted_return_60m'])}",
        f"P(positive): {item['probability_positive_60m']:.0%}",
        f"P(TP before stop): {item['probability_tp_before_stop']:.0%}",
        f"Gross expected return: {pct(item['gross_expected_return_pct'])}",
        f"Estimated round-trip cost: {pct(item['estimated_round_trip_cost_pct'])}",
        f"Net expectancy: {pct(item['net_expectancy_pct'])}",f"Confidence: {item['confidence']}",'',
        'BUY:','Market','',f"TP1: {price(item['tp1'])}",f"TP2: {price(item['tp2'])}",
        f"STOP: {price(item['stop'])}",f"MAX HOLD: {item['max_hold_minutes']} min",'',
        f"WHY NOW: {why}",f"RISKS: {risks}",
        'SHADOW MODE — no order is sent; this is not a guarantee or investment advice.'])
