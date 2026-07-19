"""QR code generation, returned as base64 PNG data URIs the frontend can drop
straight into an <img src>."""
import base64
import io

import qrcode


def make_qr_data_uri(data: str) -> str:
    img = qrcode.make(data, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
