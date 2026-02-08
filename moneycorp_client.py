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


def get_payments(account_id=None, from_date=None, to_date=None):
    """Get payments, optionally filtered."""
    params = {}
    if account_id:
        params['accountId'] = account_id
    if from_date:
        params['fromDate'] = from_date
    if to_date:
        params['toDate'] = to_date
    
    resp = requests.get(f'{BASE_URL}/payments', headers=_headers(), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_transactions(account_id=None, from_date=None, to_date=None):
    """Get transactions."""
    params = {}
    if account_id:
        params['accountId'] = account_id
    if from_date:
        params['fromDate'] = from_date
    if to_date:
        params['toDate'] = to_date
    
    resp = requests.get(f'{BASE_URL}/transactions', headers=_headers(), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


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
