import uvicorn
from app import app
from config import Settings

settings = Settings()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, ws_ping_timeout=3600)
