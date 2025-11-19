"""
Database initialization and management script
"""
import sys
from backend.db.database import init_db, drop_db, engine
from backend.db.models import ExamSubmission, MultipleChoiceAnswer, FreeResponseAnswer, ProcessingLog
from sqlalchemy import inspect
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_tables():
    """Check if tables exist in database"""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    logger.info("Current database tables:")
    for table in tables:
        logger.info(f"  - {table}")
    
    return tables


def main():
    """Main database management function"""
    if len(sys.argv) < 2:
        print("Usage: python init_db.py [init|drop|check]")
        print("  init  - Create all tables")
        print("  drop  - Drop all tables (CAUTION!)")
        print("  check - Check existing tables")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == "init":
        logger.info("Initializing database tables...")
        init_db()
        logger.info("Database initialization complete!")
        check_tables()
        
    elif command == "drop":
        confirm = input("Are you sure you want to drop all tables? (yes/no): ")
        if confirm.lower() == "yes":
            logger.warning("Dropping all tables...")
            drop_db()
            logger.info("All tables dropped!")
        else:
            logger.info("Operation cancelled")
            
    elif command == "check":
        tables = check_tables()
        if not tables:
            logger.warning("No tables found in database!")
        
    else:
        logger.error(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
