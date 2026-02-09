"""MoneyCorp API client for payment cross-referencing."""
import logging
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = os.getenv('MONEYCORP_API_URL', 'https://corpapi.moneycorp.com')
LOGIN_ID = os.getenv('MONEYCORP_LOGIN_ID', 'ShortlistApi')
API_KEY = os.getenv('MONEYCORP_API_KEY', '')

_token = None
_token_expiry = 0


def authenticate():
    """Get fresh JWT token from MoneyCorp."""
    global _token, _token_expiry
    
    logger.info("Authenticating with MoneyCorp API at %s (login: %s)", BASE_URL, LOGIN_ID)
    resp = requests.post(f'{BASE_URL}/login', json={
        'loginId': LOGIN_ID,
        'apiKey': API_KEY,
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    _token = data.get('token') or data.get('access_token') or (data.get('data', {}) or {}).get('accessToken')
    # Token expires in 900s (15 min), refresh at 800s
    _token_expiry = time.time() + 800
    logger.info("MoneyCorp authentication successful (token expires in ~13min)")
    return _token


def get_token():
    """Get valid token, refreshing if needed."""
    global _token, _token_expiry
    if not _token or time.time() > _token_expiry:
        authenticate()
    return _token


def _headers():
    return {'Authorization': f'Bearer {get_token()}', 'Content-Type': 'application/json'}


def get_accounts():
    """List all accounts."""
    resp = requests.get(f'{BASE_URL}/accounts', headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_account_payments(account_id: str):
    """Get payments for a specific account."""
    resp = requests.get(f'{BASE_URL}/accounts/{account_id}/payments', headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_account_balances(account_id: str):
    """Get balances for a specific account."""
    resp = requests.get(f'{BASE_URL}/accounts/{account_id}/balances', headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_omc_accounts():
    """Get only Omnicom-related sub-accounts."""
    accounts = get_accounts()
    data = accounts.get('data', [])
    return [a for a in data if 'omnicom' in a['attributes']['accountName'].lower()
            or 'omni ' in a['attributes']['accountName'].lower()]


def get_all_omc_payments():
    """Get all payments across all OMC accounts. Returns list of dicts with NVC codes extracted.
    
    paymentReference format: '{tenant}.{nvc_code}' e.g. 'omnicomtbwa.NVC7KVAR66CR'
    """
    omc_accounts = get_omc_accounts()
    all_payments = []
    
    for acc in omc_accounts:
        acc_id = acc['id']
        acc_name = acc['attributes']['accountName']
        try:
            resp = get_account_payments(acc_id)
            payments = resp.get('data', [])
            for p in payments:
                attrs = p['attributes']
                # Extract NVC code from paymentReference (format: tenant.NVCxxxx)
                pay_ref = attrs.get('paymentReference', '')
                nvc_code = None
                if '.' in pay_ref:
                    parts = pay_ref.split('.', 1)
                    if parts[1].startswith('NVC'):
                        nvc_code = parts[1]
                
                all_payments.append({
                    'payment_id': p['id'],
                    'account_id': acc_id,
                    'account_name': acc_name,
                    'nvc_code': nvc_code,
                    'amount': attrs.get('paymentAmount'),
                    'currency': attrs.get('paymentCurrency'),
                    'status': attrs.get('paymentStatus'),
                    'payment_date': attrs.get('paymentDate'),
                    'value_date': attrs.get('paymentValueDate'),
                    'recipient_name': (attrs.get('recipientDetails') or {}).get('bankAccountName'),
                    'payment_reference': pay_ref,
                    'client_reference': attrs.get('clientReference'),
                    'batch_reference': attrs.get('batchReference'),
                    'created_at': attrs.get('createdAt'),
                })
            logger.info("MoneyCorp: %d payments from %s (account %s)", len(payments), acc_name, acc_id)
        except Exception as e:
            logger.error("MoneyCorp: failed to get payments for %s: %s", acc_name, e)
    
    logger.info("MoneyCorp: total %d payments across %d OMC accounts", len(all_payments), len(omc_accounts))
    return all_payments


if __name__ == '__main__':
    print("Authenticating with MoneyCorp...")
    token = authenticate()
    print(f"Token: {token[:50]}...")
    
    print("\nFetching accounts...")
    accounts = get_accounts()
    if isinstance(accounts, list):
        print(f"Found {len(accounts)} accounts")
        for a in accounts[:5]:
            print(f"  {a}")
    else:
        print(accounts)
