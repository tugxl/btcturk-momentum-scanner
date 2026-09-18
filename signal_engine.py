def signal_level(score):
    if score >= 80:
        return 'ACTION'
    if score >= 55:
        return 'WATCH'
    return 'NONE'
