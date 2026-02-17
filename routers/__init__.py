"""FastAPI routers for OMC Funding Tracker API."""
import json
from datetime import datetime
from decimal import Decimal


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


def serialize(obj):
    """Recursively convert Decimals/datetimes for JSON."""
    return json.loads(json.dumps(obj, cls=DecimalEncoder))
