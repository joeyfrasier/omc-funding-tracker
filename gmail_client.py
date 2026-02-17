"""Gmail client for fetching Omnicom remittance emails."""
import base64
import json
import logging
import os
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE', 'service-account.json')
IMPERSONATE_USER = os.getenv('GMAIL_IMPERSONATE', '')
PROCESSED_FILE = Path('data/processed_emails.json')

# Email source queries
EMAIL_SOURCES = {
    'oasys': {
        'query': 'from:"OASYS Notification" has:attachment -subject:Re: -subject:RE:',
        'description': 'OASYS Notifications (Omnicom agencies)',
    },
    'd365_ach': {
        'query': 'subject:"OMG AP ACH PAYMENT REMITTANCE" has:attachment',
        'description': 'D365 OMG ACH Remittance',
    },
    'ldn_gss': {
        'query': 'from:"LDN GSS" subject:Remittance',
        'description': 'LDN GSS Payments (image-only, manual review)',
    },
    'flywheel': {
        'query': 'to:paymentops+flywheelfunding@worksuite.com has:attachment',
        'description': 'Flywheel Agency Funding Requests',
    },
}


def get_service():
    """Build authenticated Gmail service."""
    logger.info("Authenticating Gmail service (impersonating %s)", IMPERSONATE_USER)
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    creds = creds.with_subject(IMPERSONATE_USER)
    service = build('gmail', 'v1', credentials=creds)
    logger.info("Gmail service authenticated successfully")
    return service


def load_processed():
    """Load set of already-processed message IDs."""
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text()))
    return set()


def save_processed(processed: set):
    """Save processed message IDs."""
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_FILE.write_text(json.dumps(sorted(processed)))


def get_header(headers, name):
    """Extract header value by name."""
    for h in headers:
        if h['name'].lower() == name.lower():
            return h['value']
    return ''


def fetch_emails(source_key='oasys', max_results=100, include_processed=False):
    """Fetch remittance emails from a source.
    
    Returns list of dicts with: id, subject, from, date, attachments
    """
    service = get_service()
    source = EMAIL_SOURCES[source_key]
    processed = load_processed()
    
    results = service.users().messages().list(
        userId='me', q=source['query'], maxResults=max_results
    ).execute()
    
    messages = results.get('messages', [])
    logger.info("[%s] Query returned %d messages (processed so far: %d)", source_key, len(messages), len(processed))
    emails = []
    
    for msg_ref in messages:
        msg_id = msg_ref['id']
        if not include_processed and msg_id in processed:
            logger.debug("[%s] Skipping already-processed message %s", source_key, msg_id)
            continue
        
        msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
        headers = msg['payload']['headers']
        
        subject = get_header(headers, 'Subject')
        email_data = {
            'id': msg_id,
            'source': source_key,
            'subject': subject,
            'from': get_header(headers, 'From'),
            'date': get_header(headers, 'Date'),
            'attachments': [],
        }
        
        # Extract attachments
        _extract_attachments(service, msg_id, msg['payload'], email_data['attachments'])
        logger.info("[%s] Fetched: %s (%d attachments)", source_key, subject[:60], len(email_data['attachments']))
        emails.append(email_data)
    
    return emails


def _extract_attachments(service, msg_id, payload, attachments):
    """Recursively extract attachments from email payload."""
    parts = payload.get('parts', [])
    for part in parts:
        filename = part.get('filename', '')
        if filename and part['body'].get('attachmentId'):
            att = service.users().messages().attachments().get(
                userId='me', messageId=msg_id, id=part['body']['attachmentId']
            ).execute()
            raw_data = base64.urlsafe_b64decode(att['data'])
            attachments.append({
                'filename': filename,
                'mimeType': part['mimeType'],
                'data': raw_data,
            })
        if part.get('parts'):
            _extract_attachments(service, msg_id, part, attachments)


def mark_processed(message_ids):
    """Mark message IDs as processed."""
    processed = load_processed()
    processed.update(message_ids)
    save_processed(processed)


def fetch_all_remittances(max_per_source=100):
    """Fetch from all sources, return combined list."""
    all_emails = []
    for key in ['oasys', 'd365_ach', 'flywheel']:
        try:
            logger.info("Fetching emails from source: %s (max %d)", key, max_per_source)
            emails = fetch_emails(key, max_results=max_per_source)
            all_emails.extend(emails)
            logger.info("[%s] Found %d new emails", key, len(emails))
        except Exception as e:
            logger.error("[%s] Error fetching emails: %s", key, e, exc_info=True)
    
    # LDN GSS - just count, no CSV to parse
    try:
        logger.info("Fetching emails from source: ldn_gss (image-only, manual review)")
        ldn = fetch_emails('ldn_gss', max_results=10)
        logger.info("[ldn_gss] Found %d emails (image-only, flagged for manual review)", len(ldn))
        for e in ldn:
            e['manual_review'] = True
        all_emails.extend(ldn)
    except Exception as e:
        logger.error("[ldn_gss] Error fetching emails: %s", e, exc_info=True)
    
    return all_emails


if __name__ == '__main__':
    print("Fetching remittance emails...")
    emails = fetch_all_remittances(max_per_source=5)
    for e in emails:
        att_names = [a['filename'] for a in e['attachments']]
        print(f"  {e['date'][:16]} | {e['source']:10} | {e['subject'][:60]} | {att_names}")
