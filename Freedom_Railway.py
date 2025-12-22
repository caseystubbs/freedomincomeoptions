import time
import schedule
from datetime import datetime
import Freedom_Final as scanner

print("🚀 Railway Robot Started. Waiting for market hours...")

def run_job():
    print(f"⏰ Wake Up! Starting Scan at {datetime.now()}...")
    try:
        scanner.main() 
        print("✅ Scan finished. Going back to sleep.")
    except Exception as e:
        print(f"❌ Error during scan: {e}")

# Run every 30 minutes
schedule.every(30).minutes.do(run_job)

# Run once immediately on launch to prove it works
run_job()

while True:
    schedule.run_pending()
    time.sleep(60)