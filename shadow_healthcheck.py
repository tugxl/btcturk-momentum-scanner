"""Container healthcheck for a recently completed V3 shadow scan."""
import argparse
import sqlite3
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--max-age',type=float,default=900)
    args=parser.parse_args()
    try:
        db=sqlite3.connect(f'file:{args.db}?mode=ro',uri=True)
        row=db.execute('SELECT MAX(timestamp) FROM shadow_health').fetchone()
        db.close()
        return 0 if row and row[0] and time.time()-row[0]<=args.max_age else 1
    except (OSError,sqlite3.Error):
        return 1


if __name__=='__main__':
    raise SystemExit(main())
