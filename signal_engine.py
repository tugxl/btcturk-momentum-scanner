def signal_level(score):
    if score >= 90:
        return 'EXCEPTIONAL'
    if score >= 80:
        return 'STRONG EARLY MOMENTUM'
    if score >= 70:
        return 'SETUP FORMING'
    if score >= 60:
        return 'WATCH'
    return 'IGNORE'
