import os
from pathlib import Path
from pydantic import BaseModel, Field


class Settings(BaseModel):
    # Directory paths
    base_dir: Path = Path(__file__).resolve().parent.parent
    data_dir: Path = base_dir / "data"
    browser_data_dir: Path = base_dir / "browser_data"
    db_path: Path = Field(default_factory=lambda: Path(os.getenv("DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "queue.db"))))
    log_path: Path = data_dir / "logs.log"

    # Automation parameters
    like_delay_minutes: int = Field(default=5, ge=1, le=60, description="Delay before liking back (minutes)")
    like_delay_seconds: int = Field(default=300, ge=20, le=3600, description="Delay before liking back (seconds)")
    max_retries: int = Field(default=3, ge=1, le=10, description="Max retries for temporary failures")
    request_timeout_seconds: int = Field(default=30, ge=5, le=120, description="Request timeout in seconds")
    scan_interval_seconds: int = Field(default=60, ge=15, le=3600, description="Interval between scans (seconds)")
    human_jitter_seconds: int = Field(default=30, ge=0, le=120, description="Random jitter window (±seconds)")
    headless_mode: bool = Field(default_factory=lambda: os.getenv("HEADLESS_MODE", "false").lower() in ("1", "true", "yes"), description="Run browser in headless background mode")
    test_mode: bool = Field(default_factory=lambda: os.getenv("TEST_MODE", "false").lower() in ("1", "true", "yes"), description="Run in Test Mode without external modifications")

    # Mingle2 URLs
    target_url: str = "https://mingle2.com/mutual_match/who_is_interested_in_me"
    login_url: str = "https://mingle2.com/user/login"
    home_url: str = "https://mingle2.com/"

    # Web Dashboard Server
    host: str = Field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = Field(default_factory=lambda: int(os.getenv("PORT", "8000")))

    def ensure_directories(self) -> None:
        """Ensure necessary runtime directories exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.browser_data_dir.mkdir(parents=True, exist_ok=True)


# Global settings singleton
settings = Settings()
settings.ensure_directories()
