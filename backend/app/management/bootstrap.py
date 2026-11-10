"""Offline bootstrap for an existing user. No default account or password."""
import argparse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.config import settings
from .service import Management

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id',required=True)
    parser.add_argument('--reason',required=True)
    args=parser.parse_args()
    with Session(create_engine(settings.database_url)) as db,db.begin():
        result=Management(db,args.user_id).bootstrap(args.reason)
    print(result)

if __name__=='__main__':main()
