import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


def _send_via(host: str, port: int, user: str, password: str, use_tls: bool, use_ssl: bool,
              msg: MIMEMultipart) -> None:
    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        if use_tls:
            server.starttls()
    try:
        if user:
            server.login(user, password)
        server.sendmail(msg["From"], msg["To"].split(","), msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass


def send_email(to: str, subject: str, html_body: str, text_body: str = "") -> bool:
    """Send email with primary SMTP and fallback to backup SMTP."""
    msg = MIMEMultipart("alternative")
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body or html_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    errors = []
    primary_configs = [
        (settings.SMTP_HOST, settings.SMTP_PORT, settings.SMTP_USER, settings.SMTP_PASSWORD,
         settings.SMTP_TLS, settings.SMTP_SSL),
    ]
    backup = []
    if settings.SMTP_BACKUP_HOST:
        backup.append((settings.SMTP_BACKUP_HOST, settings.SMTP_BACKUP_PORT,
                       settings.SMTP_BACKUP_USER, settings.SMTP_BACKUP_PASSWORD, True, False))

    for cfg in primary_configs + backup:
        try:
            _send_via(*cfg, msg)
            return True
        except Exception as exc:
            errors.append(str(exc))
            logger.warning("SMTP send failed via %s: %s", cfg[0], exc)

    logger.error("Email to %s failed after all SMTP servers: %s", to, errors)
    return False


def send_verification_email(to: str, token: str) -> bool:
    link = f"{settings.FRONTEND_URL}/dashboard.html?token={token}&action=verify"
    html = f"""<p>Здравствуйте!</p><p>Подтвердите ваш email для завершения регистрации:</p>
    <p><a href="{link}">Подтвердить email</a></p><p>Ссылка действует 24 часа.</p>"""
    return send_email(to, "Подтверждение email — QA Agent", html)


def send_reset_email(to: str, token: str) -> bool:
    link = f"{settings.FRONTEND_URL}/dashboard.html?token={token}&action=reset"
    html = f"""<p>Здравствуйте!</p><p>Для восстановления пароля перейдите по ссылке:</p>
    <p><a href="{link}">Сбросить пароль</a></p><p>Ссылка действует 15 минут.</p>"""
    return send_email(to, "Восстановление пароля — QA Agent", html)


def send_limit_notice_email(to: str, pct: int, is_100: bool = False) -> bool:
    if is_100:
        subject = "QA Agent: достигнут лимит тестов"
        html = (f"<p>Вы использовали 100% месячного лимита тестов. Новые запуски заблокированы.</p>"
                f'<p><a href="{settings.FRONTEND_URL}/pricing">Перейти к тарифам</a></p>')
    else:
        subject = "QA Agent: использовано 80% лимита тестов"
        html = (f"<p>Вы использовали {pct}% месячного лимита тестов.</p>"
                f'<p><a href="{settings.FRONTEND_URL}/pricing">Подобрать тариф</a></p>')
    return send_email(to, subject, html)


def send_renewal_notice_email(to: str, days_left: int) -> bool:
    html = f"<p>Подписка будет автоматически продлена через {days_left} дн.</p>"
    return send_email(to, "QA Agent: скоро списание по подписке", html)


def send_report_email(to: str, subject: str, summary_text: str) -> bool:
    html = f"<p>Результат теста:</p><pre>{summary_text}</pre>"
    return send_email(to, subject, html)