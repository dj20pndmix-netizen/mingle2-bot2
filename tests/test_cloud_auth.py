import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from src.server import app

def test_cloud_auth():
    client = TestClient(app)

    # 1. Healthz endpoint
    res = client.get('/healthz')
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    print("[PASS] Healthz endpoint responds with 200 OK")

    # 2. Auth status
    res = client.get('/api/auth/status')
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert "session_state" in data
    print(f"[PASS] Auth status endpoint: session_state={data.get('session_state')}")

    # 3. Direct login validation (empty credentials rejected with 422)
    res = client.post('/api/auth/login', json={'login': '', 'password': ''})
    assert res.status_code == 422, f"Expected 422, got {res.status_code}"
    print("[PASS] Direct login request validation rejects empty inputs")

    # 4. Check DOM elements in index.html
    with open('static/index.html', 'r', encoding='utf-8') as f:
        html = f.read()
    assert 'id="session-zip-input"' in html, "session-zip-input missing from index.html"
    assert 'id="credentials-login-modal"' in html, "credentials-login-modal missing from index.html"
    assert 'id="login-username"' in html, "login-username input missing from modal"
    assert 'id="login-password"' in html, "login-password input missing from modal"
    assert 'Upload Session Zip' in html, "Upload Session Zip button missing"
    print("[PASS] static/index.html contains session-zip-input, credentials modal, and actions")

    # 5. Check functions in app.js
    with open('static/app.js', 'r', encoding='utf-8') as f:
        js = f.read()
    assert 'function uploadSessionZip' in js, "uploadSessionZip missing from app.js"
    assert 'function openCredentialsModal' in js, "openCredentialsModal missing from app.js"
    assert 'function closeCredentialsModal' in js, "closeCredentialsModal missing from app.js"
    assert 'function submitCredentialsLogin' in js, "submitCredentialsLogin missing from app.js"
    print("[PASS] static/app.js implements all modal, login, and upload handlers")

    print("\n=======================================================")
    print("ALL CLOUD AUTH & UPLOAD VERIFICATION TESTS PASSED (5/5)")
    print("=======================================================")

if __name__ == "__main__":
    test_cloud_auth()
