import sys
from app.db.database import SessionLocal
from app.db.models import URL, Click

print("Connecting to the database...")
try:
    db = SessionLocal()
    
    print("--- URLs TABLE ---")
    urls = db.query(URL).all()
    if urls:
        for row in urls:
            print(f"ID: {row.id} | Code: {row.short_code} | Long URL: {row.long_url} | Created: {row.created_at}")
    else:
        print("No URLs found.")

    print("\n--- CLICKS TABLE ---")
    clicks = db.query(Click).all()
    if clicks:
        for row in clicks:
            print(f"ID: {row.id} | URL_ID: {row.url_id} | Clicked At: {row.clicked_at}")
    else:
        print("No clicks found.")
        
    db.close()
except Exception as e:
    print(f"Error reading database: {e}")
    sys.exit(1)
