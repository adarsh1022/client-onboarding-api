"""Check a running copy of the API end to end, using only the standard library.

    python scripts/smoke_test.py https://your-service.onrender.com
    API_KEY=ob_... python scripts/smoke_test.py http://localhost:8000

The key is read from the API_KEY environment variable (so it stays out of your shell
history), or you are asked for it. It checks that /health and /docs are open, that the
other endpoints refuse a missing or wrong key, then uploads samples/messy_customers.csv,
waits for it to finish, and prints the status, the errors and the customers.

Exit status 0 if every check passed.
"""

import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "messy_customers.csv"
# A free Render service sleeps when idle and takes about a minute to wake up.
WAKE_UP_SECONDS = 150


def request(method, url, key=None, body=None, content_type=None, timeout=60):
    headers = {"X-API-Key": key} if key else {}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def upload_sample(base, key):
    boundary = uuid.uuid4().hex
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{SAMPLE.name}"\r\n'
            "Content-Type: text/csv\r\n\r\n"
        ).encode()
        + SAMPLE.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return request(
        "POST", f"{base}/uploads", key, body, f"multipart/form-data; boundary={boundary}"
    )


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    base = argv[1].rstrip("/")
    key = os.environ.get("API_KEY") or getpass.getpass("API key (input hidden): ")
    failures = []

    def check(name, ok, detail=""):
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail else ""))
        if not ok:
            failures.append(name)

    print(f"Checking {base}")
    print("Waking the service if it is asleep (a free Render service can take a minute)...")
    deadline = time.monotonic() + WAKE_UP_SECONDS
    while True:
        try:
            status, _ = request("GET", f"{base}/health", timeout=30)
            if status == 200:
                break
        except OSError:
            pass
        if time.monotonic() > deadline:
            print("The service never answered /health.")
            return 1
        time.sleep(5)

    check("GET /health is open", status == 200)
    check("GET /docs is open", request("GET", f"{base}/docs")[0] == 200)
    check("GET /customers without a key -> 401", request("GET", f"{base}/customers")[0] == 401)
    check(
        "GET /customers with a wrong key -> 401",
        request("GET", f"{base}/customers", "ob_wrong")[0] == 401,
    )
    check("GET /customers with your key", request("GET", f"{base}/customers", key)[0] == 200)

    status, raw = upload_sample(base, key)
    check("POST /uploads -> 202", status == 202, f"got {status}")
    if status != 202:
        print(raw.decode(errors="replace"))
        return 1
    upload_id = json.loads(raw)["id"]

    upload = {}
    for _ in range(60):
        _, raw = request("GET", f"{base}/uploads/{upload_id}", key)
        upload = json.loads(raw)
        if upload["status"] not in ("pending", "processing"):
            break
        time.sleep(1)
    check("upload finished", upload.get("status") == "completed", upload.get("status", "?"))
    check(
        "sample counts",
        (upload.get("total_rows"), upload.get("valid_rows"), upload.get("invalid_rows"))
        == (10, 6, 4),
        f"total {upload.get('total_rows')}, valid {upload.get('valid_rows')}, "
        f"invalid {upload.get('invalid_rows')}",
    )

    print("\nUpload:", json.dumps(upload, indent=2))
    _, raw = request("GET", f"{base}/uploads/{upload_id}/errors", key)
    errors = json.loads(raw)
    print(f"\nErrors ({errors['total']}):")
    for e in errors["items"]:
        print(f"  row {e['row_number']}: {e['field']}: {e['message']}")
    _, raw = request("GET", f"{base}/customers?limit=100", key)
    customers = json.loads(raw)
    print(f"\nCustomers ({customers['total']}):")
    for c in customers["items"]:
        print(f"  {c['name']:<14} {c['email']:<26} {c['country_code']}  {c['phone'] or '-'}")

    print("\n" + ("All checks passed." if not failures else f"FAILED: {', '.join(failures)}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
