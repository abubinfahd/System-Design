import requests
import concurrent.futures
import time

# --- CONFIGURATION ---
BASE_URL = "http://localhost:8000"
LONG_URL = "https://www.google.com"
CONCURRENT_USERS = 100       # Number of simultaneous requests
TOTAL_REQUESTS = 1000        # Total number of requests to send
RESULT_FILE = "load_test_result.txt"

def create_short_url():
    """Helper to create a short URL to test redirects."""
    try:
        response = requests.post(f"{BASE_URL}/shorten", json={"long_url": LONG_URL})
        if response.status_code == 201:
            return response.json().get("short_code")
    except requests.exceptions.ConnectionError:
        pass
    return None

def make_request(short_code):
    """Hits the redirect endpoint and measures response time."""
    start_time = time.time()
    try:
        # allow_redirects=False because we just want to measure the API's response time (the 302),
        # not the time it takes to actually load google.com
        response = requests.get(f"{BASE_URL}/{short_code}", allow_redirects=False)
        latency = (time.time() - start_time) * 1000  # Convert to milliseconds
        return {
            "status": response.status_code,
            "latency": latency,
            "success": response.status_code == 302
        }
    except Exception as e:
        latency = (time.time() - start_time) * 1000
        return {
            "status": 500,
            "latency": latency,
            "success": False,
            "error": str(e)
        }

def run_load_test():
    print(f"Checking if server is running at {BASE_URL}...")
    short_code = create_short_url()
    
    if not short_code:
        print("Server is not running or unreachable! Start the server first.")
        return

    print(f"Created test short_code: {short_code}")
    print(f"Starting load test: {TOTAL_REQUESTS} requests with {CONCURRENT_USERS} concurrent users...\n")

    results = []
    start_test_time = time.time()

    # Using ThreadPoolExecutor to simulate concurrent users
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENT_USERS) as executor:
        # Map the make_request function to execute TOTAL_REQUESTS times
        futures = [executor.submit(make_request, short_code) for _ in range(TOTAL_REQUESTS)]
        
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    total_time = time.time() - start_test_time
    
    # Calculate statistics
    success_count = sum(1 for r in results if r["success"])
    fail_count = TOTAL_REQUESTS - success_count
    
    latencies = [r["latency"] for r in results]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    max_latency = max(latencies) if latencies else 0
    min_latency = min(latencies) if latencies else 0
    requests_per_sec = TOTAL_REQUESTS / total_time if total_time > 0 else 0

    # Format the report
    report = (
        "=======================================\n"
        "       URL SHORTENER LOAD TEST         \n"
        "=======================================\n"
        f"Total Requests:      {TOTAL_REQUESTS}\n"
        f"Concurrent Users:    {CONCURRENT_USERS}\n"
        f"Total Time Taken:    {total_time:.2f} seconds\n"
        "---------------------------------------\n"
        f"Successful Requests: {success_count} ({(success_count/TOTAL_REQUESTS)*100:.1f}%)\n"
        f"Failed Requests:     {fail_count}\n"
        f"Requests per Second: {requests_per_sec:.2f} req/s\n"
        "---------------------------------------\n"
        f"Average Latency:     {avg_latency:.2f} ms\n"
        f"Minimum Latency:     {min_latency:.2f} ms\n"
        f"Maximum Latency:     {max_latency:.2f} ms\n"
        "=======================================\n"
    )

    print(report)

    # Save to file
    with open(RESULT_FILE, "w") as f:
        f.write(report)
        print(f"Results saved to -> {RESULT_FILE}")

if __name__ == "__main__":
    run_load_test()
