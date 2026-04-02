import base64
import hashlib
import hmac
import time
from typing import Any

from fastapi import HTTPException, status


def verify_hmac_signature(
    secret: str,
    payload: bytes,
    headers: dict[str, str],
    max_age_seconds: int = 300,
    is_clerk: bool = False,
) -> None:
    if is_clerk:
        msg_id = headers.get("svix-id", "")
        msg_timestamp = headers.get("svix-timestamp", "")
        msg_signature = headers.get("svix-signature", "")
        
        if not msg_id or not msg_timestamp or not msg_signature:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing svix headers")
            
        try:
            timestamp_int = int(msg_timestamp)
            if time.time() - timestamp_int > max_age_seconds:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook too old")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid timestamp format")
            
        signed_content = f"{msg_id}.{msg_timestamp}.".encode("utf-8") + payload
        clerk_secret_bytes = base64.b64decode(secret.split("_")[1] if secret.startswith("whsec_") else secret)
        
        expected_sig = hmac.new(clerk_secret_bytes, signed_content, hashlib.sha256).digest()
        encoded_sig = base64.b64encode(expected_sig).decode("utf-8")
        
        passed_sigs = msg_signature.split(" ")
        for passed_sig in passed_sigs:
            version, signature = passed_sig.split(",", 1)
            if version == "v1" and hmac.compare_digest(signature, encoded_sig):
                return
                
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")
    
    else:
        # Stripe
        stripe_signature = headers.get("stripe-signature", "")
        if not stripe_signature:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing stripe signature")
            
        parts = dict(part.split("=") for part in stripe_signature.split(",") if "=" in part)
        timestamp = parts.get("t", "")
        signatures = [part.split("=")[1] for part in stripe_signature.split(",") if part.startswith("v1=")]
        
        if not timestamp or not signatures:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid stripe signature format")
            
        try:
            timestamp_int = int(timestamp)
            if time.time() - timestamp_int > max_age_seconds:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook too old")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid timestamp format")
            
        signed_content = f"{timestamp}.".encode("utf-8") + payload
        expected_sig = hmac.new(secret.encode("utf-8"), signed_content, hashlib.sha256).hexdigest()
        
        for signature in signatures:
            if hmac.compare_digest(signature, expected_sig):
                return
                
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")
