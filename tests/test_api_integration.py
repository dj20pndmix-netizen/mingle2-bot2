import sys
import io
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from src.server import app

def test_full_api_workflow():
    print("=" * 60)
    print("  TEST 2: FASTAPI INTEGRATION & ZIP UPLOAD WORKFLOW")
    print("=" * 60)
    
    client = TestClient(app)
    
    # 1. Health check
    print("[1] Testing /healthz...")
    res = client.get("/healthz")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    print(f"    -> Status: {res.status_code}")
    
    # 2. Upload minglebot_session.zip
    print("[2] Testing /api/session/upload with minglebot_session.zip...")
    zip_path = Path("minglebot_session.zip")
    assert zip_path.exists(), "minglebot_session.zip must exist"
    
    with open(zip_path, "rb") as f:
        zip_bytes = f.read()
    
    res = client.post(
        "/api/session/upload",
        files={"file": ("minglebot_session.zip", io.BytesIO(zip_bytes), "application/zip")}
    )
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    upload_data = res.json()
    print(f"    -> Upload response status: {upload_data.get('status')}")
    print(f"    -> Is Logged In: {upload_data.get('is_logged_in')}")
    assert upload_data.get("is_logged_in") is True, "Expected is_logged_in=True after uploading valid zip"
    
    # 3. Session State
    print("[3] Testing /api/session/state...")
    res = client.get("/api/session/state")
    assert res.status_code == 200
    state_data = res.json()
    print(f"    -> Session State: {state_data.get('session_state')}")
    print(f"    -> Connection Status: {state_data.get('connection_status')}")
    assert state_data.get("session_state") in ("CONNECTED", "SESSION_VERIFIED")
    
    # 4. Auth status
    print("[4] Testing /api/auth/status...")
    res = client.get("/api/auth/status")
    assert res.status_code == 200
    auth_data = res.json()
    print(f"    -> Is Logged In: {auth_data.get('is_logged_in')}")
    assert auth_data.get("is_logged_in") is True
    
    # 5. Session Verify
    print("[5] Testing /api/session/verify...")
    res = client.post("/api/session/verify")
    assert res.status_code == 200
    verify_data = res.json()
    print(f"    -> Verify Status: {verify_data.get('status')}")
    print(f"    -> Message: {verify_data.get('message')}")
    assert verify_data.get("is_authenticated") is True
    
    print("\n>>> ALL API & ZIP INTEGRATION TESTS PASSED (5/5)! <<<\n")

if __name__ == "__main__":
    test_full_api_workflow()
