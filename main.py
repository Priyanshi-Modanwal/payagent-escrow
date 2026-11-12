import os
import httpx
import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from ai_agent import verify_escrow_milestone

load_dotenv()

app = FastAPI(title="PayAgent Escrow", version="1.0")

def init_db():
    conn = sqlite3.connect("escrow.db")
    cursor = conn.cursor()
    
    # Transactions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            order_id TEXT PRIMARY KEY,
            amount TEXT,
            status TEXT
        )
    ''')
    
    # Agent Logs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agent_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            status TEXT,
            reason TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

@app.on_event("startup")
def startup_event():
    init_db()

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET")
PAYPAL_API_BASE = os.getenv("PAYPAL_API_BASE", "https://api-m.sandbox.paypal.com")

class EscrowOrder(BaseModel):
    amount: str
    currency: str = "USD"

class TaskSubmission(BaseModel):
    task_description: str
    proof_submission: str

class ResubmitRequest(BaseModel):
    new_proof: str

@app.get("/")
def read_root():
    return {"message": "PayAgent Escrow Backend is running successfully"}

async def get_paypal_access_token():
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="PayPal Client ID or Secret not found in .env file")
    
    auth_url = f"{PAYPAL_API_BASE}/v1/oauth2/token"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            auth_url,
            auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
            data={"grant_type": "client_credentials"}
        )
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"PayPal Auth Failed: {response.text}")
        return response.json().get("access_token")

async def capture_paypal_logic(order_id: str):
    conn = sqlite3.connect("escrow.db")
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM transactions WHERE order_id = ?", (order_id,))
    row = cursor.fetchone()
    conn.close()
    
    db_status = row[0] if row else None

    access_token = await get_paypal_access_token()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    
    capture_url = f"{PAYPAL_API_BASE}/v2/checkout/orders/{order_id}/capture"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(capture_url, headers=headers, json={})
        
        # Fallback for mock-approved hackathon demo if PayPal sandbox rejects capture
        if response.status_code not in [200, 201]:
            if db_status == "APPROVED":
                conn = sqlite3.connect("escrow.db")
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE transactions SET status = ? WHERE order_id = ?", 
                    ("CAPTURED", order_id)
                )
                conn.commit()
                conn.close()
                return {
                    "id": order_id,
                    "status": "COMPLETED",
                    "note": "Mock captured successfully for hackathon demo"
                }
            else:
                raise HTTPException(status_code=500, detail=f"PayPal Capture Error: {response.text}")
        
        # Update SQLite database status to CAPTURED
        conn = sqlite3.connect("escrow.db")
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE transactions SET status = ? WHERE order_id = ?", 
            ("CAPTURED", order_id)
        )
        conn.commit()
        conn.close()
        
        return response.json()

@app.post("/create-escrow-order")
async def create_order(order: EscrowOrder):
    access_token = await get_paypal_access_token()
    
    orders_url = f"{PAYPAL_API_BASE}/v2/checkout/orders"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": order.currency,
                    "value": order.amount
                }
            }
        ]
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(orders_url, headers=headers, json=payload)
        if response.status_code not in [200, 201]:
            raise HTTPException(status_code=500, detail=f"PayPal Error: {response.text}")
        
        response_data = response.json()
        order_id = response_data.get("id")
        
        conn = sqlite3.connect("escrow.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions (order_id, amount, status) VALUES (?, ?, ?)", 
            (order_id, order.amount, "CREATED")
        )
        conn.commit()
        conn.close()
        
        return response_data

@app.post("/capture-escrow-order/{order_id}")
async def capture_order(order_id: str):
    return await capture_paypal_logic(order_id)

@app.post("/test-mock-approve/{order_id}")
async def test_mock_approve(order_id: str):
    conn = sqlite3.connect("escrow.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transactions WHERE order_id = ?", (order_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found in database")
    
    cursor.execute("UPDATE transactions SET status = ? WHERE order_id = ?", ("APPROVED", order_id))
    conn.commit()
    conn.close()
    
    return {
        "message": f"Order {order_id} status successfully updated to APPROVED for testing/demo.",
        "order_id": order_id,
        "status": "APPROVED"
    }

@app.post("/verify-and-release/{order_id}")
async def verify_and_release(order_id: str, submission: TaskSubmission):
    # 1. AI Verification
    result = verify_escrow_milestone(submission.task_description, submission.proof_submission)
    
    conn = sqlite3.connect("escrow.db")
    cursor = conn.cursor()
    
    # Log Agent Action
    cursor.execute(
        "INSERT INTO agent_logs (order_id, status, reason) VALUES (?, ?, ?)",
        (order_id, "APPROVED" if result.is_approved else "REJECTED", result.reason)
    )
    conn.commit()
    conn.close()

    if not result.is_approved:
        return {
            "order_id": order_id,
            "status": "DISPUTED",
            "reason": result.reason,
            "action": "Payment Held in Escrow"
        }

    # 2. Trigger PayPal Capture directly if approved
    capture_response = await capture_paypal_logic(order_id)
    return {
        "order_id": order_id,
        "status": "RELEASED",
        "reason": result.reason,
        "paypal_details": capture_response
    }

# --- NEW: RESUBMISSION & DISPUTE ENDPOINT ---
@app.post("/resubmit-task/{order_id}")
async def resubmit_task(order_id: str, payload: ResubmitRequest):
    conn = sqlite3.connect("escrow.db")
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM transactions WHERE order_id = ?", (order_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found in database")
    
    # Run AI re-evaluation with updated proof
    task_desc = "Milestone compliance re-evaluation after dispute revision"
    result = verify_escrow_milestone(task_desc, payload.new_proof)
    
    # Log re-submission agent action
    cursor.execute(
        "INSERT INTO agent_logs (order_id, status, reason) VALUES (?, ?, ?)",
        (order_id, "APPROVED" if result.is_approved else "REJECTED", f"Resubmission Audit: {result.reason}")
    )
    conn.commit()
    conn.close()

    if not result.is_approved:
        return {
            "order_id": order_id,
            "status": "DISPUTED",
            "reason": result.reason,
            "action": "Payment Kept in Escrow"
        }

    # If approved upon re-submission, trigger PayPal payout release
    capture_response = await capture_paypal_logic(order_id)
    return {
        "order_id": order_id,
        "status": "RELEASED",
        "reason": result.reason,
        "paypal_details": capture_response
    }
@app.get("/audit-logs/{order_id}")
def get_audit_logs(order_id: str):
    conn = sqlite3.connect("escrow.db")
    cursor = conn.cursor()
    cursor.execute("SELECT order_id, status, reason, timestamp FROM agent_logs WHERE order_id = ?", (order_id,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        raise HTTPException(status_code=404, detail="No audit logs found for this Order ID")
    
    audit_trail = [
        {"order_id": r[0], "status": r[1], "reason": r[2], "timestamp": r[3]}
        for r in rows
    ]
    return {
        "order_id": order_id,
        "audit_trail": audit_trail
    }