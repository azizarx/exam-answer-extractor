#!/usr/bin/env python3
"""Add queue tables/indexes without changing existing rows; SQLite backup required."""
import argparse
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from backend.db.database import Base, engine
from backend.db import models

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--backup',required=True,help='New backup file; will not overwrite an existing backup')
args=parser.parse_args()
backup=Path(args.backup)
if backup.exists():raise SystemExit('Backup already exists; refusing to overwrite')
backup.parent.mkdir(parents=True,exist_ok=True)
if engine.dialect.name!='sqlite':raise SystemExit('Use a managed database snapshot for a PostgreSQL migration')
source=Path(engine.url.database)
with sqlite3.connect(source) as connection,sqlite3.connect(backup) as target:
    connection.backup(target)
backup.chmod(0o600)
Base.metadata.create_all(engine)
with engine.begin() as connection:
    connection.execute(text('CREATE TABLE IF NOT EXISTS schema_migrations (version VARCHAR(100) PRIMARY KEY, applied_at VARCHAR(100) NOT NULL)'))
    connection.execute(text('CREATE INDEX IF NOT EXISTS ix_candidate_paper_number ON candidate_results (template_id, candidate_number, submission_id, page_number)'))
    connection.execute(text('INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES (:version,:stamp)'),{'version':'20260919_queue_storage_v1','stamp':datetime.utcnow().isoformat()})
print('Queue/storage schema added; existing rows preserved. Backup:',backup)
