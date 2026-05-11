import sqlite3
import os

# Path to the database (adjust if you run via Docker vs locally)
# If you ran docker, the file is in 'data/shortener.db'
# If you run locally, it's just 'shortener.db'
db_path = "shortener.db"
if not os.path.exists(db_path):
    db_path = "shortener.db"

if not os.path.exists(db_path):
    print("Database file not found! Make sure you start the server first so it gets generated.")
    exit()

# Connect to the SQLite database
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("--- URLs TABLE ---")
try:
    cursor.execute("SELECT id, short_code, long_url, created_at FROM urls")
    urls = cursor.fetchall()
    if urls:
        for row in urls:
            print(f"ID: {row[0]} | Code: {row[1]} | Long URL: {row[2]} | Created: {row[3]}")
    else:
        print("No URLs found.")
except sqlite3.OperationalError:
    print("Table 'urls' does not exist yet.")

print("\n--- CLICKS TABLE ---")
try:
    cursor.execute("SELECT id, url_id, clicked_at FROM clicks")
    clicks = cursor.fetchall()
    if clicks:
        for row in clicks:
            print(f"ID: {row[0]} | URL_ID: {row[1]} | Clicked At: {row[2]}")
    else:
        print("No clicks found.")
except sqlite3.OperationalError:
    print("Table 'clicks' does not exist yet.")

# Always close the connection when done
conn.close()
