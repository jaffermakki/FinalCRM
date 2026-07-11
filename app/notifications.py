import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, make_msgid
import httpx
from .encryption import decrypt_value


def _send_via_smtp(host: str, port: int, user: str, password: str, msg: MIMEMultipart) -> tuple[bool, str]:
    """Shared low-level sender. Returns (success, message)."""
    try:
        # Special case: Brevo API mode
        if host == "brevo_api":
            api_key = password
            url = "https://api.brevo.com/v3/smtp/email"
            headers = {"api-key": api_key, "Content-Type": "application/json"}

            # Extract plain and HTML parts
            html_body = None
            plain_body = None
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    html_body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")
                elif part.get_content_type() == "text/plain":
                    plain_body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")

            payload = {
                "sender": {"name": msg["From"], "email": msg["From"]},
                "to": [{"email": msg["To"]}],
                "subject": msg["Subject"],
                "htmlContent": html_body or plain_body or ""
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

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(body, "html"))

    ok, detail = _send_via_smtp(host, int(port) if port else 0, user, password, msg)
    if ok:
        return True, "Email accepted." + _from_address_warning(user, from_addr)
    return False, detail


def _receipt_html(shop_name, shop_address, shop_phone, invoice):
    # Format the line items to match the layout
    rows = "".join(
        f'<tr>'
        f'<td style="padding:10px; border-bottom:1px solid #eaeaea; color:#333;">{l.name}</td>'
        f'<td style="padding:10px; border-bottom:1px solid #eaeaea; color:#333; text-align:center;">{l.qty}</td>'
        f'<td style="padding:10px; border-bottom:1px solid #eaeaea; color:#333; text-align:right;">${l.price:.2f}</td>'
        f'<td style="padding:10px; border-bottom:1px solid #eaeaea; color:#333; text-align:right;">${(l.price * l.qty):.2f}</td>'
        f'</tr>'
        for l in invoice.lines
    )
    
    addr_line = f'<div>{shop_address}</div>' if shop_address else ""
    phone_line = f'<div>{shop_phone}</div>' if shop_phone else ""
    
    # Fallbacks in case your invoice model doesn't store these exact properties yet
    customer_name = getattr(invoice, "customer_name", "Walk-in")
    payment_method = getattr(invoice, "payment_method", "Cash")

    return f"""\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 600px; margin: 0 auto; color: #333; border: 1px solid #eaeaea; padding: 30px; background-color: #ffffff;">
    
    <!-- Header -->
    <div style="margin-bottom: 30px;">
        <h2 style="margin: 0 0 5px 0; color: #000; font-size: 24px;">{shop_name}</h2>
        <div style="color: #666; font-size: 14px; line-height: 1.5;">
            {addr_line}
            {phone_line}
        </div>
    </div>

    <!-- Top Summary Table -->
    <table style="width: 100%; border-collapse: collapse; margin-bottom: 25px; font-size: 14px; background-color: #f8f9fa;">
        <thead>
            <tr>
                <th style="padding: 12px; text-align: left; border-bottom: 2px solid #dee2e6; color: #495057;">INVOICE #</th>
                <th style="padding: 12px; text-align: left; border-bottom: 2px solid #dee2e6; color: #495057;">DATE</th>
                <th style="padding: 12px; text-align: left; border-bottom: 2px solid #dee2e6; color: #495057;">PAYMENT</th>
                <th style="padding: 12px; text-align: right; border-bottom: 2px solid #dee2e6; color: #495057;">TOTAL</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td style="padding: 12px; font-weight: 600; color: #212529;">{invoice.number}</td>
                <td style="padding: 12px; color: #212529;">{invoice.date.strftime('%B %d, %Y')}</td>
                <td style="padding: 12px; color: #212529;">{payment_method}</td>
                <td style="padding: 12px; text-align: right; font-weight: 600; color: #212529;">${invoice.total:.2f}</td>
            </tr>
        </tbody>
    </table>

    <!-- Bill To -->
    <div style="margin-bottom: 25px; font-size: 14px;">
        <strong style="color: #495057;">BILL TO</strong><br>
        <span style="color: #212529;">{customer_name}</span>
    </div>

    <!-- Line Items Table -->
    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 14px;">
        <thead>
            <tr>
                <th style="padding: 10px; text-align: left; border-bottom: 2px solid #dee2e6; color: #495057;">DESCRIPTION</th>
                <th style="padding: 10px; text-align: center; border-bottom: 2px solid #dee2e6; color: #495057;">QTY</th>
                <th style="padding: 10px; text-align: right; border-bottom: 2px solid #dee2e6; color: #495057;">UNIT PRICE</th>
                <th style="padding: 10px; text-align: right; border-bottom: 2px solid #dee2e6; color: #495057;">AMOUNT</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>

    <!-- Totals Area -->
    <div style="margin-bottom: 40px; display: table; width: 100%;">
        <table style="width: 100%; border-collapse: collapse; font-size: 14px; margin-left: auto;">
            <tr>
                <td style="padding: 6px 10px; text-align: right; width: 60%; color: #495057;">Subtotal</td>
                <td style="padding: 6px 10px; text-align: right; width: 40%; color: #212529;">${invoice.subtotal:.2f}</td>
            </tr>
            <!-- Only show discount if it exists and is greater than 0 -->
            {"<tr><td style='padding: 6px 10px; text-align: right; color: #495057;'>Discount</td><td style='padding: 6px 10px; text-align: right; color: #212529;'>-$" + f"{invoice.discount:.2f}" + "</td></tr>" if getattr(invoice, 'discount', 0) > 0 else ""}
            <tr>
                <td style="padding: 6px 10px; text-align: right; color: #495057;">Tax</td>
                <td style="padding: 6px 10px; text-align: right; color: #212529;">${invoice.tax_total:.2f}</td>
            </tr>
            <tr>
                <td style="padding: 12px 10px; text-align: right; font-weight: bold; border-top: 2px solid #dee2e6; font-size: 16px; color: #212529;">Total</td>
                <td style="padding: 12px 10px; text-align: right; font-weight: bold; border-top: 2px solid #dee2e6; font-size: 16px; color: #212529;">${invoice.total:.2f}</td>
            </tr>
        </table>
    </div>

    <!-- Footer -->
    <div style="border-top: 1px solid #dee2e6; padding-top: 20px; font-size: 12px; color: #6c757d; text-align: center;">
        <p style="margin: 0 0 8px 0; font-weight: 600; font-size: 14px; color: #495057;">Thank you for choosing {shop_name}</p>
        <p style="margin: 0;">This invoice is designed to be printed minimally consider saving digitally where possible</p>
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
    
    # 1. Complete the cut-off plain text generation
    lines_text = "\n".join(
        f"{l.name} x{l.qty} ... ${l.price * l.qty:.2f}" 
        for l in invoice.lines
    )
    
    plain_body = f"""Receipt from {shop_name}
Invoice {invoice.number} · {invoice.date.strftime('%b %d, %Y')}

{lines_text}

Subtotal: ${invoice.subtotal:.2f}
Discount: -${invoice.discount:.2f}
Tax: ${invoice.tax_total:.2f}
Total: ${invoice.total:.2f}

Thank you for your business!
"""

    # 2. Generate the HTML body using your existing helper
    html_body = _receipt_html(shop_name, shop_address, shop_phone, invoice)

    # 3. Assemble the multipart email
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Receipt from {shop_name} (Invoice {invoice.number})"
    msg["From"] = from_addr
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    
    # Attach plain text first, then HTML
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    # 4. Send the email
    ok, detail = _send_via_smtp(host, int(port) if port else 0, user, password, msg)
    
    if ok:
        return True, "Receipt accepted." + _from_address_warning(user, from_addr)
    return False, detail


def send_sms(db, to_phone: str, message: str, get_setting) -> tuple[bool, str]:
    """
    Placeholder for SMS functionality.
    """
    # Just returns False right now so it doesn't break your app while you build it out.
    return False, "SMS sending is not yet implemented in the codebase."
