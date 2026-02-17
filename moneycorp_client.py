"""MoneyCorp API client for payment cross-referencing."""
import logging
import os
import time
import requests
from requests.exceptions import ConnectionError, Timeout
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = os.getenv('MONEYCORP_API_URL', 'https://corpapi.moneycorp.com')
LOGIN_ID = os.getenv('MONEYCORP_LOGIN_ID', 'ShortlistApi')
API_KEY = os.getenv('MONEYCORP_API_KEY', '')

API_TIMEOUT = int(os.getenv('MONEYCORP_API_TIMEOUT', '30'))
API_MAX_RETRIES = int(os.getenv('MONEYCORP_API_RETRIES', '3'))

_token = None
_token_expiry = 0


def _api_call(method: str, url: str, **kwargs):
    """Make an API call with retry logic for transient failures."""
    kwargs.setdefault('timeout', API_TIMEOUT)
    last_error = None
    for attempt in range(1, API_MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            # Retry on 5xx server errors
            if resp.status_code >= 500 and attempt < API_MAX_RETRIES:
                logger.warning("MoneyCorp API %s %s returned %d — retry %d/%d",
                               method, url, resp.status_code, attempt, API_MAX_RETRIES)
                time.sleep(2 ** (attempt - 1))
                continue
            resp.raise_for_status()
            return resp
        except (ConnectionError, Timeout) as e:
            last_error = e
            if attempt < API_MAX_RETRIES:
                wait = 2 ** (attempt - 1)
                logger.warning("MoneyCorp API %s %s failed: %s — retry %d/%d in %ds",
                               method, url, e, attempt, API_MAX_RETRIES, wait)
                time.sleep(wait)
            else:
                logger.error("MoneyCorp API %s %s failed after %d attempts: %s",
                             method, url, API_MAX_RETRIES, e)
                raise
    raise last_error


def authenticate():
    """Get fresh JWT token from MoneyCorp."""
    global _token, _token_expiry

    logger.info("Authenticating with MoneyCorp API at %s (login: %s)", BASE_URL, LOGIN_ID)
    resp = _api_call('POST', f'{BASE_URL}/login', json={
        'loginId': LOGIN_ID,
        'apiKey': API_KEY,
    })
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
    return _api_call('GET', f'{BASE_URL}/accounts', headers=_headers()).json()


def get_account_payments(account_id: str):
    """Get payments for a specific account."""
    return _api_call('GET', f'{BASE_URL}/accounts/{account_id}/payments', headers=_headers()).json()


def get_account_balances(account_id: str):
    """Get balances for a specific account."""
    return _api_call('GET', f'{BASE_URL}/accounts/{account_id}/balances', headers=_headers()).json()


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
                
                recip = attrs.get('recipientDetails') or {}
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
                    'recipient_name': recip.get('bankAccountName'),
                    'recipient_country': recip.get('bankAccountCountry'),
                    'recipient_currency': recip.get('bankAccountCurrency'),
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


def get_account_received_payments(account_id: str):
    """Get received payments for a specific account."""
    return _api_call('GET', f'{BASE_URL}/accounts/{account_id}/receivedPayments', headers=_headers()).json()


def parse_payer_from_info(info: str) -> str:
    """Extract payer name from infoToAccountOwner field.
    
    Examples:
      "THE SCIENOMICS DES:ACH10030 ID:..." → "THE SCIENOMICS"
      "BBDO USA LLC WIRE TYPE:WIRE IN..." → "BBDO USA LLC"
    """
    if not info:
        return ''
    # Take first line, strip whitespace
    line = info.split('\r')[0].split('\n')[0].strip()
    # Cut at common delimiters
    for delim in ['DES:', 'WIRE TYPE:', 'ID:', 'TRX']:
        idx = line.find(delim)
        if idx > 0:
            line = line[:idx].strip()
            break
    return line.strip()


def get_all_omc_received_payments():
    """Get all received payments across all OMC sub-accounts."""
    omc_accounts = get_omc_accounts()
    all_received = []
    
    for acc in omc_accounts:
        acc_id = acc['id']
        acc_name = acc['attributes']['accountName']
        try:
            resp = get_account_received_payments(acc_id)
            payments = resp.get('data', [])
            for p in payments:
                attrs = p.get('attributes', {})
                payer = parse_payer_from_info(attrs.get('infoToAccountOwner', ''))
                all_received.append({
                    'id': p['id'],
                    'account_id': acc_id,
                    'account_name': acc_name,
                    'amount': float(attrs.get('amount', 0)),
                    'currency': attrs.get('currency', 'USD'),
                    'payment_date': attrs.get('paymentDate', ''),
                    'payment_status': attrs.get('paymentStatus', ''),
                    'payer_name': payer,
                    'raw_info': attrs.get('infoToAccountOwner', ''),
                    'msl_reference': attrs.get('mslReference1', ''),
                    'created_on': attrs.get('createdOn', ''),
                })
            logger.info("MoneyCorp: %d received payments from %s (account %s)", len(payments), acc_name, acc_id)
        except Exception as e:
            logger.error("MoneyCorp: failed to get received payments for %s: %s", acc_name, e)
    
    logger.info("MoneyCorp: total %d received payments across %d OMC accounts", len(all_received), len(omc_accounts))
    return all_received


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
