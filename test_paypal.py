import os
from fastapi import FastAPI
from dotenv import load_dotenv

# .env file se credentials load karne ke liye
load_dotenv()

app = FastAPI(title="PayAgent Escrow API", version="1.0")

@app.get("/")
def read_root():
    return {"message": "PayAgent Escrow Backend is running successfully!"}

@app.get("/check-env")
def check_env():
    client_id = os.getenv("PAYPAL_CLIENT_ID")
    if client_id:
        return {"status": "Connected", "client_id_prefix": client_id[:5] + "..."}
    return {"status": "Error", "message": "PayPal Client ID not found!"}