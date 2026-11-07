"""Explicitly run one pure management validation; no scheduler or source clients."""
import argparse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.config import settings
from .models import tables
from .service import Management

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id',required=True)
    args=parser.parse_args()
    with Session(create_engine(settings.database_url)) as db,db.begin():
        task=db.execute(tables['tasks'].select().where(tables['tasks'].c.id==args.task_id)).mappings().one_or_none()
        if not task or task['kind']!='proposal_validation':raise SystemExit('Unknown validation task')
        result=Management(db,task['created_by']).run_validation(args.task_id)
    print(result['validation_id'])

if __name__=='__main__':main()
