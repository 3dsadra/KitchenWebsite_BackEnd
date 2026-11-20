"""اتصال ساده به درگاه پرداخت زرین‌پال (حالت آزمایشی / Sandbox)."""
import json
import urllib.error
import urllib.request

from django.conf import settings


class ZarinpalError(Exception):
    pass


def _post(path: str, payload: dict) -> dict:
    url = f"{settings.ZARINPAL_BASE_URL.rstrip('/')}{path}"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_000_000).decode("utf-8")
        data = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise ZarinpalError("ارتباط با درگاه پرداخت برقرار نشد.") from exc

    if not isinstance(data, dict):
        raise ZarinpalError("پاسخ درگاه پرداخت معتبر نیست.")
    return data


def request_payment(amount_rial: int, callback_url: str, description: str) -> dict:
    """درخواست تراکنش؛ مبلغ باید به ریال باشد."""
    return _post(
        "/v4/payment/request.json",
        {
            "merchant_id": settings.ZARINPAL_MERCHANT_ID,
            "amount": int(amount_rial),
            "callback_url": callback_url,
            "description": description,
        },
    )


def verify_payment(amount_rial: int, authority: str) -> dict:
    return _post(
        "/v4/payment/verify.json",
        {
            "merchant_id": settings.ZARINPAL_MERCHANT_ID,
            "amount": int(amount_rial),
            "authority": authority,
        },
    )


def start_url(authority: str) -> str:
    return f"{settings.ZARINPAL_BASE_URL.rstrip('/')}/StartPay/{authority}"
