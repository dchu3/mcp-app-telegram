import pytest
import os
from unittest.mock import patch
from mcp_app_telegram.database import initialize_database

@pytest.fixture(autouse=True)
def test_db():
    db_file = "test.db"
    with patch('mcp_app_telegram.database.DATABASE_FILE', db_file):
        if os.path.exists(db_file):
            os.remove(db_file)
        
        initialize_database()
        yield
        
        if os.path.exists(db_file):
            os.remove(db_file)