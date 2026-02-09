"""Vision-based extraction for image-only remittance documents (LDN GSS)."""
import base64
import json
import logging
import os
from typing import List, Dict, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')

EXTRACTION_PROMPT = """You are extracting structured payment/remittance data from a bank document image.

Extract ALL payment line items you can see. For each line item, extract:
- reference: The payment reference or invoice number
- amount: The payment amount (numeric, no currency symbols)
- currency: The currency code (USD, GBP, EUR, etc.)

Also extract any header-level data:
- settlement_amount: Total settlement/transfer amount
- settlement_date: Date of settlement (YYYY-MM-DD format if possible)
- debtor_name: Name of the payer/debtor
- creditor_name: Name of the payee/creditor
- account_reference: Any account reference (e.g., CK8300829172KC)
- end_to_end_id: End-to-end identification reference

Return ONLY valid JSON in this format:
{
  "document_type": "pacs008_summary" | "remittance_advice" | "unknown",
  "header": {
    "settlement_amount": 248484.00,
    "settlement_currency": "GBP",
    "settlement_date": "2026-01-22",
    "debtor_name": "...",
    "creditor_name": "...",
    "account_reference": "CK8300829172KC",
    "end_to_end_id": "..."
  },
  "line_items": [
    {"reference": "INV-001", "amount": 1500.00, "currency": "GBP"},
    ...
  ]
}

If the image doesn't contain payment data (e.g., it's a logo or signature), return:
{"document_type": "non_payment", "header": {}, "line_items": []}
"""


def extract_from_image(image_data: bytes, mime_type: str = "image/png") -> Dict:
    """Use Claude Vision to extract payment data from an image."""
    if not ANTHROPIC_API_KEY:
        logger.warning("No ANTHROPIC_API_KEY set, skipping vision extraction")
        return {"document_type": "error", "error": "No API key", "header": {}, "line_items": []}

    b64 = base64.standard_b64encode(image_data).decode('utf-8')

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2048,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64,
                        }
                    },
                    {
                        "type": "text",
                        "text": EXTRACTION_PROMPT,
                    }
                ]
            }]
        },
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()

    # Extract text content
    text = ""
    for block in result.get("content", []):
        if block.get("type") == "text":
            text += block["text"]

    # Parse JSON from response
    try:
        # Try to find JSON in the response
        if "{" in text:
            json_start = text.index("{")
            json_end = text.rindex("}") + 1
            return json.loads(text[json_start:json_end])
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse vision response as JSON: %s", e)
        return {"document_type": "parse_error", "raw_text": text, "header": {}, "line_items": []}

    return {"document_type": "empty", "header": {}, "line_items": []}


def process_ldn_gss_email(email_data: dict) -> List[Dict]:
    """Process a LDN GSS email — extract data from images using vision.
    
    Returns list of extracted payment records with NVC-like references.
    """
    results = []
    
    for att in email_data.get('attachments', []):
        filename = att.get('filename', '')
        mime = att.get('mimeType', '')
        data = att.get('data', b'')
        
        # Skip tiny images (logos/signatures) and non-images
        if not mime.startswith('image/') or len(data) < 5000:
            continue
        
        logger.info("Vision extracting from %s (%d bytes)", filename, len(data))
        extracted = extract_from_image(data, mime)
        
        if extracted.get('document_type') in ('non_payment', 'error', 'empty'):
            logger.info("Skipping %s: %s", filename, extracted.get('document_type'))
            continue
        
        extracted['source_filename'] = filename
        extracted['source_email_id'] = email_data.get('id', '')
        results.append(extracted)
        
        logger.info("Extracted from %s: type=%s, %d line items, header=%s",
                    filename, extracted.get('document_type'),
                    len(extracted.get('line_items', [])),
                    extracted.get('header', {}).get('account_reference', 'none'))
    
    return results


if __name__ == '__main__':
    # Test with a saved image
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'rb') as f:
            data = f.read()
        result = extract_from_image(data)
        print(json.dumps(result, indent=2))
    else:
        print("Usage: python vision_parser.py <image_file>")
