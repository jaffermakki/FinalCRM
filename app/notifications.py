import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, make_msgid
import httpx
from .encryption import decrypt_value


def _send_via_smtp(host: str, port: int, user: str, password: str, msg: MIMEText) -> tuple[bool, str]:
    """Shared low-level sender. Returns (success, message)."""
    try:
        # Special case: Brevo API mode
        if host == "brevo_api":
            api_key = password  # store Brevo API key in smtp_password field
            url = "https://api.brevo.com/v3/smtp/email"
            headers = {"api-key": api_key, "Content-Type": "application/json"}
            payload = {
                "sender": {"name": msg["From"], "email": msg["From"]},
                "to": [{"email": msg["To"]}],
                "subject": msg["Subject"],
                "htmlContent": msg.as_string()
            }
            resp = httpx.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 201:
                return True, "Email sent successfully via Brevo API."
            return False, f"Brevo error ({resp.status_code}): {resp.text[:200]}"

        # Normal SMTP path
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as server:
                server.login(user, password)
                refused = server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(user, password)
                refused = server.send_message(msg)

        if refused:
            reasons = "; ".join(f"{addr}: {info}" for addr, info in refused.items())
            return False, f"Server rejected the recipient: {reasons}"
        return True, "Email accepted by the mail server."
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP login failed — check the username/password in Settings."
    except smtplib.SMTPRecipientsRefused as e:
        return False, f"Server refused the recipient address: {e}"
    except (smtplib.SMTPException, OSError, TimeoutError) as e:
        return False, f"Failed to send email: {e}"


def _from_address_warning(user: str, from_addr: str) -> str:
    def domain(addr):
        return addr.split("@")[-1].lower().strip() if "@" in addr else ""
    if from_addr and user and domain(from_addr) != domain(user):
        return (f" Note: your From address ({from_addr}) is on a different domain than "
                f"your SMTP login ({user}) — many providers silently drop mail like this.")
    return ""


def send_plain_email(db, to_email: str, subject: str, body: str, get_setting) -> tuple[bool, str]:
    host = get_setting(db, "smtp_host", "")
    port = get_setting(db, "smtp_port", "")
    user = get_setting(db, "smtp_user", "")
    password = decrypt_value(get_setting(db, "smtp_password", ""))
    from_addr = get_setting(db, "smtp_from", "") or user

    if not (host and user and password):
        return False, "Email is not configured — go to Settings → Notifications."

    msg = MIMEText(body, "html")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    ok, detail = _send_via_smtp(host, int(port) if port else 0, user, password, msg)
    if ok:
        return True, "Email accepted." + _from_address_warning(user, from_addr)
    return False, detail


def _receipt_html(shop_name, shop_address, shop_phone, invoice):
    rows = "".join(
        f'<tr><td style="padding:8px 0;border-bottom:1px solid #e8e2d9;color:#2c2c2c">{l.name} '
        f'<span style="color:#9a9284">×{l.qty}</span></td>'
        f'<td style="padding:8px 0;border-bottom:1px solid #e8e2d9;text-align:right;color:#2c2c2c">'
        f'${l.price * l.qty:.2f}</td></tr>'
        for l in invoice.lines
    )
    addr_line = f'<div style="color:#7a7060;font-size:12px;margin-top:4px">{shop_address}</div>' if shop_address else ""
    phone_line = f'<div style="color:#7a7060;font-size:12px">{shop_phone}</div>' if shop_phone else ""
    return f"""\
<div style="background:#f4f1ea;padding:28px 12px;font-family:Georgia,'Times New Roman',serif">
  <div style="max-width:520px;margin:0 auto;background:#fdfcf9;border:1px solid #e8e2d9;padding:32px 30px">
    <div style="border-bottom:2px solid #1e5c3a;padding-bottom:16px;margin-bottom:20px">
      <div style="font-size:20px;font-weight:bold;color:#1e5c3a">{shop_name}</div>
      {addr_line}{phone_line}
    </div>
    <div style="font-size:13px;color:#7a7060;margin-bottom:4px">Receipt for</div>
    <div style="font-size:16px;color:#2c2c2c;margin-bottom:20px">Invoice {invoice.number} &nbsp;·&nbsp; {invoice.date.strftime('%b %d, %Y')}</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px">{rows}</table>
    <table style="width:100%;margin-top:14px;font-size:13px">
      <tr><td style="color:#7a7060;padding:2px 0">Subtotal</td><td style="text-align:right;color:#2c2c2c">${invoice.subtotal:.2f}</td></tr>
      <tr><td style="color:#7a7060;padding:2px 0">Discount</td><td style="text-align:right;color:#2c2c2c">-${invoice.discount:.2f}</td></tr>
      <tr><td style="color:#7a7060;padding:2px 0">Tax</td><td style="text-align:right;color:#2c2c2c">${invoice.tax_total:.2f}</td></tr>
      <tr><td style="color:#1e5c3a;font-weight:bold;font-size:16px;padding-top:8px;border-top:1px solid #e8e2d9">Total</td>
          <td style="text-align:right;color:#1e5c3a;font-weight:bold;font-size:16px;padding-top:8px;border-top:1px solid #e8e2d9">${invoice.total:.2f}</td></tr>
    </table>
    <div style="margin-top:26px;padding-top:16px;border-top:1px solid #e8e2d9;text-align:center;color:#9a9284;font-size:12px">
      Thank you for your business!
    </div>
  </div>
</div>"""


def send_email_receipt(db, invoice, to_email: str, get_setting) -> tuple[bool, str]:
    host = get_setting(db, "smtp_host", "")
    port = get_setting(db, "smtp_port", "")
    user = get_setting(db, "smtp_user", "")
    password = decrypt_value(get_setting(db, "smtp_password", ""))
    from_addr = get_setting(db, "smtp_from", "") or user

    if not (host and user and password):
        return False, "Email is not configured — go to Settings → Notifications."

    shop_name = get_setting(db, "shop_name", "Your Shop")
    shop_address = get_setting(db, "shop_address", "")
    shop_phone = get_setting(db, "shop_phone", "")
    lines_text = "\n".join(f"{l.name} x{l.qty} — ${l.price * l.qty:.2f}" for l in invoice.lines)
    text_body = (
        f"Receipt from {shop_name}\n\n"
        f"Invoice: {invoice.number}\n"
        f"Date: {invoice.date.strftime('%b %d, %Y %H:%M')}\n\n"
        f"{lines_text}\n\n"
        f"Subtotal: ${invoice.subtotal:.2f}\n"
        f"Discount: ${invoice.discount:.2f}\n"
        f"Tax: ${invoice.tax_total:.2f}\n"
        f"Total: ${invoice.total:.2f}\n\n"
        f"Thank you for your business!"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Your receipt from {shop_name} — {invoice.number}"
    msg["From"] = from_addr
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(_receipt_html(shop_name, shop_address, shop_phone, invoice), "html"))

    ok, detail = _send_via_smtp(host, int(port) if port else 0, user, password, msg)
    if ok:
        warning = _from_address_warning(user, from_addr)
        base = "Receipt accepted by the mail server."
        if warning:
            return True, base + warning
        return True, base + " If it doesn't arrive within a few minutes, check spam/junk."
    return False, detail
