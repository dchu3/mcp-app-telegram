import sqlite3
from .config import DATABASE_FILE

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_database():
    """Initializes the database and creates the gas_alerts table if it doesn't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gas_alerts (
            chat_id INTEGER NOT NULL,
            network TEXT NOT NULL,
            price_threshold INTEGER NOT NULL,
            direction TEXT NOT NULL,
            PRIMARY KEY (chat_id, network, direction)
        );
    """)
    conn.commit()
    conn.close()

def add_gas_alert(chat_id: int, network: str, price_threshold: int, direction: str):
    """Adds or updates a gas alert in the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO gas_alerts (chat_id, network, price_threshold, direction)
        VALUES (?, ?, ?, ?);
    """, (chat_id, network, price_threshold, direction))
    conn.commit()
    conn.close()

def remove_gas_alert(chat_id: int, network: str, direction: str):
    """Removes a gas alert from the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM gas_alerts WHERE chat_id = ? AND network = ? AND direction = ?;
    """, (chat_id, network, direction))
    conn.commit()
    conn.close()

def get_gas_alerts(network: str) -> list:
    """Retrieves all gas alerts for a given network."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT chat_id, price_threshold, direction FROM gas_alerts WHERE network = ?;
    """, (network,))
    alerts = cursor.fetchall()
    conn.close()
    return alerts

def get_gas_alerts_for_chat(chat_id: int) -> list:
    """Retrieves all gas alerts for a given chat."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT network, price_threshold, direction FROM gas_alerts WHERE chat_id = ?;
    """, (chat_id,))
    alerts = cursor.fetchall()
    conn.close()
    return alerts

def get_distinct_networks_with_alerts() -> list:
    """Retrieves a distinct list of networks with active gas alerts."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT network FROM gas_alerts;
    """)
    networks = [row['network'] for row in cursor.fetchall()]
    conn.close()
    return networks

def remove_all_gas_alerts_for_chat(chat_id: int):
    """Removes all gas alerts for a given chat."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM gas_alerts WHERE chat_id = ?;
    """, (chat_id,))
    conn.commit()
    conn.close()