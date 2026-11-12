# ai_agent.py
from pydantic import BaseModel

class VerificationResult(BaseModel):
    is_approved: bool
    confidence_score: float
    reason: str

def verify_escrow_milestone(task_description: str, proof_submission: str) -> VerificationResult:
    """
    Mock AI Agent Logic: Verifies if the submitted proof meets 
    the escrow contract requirements.
    """
    if not proof_submission or len(proof_submission.strip()) < 10:
        return VerificationResult(
            is_approved=False,
            confidence_score=0.1,
            reason="Proof of work is empty or insufficient."
        )
    
    # Simple semantic rule verification (Can connect OpenAI/Gemini API here)
    if "git" in proof_submission.lower() or "http" in proof_submission.lower() or "done" in proof_submission.lower():
        return VerificationResult(
            is_approved=True,
            confidence_score=0.95,
            reason="Deliverable verified: Proof matches required specifications."
        )
    
    return VerificationResult(
        is_approved=False,
        confidence_score=0.4,
        reason="Deliverable does not meet criteria."
    )