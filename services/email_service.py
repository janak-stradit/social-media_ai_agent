"""Approval-notification emails: sent when a generated asset is approved,
with the story/strategy context and the generated image attached. Plain
smtplib/email (stdlib) over STARTTLS - no new dependency needed."""

import contextlib
import contextvars
import mimetypes
import os
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import Config


def _resolve_local_path(url_or_path: str | None) -> str | None:
    """Same resolution rule as MediaGenerationService._resolve_image_path:
    accepts an absolute path, a relative path that already exists, or a
    /static/uploads/<file> URL and maps it to the file on disk."""
    if not url_or_path:
        return None
    if os.path.isabs(url_or_path) and os.path.exists(url_or_path):
        return url_or_path
    if os.path.exists(url_or_path):
        return url_or_path
    candidate = os.path.join(Config.UPLOAD_FOLDER, os.path.basename(url_or_path))
    if os.path.exists(candidate):
        return candidate
    # Not on this server's disk - fetch the S3 copy (services/storage_service.py)
    from services import storage_service

    return storage_service.ensure_local(storage_service.UPLOAD_URL_PREFIX + os.path.basename(url_or_path))


def _load_image_slides(paths: list[str]) -> list[tuple[str, bytes, str]]:
    """Resolves a list of image URLs/paths to (local_path, bytes, subtype)
    tuples ready to attach - shared by both the approval-notification and
    approval-request emails."""
    slides = []
    for p in paths:
        local_image = _resolve_local_path(p)
        if not local_image:
            continue
        with open(local_image, "rb") as f:
            img_data = f.read()
        mime_type, _ = mimetypes.guess_type(local_image)
        subtype = (mime_type or "image/png").split("/")[-1]
        slides.append((local_image, img_data, subtype))
    return slides


def _attach_slides(msg: MIMEMultipart, html: str, slides: list[tuple[str, bytes, str]]) -> None:
    """mixed(related(html, inline-images), attachment-images): the inline
    copies are what cid:slide_N in the HTML displays; the separate attachment
    copies are real attachments so they also show up in the client's
    attachment list and can be downloaded directly."""
    related = MIMEMultipart("related")
    related.attach(MIMEText(html, "html"))

    for i, (local_image, img_data, subtype) in enumerate(slides):
        inline_image = MIMEImage(img_data, _subtype=subtype)
        inline_image.add_header("Content-ID", f"<slide_{i}>")
        inline_image.add_header("Content-Disposition", "inline", filename=os.path.basename(local_image))
        related.attach(inline_image)

    msg.attach(related)

    for local_image, img_data, subtype in slides:
        attached_image = MIMEImage(img_data, _subtype=subtype)
        attached_image.add_header("Content-Disposition", "attachment", filename=os.path.basename(local_image))
        msg.attach(attached_image)


def _escape(text: str | None) -> str:
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_html(
    story: str | None,
    platform: str | None,
    competitors: list[str] | None,
    caption: str | None,
    asset_type: str | None,
    slide_count: int,
    slide_titles: list[str] | None = None,
) -> str:
    competitors_str = ", ".join(competitors) if competitors else "N/A"
    platform_label = (platform or "N/A").capitalize()
    asset_label = (asset_type or "content").capitalize()
    story_html = _escape(story).replace("\n", "<br>") if story else "<em>No story context captured.</em>"
    caption_html = _escape(caption).replace("\n", "<br>") if caption else "<em>No caption generated.</em>"

    if slide_count <= 0:
        image_block = ""
    elif slide_count == 1:
        image_block = """
        <tr>
            <td style="padding: 0 32px 24px 32px;">
                <img src="cid:slide_0" alt="Generated asset"
                     style="max-width: 100%; border-radius: 10px; border: 1px solid #e5e7eb; display: block;">
            </td>
        </tr>
        """
    else:
        titles = slide_titles or [f"Slide {i + 1}" for i in range(slide_count)]
        slides_html = ""
        for i in range(slide_count):
            label = _escape(titles[i]) if i < len(titles) else f"Slide {i + 1}"
            slides_html += f"""
            <tr>
                <td style="padding: 0 0 12px 0;">
                    <div style="font-size: 11px; font-weight: 700; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 6px;">Slide {i + 1} &middot; {label}</div>
                    <img src="cid:slide_{i}" alt="{label}"
                         style="max-width: 100%; border-radius: 10px; border: 1px solid #e5e7eb; display: block;">
                </td>
            </tr>
            """
        image_block = f"""
        <tr>
            <td style="padding: 0 32px 24px 32px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{slides_html}</table>
            </td>
        </tr>
        """

    # Many webmail clients (Outlook/OWA included) strip CSS gradient
    # functions from sanitized HTML but keep solid background-color - so the
    # header below sets background-color as the real fallback and layers
    # background-image (gradient) on top only as an enhancement, using
    # longhand properties instead of the `background:` shorthand (which
    # would otherwise reset color to transparent when the image is dropped).
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: 'Segoe UI', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f3f4f6; padding: 32px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="600" cellpadding="0" cellspacing="0"
                       style="background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.06);">
                    <tr>
                        <td style="background-color: #e85a1c; height: 5px; line-height: 5px; font-size: 0;">&nbsp;</td>
                    </tr>
                    <tr>
                        <td style="background-color: #e85a1c; background-image: linear-gradient(135deg, #ffa066 0%, #e85a1c 100%); padding: 26px 32px;">
                            <table role="presentation" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td style="vertical-align: middle; padding-right: 14px;">
                                        <table role="presentation" width="42" height="42" cellpadding="0" cellspacing="0"
                                               style="background-color: rgba(255,255,255,0.18); border-radius: 10px;">
                                            <tr><td align="center" valign="middle" style="color: #ffffff; font-size: 20px; font-weight: 700;">&#10003;</td></tr>
                                        </table>
                                    </td>
                                    <td style="vertical-align: middle;">
                                        <span style="color: #ffffff; font-size: 20px; font-weight: 700;">Content Approved</span>
                                        <div style="color: rgba(255,255,255,0.85); font-size: 13px; margin-top: 2px;">
                                            AVIR AI &mdash; Analysis Dashboard
                                        </div>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 24px 32px 8px 32px;">
                            <table role="presentation" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td style="padding-right: 8px;">
                                        <span style="display: inline-block; background-color: #fff4ec; color: #c2410c; font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 999px;">{_escape(platform_label)}</span>
                                    </td>
                                    <td>
                                        <span style="display: inline-block; background-color: #ecfdf5; color: #059669; font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 999px;">{_escape(asset_label)}</span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 32px 20px 32px; color: #6b7280; font-size: 13px;">
                            <strong style="color: #374151;">Source competitors:</strong> {_escape(competitors_str)}
                        </td>
                    </tr>
                    {image_block}
                    <tr>
                        <td style="padding: 0 32px 8px 32px;">
                            <div style="font-size: 13px; font-weight: 700; color: #c2410c; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 8px;">Generated Caption</div>
                            <div style="background-color: #f9fafb; border-left: 3px solid #c2410c; border-radius: 8px; padding: 16px; font-size: 14px; line-height: 1.6; color: #1f2937; white-space: pre-wrap;">{caption_html}</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 20px 32px 32px 32px;">
                            <div style="font-size: 13px; font-weight: 700; color: #c2410c; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 8px;">Story / Strategy Context</div>
                            <div style="background-color: #f9fafb; border-left: 3px solid #d1d5db; border-radius: 8px; padding: 16px; font-size: 13px; line-height: 1.6; color: #4b5563; white-space: pre-wrap; max-height: 400px; overflow: hidden;">{story_html}</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f9fafb; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 12px;">
                            This is an automated notification from the Analysis Dashboard's approval workflow.
                            {"Attached copies of the generated image(s) are included below." if slide_count > 0 else ""}
                            <div style="margin-top: 8px; color: #9ca3af; font-size: 11px;">AVIR AI is a product of <a href="https://stradit.com/" style="color: #c2410c; text-decoration: none; font-weight: 600;">StradIT</a></div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


def _build_request_html(
    approval_url: str,
    story: str | None,
    platform: str | None,
    competitors: list[str] | None,
    caption: str | None,
    asset_type: str | None,
    slide_count: int,
) -> str:
    competitors_str = ", ".join(competitors) if competitors else "N/A"
    platform_label = (platform or "N/A").capitalize()
    asset_label = (asset_type or "content").capitalize()
    story_html = _escape(story).replace("\n", "<br>") if story else "<em>No story context captured.</em>"
    caption_html = _escape(caption).replace("\n", "<br>") if caption else "<em>No caption generated.</em>"

    if slide_count <= 0:
        image_block = ""
    else:
        slides_html = "".join(
            f"""
            <tr>
                <td style="padding: 0 0 12px 0;">
                    <img src="cid:slide_{i}" alt="Generated asset"
                         style="max-width: 100%; border-radius: 10px; border: 1px solid #e5e7eb; display: block;">
                </td>
            </tr>
            """
            for i in range(slide_count)
        )
        image_block = f"""
        <tr>
            <td style="padding: 0 32px 24px 32px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{slides_html}</table>
            </td>
        </tr>
        """

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: 'Segoe UI', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f3f4f6; padding: 32px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="600" cellpadding="0" cellspacing="0"
                       style="background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.06);">
                    <tr>
                        <td style="background-color: #e85a1c; height: 5px; line-height: 5px; font-size: 0;">&nbsp;</td>
                    </tr>
                    <tr>
                        <td style="background-color: #e85a1c; background-image: linear-gradient(135deg, #ffa066 0%, #e85a1c 100%); padding: 26px 32px;">
                            <table role="presentation" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td style="vertical-align: middle; padding-right: 14px;">
                                        <table role="presentation" width="42" height="42" cellpadding="0" cellspacing="0"
                                               style="background-color: rgba(255,255,255,0.18); border-radius: 10px;">
                                            <tr><td align="center" valign="middle" style="color: #ffffff; font-size: 20px; font-weight: 700;">&#128065;</td></tr>
                                        </table>
                                    </td>
                                    <td style="vertical-align: middle;">
                                        <span style="color: #ffffff; font-size: 20px; font-weight: 700;">Review Requested</span>
                                        <div style="color: rgba(255,255,255,0.85); font-size: 13px; margin-top: 2px;">
                                            AVIR AI &mdash; Analysis Dashboard
                                        </div>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 24px 32px 8px 32px;">
                            <table role="presentation" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td style="padding-right: 8px;">
                                        <span style="display: inline-block; background-color: #fff4ec; color: #c2410c; font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 999px;">{_escape(platform_label)}</span>
                                    </td>
                                    <td>
                                        <span style="display: inline-block; background-color: #ecfdf5; color: #059669; font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 999px;">{_escape(asset_label)}</span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 32px 20px 32px; color: #6b7280; font-size: 13px;">
                            <strong style="color: #374151;">Source competitors:</strong> {_escape(competitors_str)}
                        </td>
                    </tr>
                    {image_block}
                    <tr>
                        <td style="padding: 0 32px 8px 32px;">
                            <div style="font-size: 13px; font-weight: 700; color: #c2410c; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 8px;">Generated Caption</div>
                            <div style="background-color: #f9fafb; border-left: 3px solid #c2410c; border-radius: 8px; padding: 16px; font-size: 14px; line-height: 1.6; color: #1f2937; white-space: pre-wrap;">{caption_html}</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 20px 32px 8px 32px;">
                            <div style="font-size: 13px; font-weight: 700; color: #c2410c; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 8px;">Story / Strategy Context</div>
                            <div style="background-color: #f9fafb; border-left: 3px solid #d1d5db; border-radius: 8px; padding: 16px; font-size: 13px; line-height: 1.6; color: #4b5563; white-space: pre-wrap; max-height: 400px; overflow: hidden;">{story_html}</div>
                        </td>
                    </tr>
                    <tr>
                        <td align="center" style="padding: 24px 32px 32px 32px;">
                            <a href="{_escape(approval_url)}"
                               style="display: inline-block; background-color: #c2410c; color: #ffffff; font-size: 15px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 999px;">
                                Review &amp; Decide
                            </a>
                            <div style="color: #9ca3af; font-size: 12px; margin-top: 12px;">Opens the Analysis Dashboard - sign-in required.</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f9fafb; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 12px;">
                            This is an automated review request from the Analysis Dashboard's approval workflow.
                            <div style="margin-top: 8px; color: #9ca3af; font-size: 11px;">AVIR AI is a product of <a href="https://stradit.com/" style="color: #c2410c; text-decoration: none; font-weight: 600;">StradIT</a></div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


def _build_verification_html(name: str, verify_url: str) -> str:
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: 'Segoe UI', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f3f4f6; padding: 32px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                       style="background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.06);">
                    <tr>
                        <td style="background-color: #e85a1c; height: 5px; line-height: 5px; font-size: 0;">&nbsp;</td>
                    </tr>
                    <tr>
                        <td style="background-color: #e85a1c; background-image: linear-gradient(135deg, #ffa066 0%, #e85a1c 100%); padding: 32px; text-align: center;">
                            <span style="color: #ffffff; font-size: 22px; font-weight: 700;">Welcome to AVIR AI</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 32px 32px 8px 32px; color: #1f2937; font-size: 15px; line-height: 1.6;">
                            Hi {_escape(name)},<br><br>
                            Thanks for creating an account. One quick step before you get started - confirm this is your email address.
                        </td>
                    </tr>
                    <tr>
                        <td align="center" style="padding: 24px 32px 32px 32px;">
                            <a href="{_escape(verify_url)}"
                               style="display: inline-block; background-color: #c2410c; color: #ffffff; font-size: 15px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 999px;">
                                Verify my email
                            </a>
                            <div style="color: #9ca3af; font-size: 12px; margin-top: 12px;">This link expires in 24 hours.</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f9fafb; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 12px;">
                            If you didn't create this account, you can safely ignore this email.
                            <div style="margin-top: 8px; color: #9ca3af; font-size: 11px;">AVIR AI is a product of <a href="https://stradit.com/" style="color: #c2410c; text-decoration: none; font-weight: 600;">StradIT</a></div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


def _build_password_reset_html(name: str, reset_url: str, ttl_minutes: int) -> str:
    return f"""<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background-color: #f8f9fc; font-family: 'Segoe UI', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8f9fc; padding: 32px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                       style="background-color: #ffffff; border: 1px solid #e6e8ef; border-radius: 16px; overflow: hidden;">
                    <tr>
                        <td style="background-color: #e85a1c; background-image: linear-gradient(135deg, #ffa066 0%, #e85a1c 100%); padding: 32px; text-align: center;">
                            <span style="color: #ffffff; font-size: 22px; font-weight: 700;">Reset your password</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 32px 32px 8px 32px; color: #172033; font-size: 15px; line-height: 1.6;">
                            Hi {_escape(name)},<br><br>
                            We received a request to reset the password for your AVIR AI account. Click the button below to choose a new one.
                        </td>
                    </tr>
                    <tr>
                        <td align="center" style="padding: 24px 32px 32px 32px;">
                            <a href="{_escape(reset_url)}"
                               style="display: inline-block; background-color: #c2410c; color: #ffffff; font-size: 15px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 6px;">
                                Reset my password
                            </a>
                            <div style="color: #667085; font-size: 12px; margin-top: 12px;">This link expires in {ttl_minutes} minutes and can only be used once.</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f8f9fc; border-top: 1px solid #e6e8ef; color: #667085; font-size: 12px;">
                            If you didn't ask to reset your password, you can safely ignore this email - your password won't change.
                            <div style="margin-top: 8px; color: #9ca3af; font-size: 11px;">AVIR AI is a product of <a href="https://stradit.com/" style="color: #c2410c; text-decoration: none; font-weight: 600;">StradIT</a></div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


def _build_credit_decision_html(
    name: str, approved: bool, requested_amount: float, credit_limit: float | None, dashboard_url: str
) -> str:
    """The user's answer to a credit extension request (Admin -> Credit
    Extension Requests -> approve / reject)."""
    if approved:
        heading = "Your credit request was approved"
        body = (
            f"Good news: your request for <strong>${requested_amount:,.2f}</strong> of extra credit has been approved. "
            + (f"Your credit limit is now <strong>${credit_limit:,.2f}</strong>." if credit_limit is not None else "")
            + " You can carry on creating content right away."
        )
        button = "Open AVIR AI"
        note = "Credit is used as you generate posts, images and videos."
    else:
        heading = "Your credit request was not approved"
        body = (
            f"Your request for <strong>${requested_amount:,.2f}</strong> of extra credit was not approved this time. "
            + (f"Your credit limit stays at <strong>${credit_limit:,.2f}</strong>. " if credit_limit is not None else "")
            + "If you need more, you can send a new request with a little more detail about what you plan to create."
        )
        button = "Go to AVIR AI"
        note = "Questions about this decision? Reply to this email or contact your account admin."
    return f"""<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background-color: #f8f9fc; font-family: 'Segoe UI', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8f9fc; padding: 32px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                       style="background-color: #ffffff; border: 1px solid #e6e8ef; border-radius: 16px; overflow: hidden;">
                    <tr>
                        <td style="background-color: #e85a1c; background-image: linear-gradient(135deg, #ffa066 0%, #e85a1c 100%); padding: 32px; text-align: center;">
                            <span style="color: #ffffff; font-size: 22px; font-weight: 700;">{heading}</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 32px 32px 8px 32px; color: #172033; font-size: 15px; line-height: 1.6;">
                            Hi {_escape(name)},<br><br>
                            {body}
                        </td>
                    </tr>
                    <tr>
                        <td align="center" style="padding: 24px 32px 32px 32px;">
                            <a href="{_escape(dashboard_url)}"
                               style="display: inline-block; background-color: #c2410c; color: #ffffff; font-size: 15px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 6px;">
                                {button}
                            </a>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f8f9fc; border-top: 1px solid #e6e8ef; color: #667085; font-size: 12px;">
                            {note}
                            <div style="margin-top: 8px; color: #9ca3af; font-size: 11px;">AVIR AI is a product of <a href="https://stradit.com/" style="color: #c2410c; text-decoration: none; font-weight: 600;">StradIT</a>.</div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


def _build_weekly_ideas_html(
    name: str, company_name: str | None, ideas: list[dict], dashboard_url: str, settings_url: str, unsubscribe_url: str
) -> str:
    """The weekly ideas email. ideas: [{title, summary, origin, makes, url}] -
    each button opens Studio Chat with that idea's brief filled in."""
    cards = "".join(
        f"""
                    <tr>
                        <td style="padding: 0 32px 16px 32px;">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                                   style="border: 1px solid #e6e8ef; border-radius: 12px;">
                                <tr>
                                    <td style="padding: 18px 20px;">
                                        <div style="color: #c2410c; font-size: 12px; font-weight: 700;">{_escape(idea.get("origin"))}</div>
                                        <div style="color: #172033; font-size: 17px; font-weight: 700; line-height: 1.35; margin-top: 6px;">{_escape(idea.get("title"))}</div>
                                        <div style="color: #475569; font-size: 14px; line-height: 1.55; margin-top: 6px;">{_escape(idea.get("summary"))}</div>
                                        <div style="color: #667085; font-size: 12px; margin-top: 10px;">{_escape(idea.get("makes"))}</div>
                                        <div style="margin-top: 14px;">
                                            <a href="{_escape(idea.get("url"))}"
                                               style="display: inline-block; background-color: #c2410c; color: #ffffff; font-size: 14px; font-weight: 700; text-decoration: none; padding: 10px 20px; border-radius: 6px;">
                                                Create this post
                                            </a>
                                        </div>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>"""
        for idea in ideas
    )
    brand = f" for {_escape(company_name)}" if company_name else ""
    return f"""<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background-color: #f8f9fc; font-family: 'Segoe UI', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8f9fc; padding: 32px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                       style="background-color: #ffffff; border: 1px solid #e6e8ef; border-radius: 16px; overflow: hidden;">
                    <tr>
                        <td style="background-color: #e85a1c; background-image: linear-gradient(135deg, #ffa066 0%, #e85a1c 100%); padding: 32px; text-align: center;">
                            <span style="color: #ffffff; font-size: 22px; font-weight: 700;">{len(ideas)} post ideas{brand} this week</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 28px 32px 20px 32px; color: #172033; font-size: 15px; line-height: 1.6;">
                            Hi {_escape(name)},<br><br>
                            Here is what's worth posting about this week, picked for your industry and written for your brand.
                            Choose one and the brief is ready to go.
                        </td>
                    </tr>{cards}
                    <tr>
                        <td align="center" style="padding: 8px 32px 28px 32px;">
                            <a href="{_escape(dashboard_url)}" style="color: #c2410c; font-size: 14px; font-weight: 700; text-decoration: none;">See all your ideas in AVIR AI &rarr;</a>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f8f9fc; border-top: 1px solid #e6e8ef; color: #667085; font-size: 12px; line-height: 1.6;">
                            You get this email once a week because you have an AVIR AI account.
                            <a href="{_escape(settings_url)}" style="color: #c2410c; text-decoration: none;">Email settings</a> &middot;
                            <a href="{_escape(unsubscribe_url)}" style="color: #c2410c; text-decoration: none;">Unsubscribe</a>
                            <div style="margin-top: 8px; color: #9ca3af; font-size: 11px;">AVIR AI is a product of <a href="https://stradit.com/" style="color: #c2410c; text-decoration: none; font-weight: 600;">StradIT</a>.</div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


def _build_nudge_html(name: str, headline: str, intro: str, idea: dict, settings_url: str, unsubscribe_url: str) -> str:
    """A timely nudge (services/nudge_service.py): one idea {title, summary,
    makes, url} whose button opens Studio Chat with the brief filled in."""
    return f"""<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background-color: #f8f9fc; font-family: 'Segoe UI', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8f9fc; padding: 32px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                       style="background-color: #ffffff; border: 1px solid #e6e8ef; border-radius: 16px; overflow: hidden;">
                    <tr>
                        <td style="background-color: #e85a1c; background-image: linear-gradient(135deg, #ffa066 0%, #e85a1c 100%); padding: 32px; text-align: center;">
                            <span style="color: #ffffff; font-size: 22px; font-weight: 700;">{_escape(headline)}</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 28px 32px 20px 32px; color: #172033; font-size: 15px; line-height: 1.6;">
                            Hi {_escape(name)},<br><br>
                            {_escape(intro)}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 0 32px 28px 32px;">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                                   style="border: 1px solid #e6e8ef; border-radius: 12px;">
                                <tr>
                                    <td style="padding: 18px 20px;">
                                        <div style="color: #172033; font-size: 17px; font-weight: 700; line-height: 1.35;">{_escape(idea.get("title"))}</div>
                                        <div style="color: #475569; font-size: 14px; line-height: 1.55; margin-top: 6px;">{_escape(idea.get("summary"))}</div>
                                        <div style="color: #667085; font-size: 12px; margin-top: 10px;">{_escape(idea.get("makes"))}</div>
                                        <div style="margin-top: 14px;">
                                            <a href="{_escape(idea.get("url"))}"
                                               style="display: inline-block; background-color: #c2410c; color: #ffffff; font-size: 14px; font-weight: 700; text-decoration: none; padding: 10px 20px; border-radius: 6px;">
                                                Create this post
                                            </a>
                                        </div>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f8f9fc; border-top: 1px solid #e6e8ef; color: #667085; font-size: 12px; line-height: 1.6;">
                            You get occasional reminders like this because you have an AVIR AI account.
                            <a href="{_escape(settings_url)}" style="color: #c2410c; text-decoration: none;">Email settings</a> &middot;
                            <a href="{_escape(unsubscribe_url)}" style="color: #c2410c; text-decoration: none;">Unsubscribe</a>
                            <div style="margin-top: 8px; color: #9ca3af; font-size: 11px;">AVIR AI is a product of <a href="https://stradit.com/" style="color: #c2410c; text-decoration: none; font-weight: 600;">StradIT</a>.</div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


def _build_monthly_recap_html(
    name: str, company_name: str | None, stats: dict, tips: list[str], ideas: list[dict],
    calendar_url: str, settings_url: str, unsubscribe_url: str,
) -> str:
    """The monthly recap (services/recap_service.py). stats: posts, images,
    videos, published, platform_names, comparison, weeks, weeks_goal_met,
    month_name. ideas: [{title, summary, origin, makes, url}] like the weekly email."""
    def tile(value, label):
        return f"""
                                    <td width="25%" align="center" style="padding: 14px 4px; border: 1px solid #e6e8ef; border-radius: 10px;">
                                        <div style="color: #172033; font-size: 24px; font-weight: 700;">{value}</div>
                                        <div style="color: #667085; font-size: 12px; margin-top: 2px;">{label}</div>
                                    </td>"""

    tiles = "".join([
        tile(stats.get("posts", 0), "posts created"), tile(stats.get("images", 0), "with an image"),
        tile(stats.get("videos", 0), "with a video"), tile(stats.get("published", 0), "published"),
    ])
    platforms = ", ".join(_escape(p) for p in stats.get("platform_names") or [])
    goal_line = (
        f"You reached your weekly goal in <strong>{stats['weeks_goal_met']} of {stats['weeks']}</strong> weeks."
        if stats.get("weeks") else ""
    )
    tips_html = "".join(
        f'<div style="color: #475569; font-size: 14px; line-height: 1.55; margin-top: 8px;">&bull; {_escape(tip)}</div>'
        for tip in tips
    )
    ideas_html = "".join(
        f"""
                    <tr>
                        <td style="padding: 0 32px 14px 32px;">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                                   style="border: 1px solid #e6e8ef; border-radius: 12px;">
                                <tr>
                                    <td style="padding: 16px 20px;">
                                        <div style="color: #c2410c; font-size: 12px; font-weight: 700;">{_escape(idea.get("origin"))}</div>
                                        <div style="color: #172033; font-size: 16px; font-weight: 700; line-height: 1.35; margin-top: 5px;">{_escape(idea.get("title"))}</div>
                                        <div style="color: #475569; font-size: 14px; line-height: 1.55; margin-top: 5px;">{_escape(idea.get("summary"))}</div>
                                        <div style="color: #667085; font-size: 12px; margin-top: 8px;">{_escape(idea.get("makes"))}</div>
                                        <div style="margin-top: 12px;">
                                            <a href="{_escape(idea.get("url"))}"
                                               style="display: inline-block; background-color: #c2410c; color: #ffffff; font-size: 14px; font-weight: 700; text-decoration: none; padding: 10px 20px; border-radius: 6px;">
                                                Create this post
                                            </a>
                                        </div>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>"""
        for idea in ideas
    )
    next_heading = (
        """
                    <tr>
                        <td style="padding: 8px 32px 12px 32px; color: #172033; font-size: 16px; font-weight: 700;">What to try next</td>
                    </tr>"""
        if ideas or tips else ""
    )
    tips_row = (
        f"""
                    <tr>
                        <td style="padding: 0 32px 18px 32px;">{tips_html}</td>
                    </tr>"""
        if tips else ""
    )
    brand = f" at {_escape(company_name)}" if company_name else ""
    return f"""<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background-color: #f8f9fc; font-family: 'Segoe UI', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8f9fc; padding: 32px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                       style="background-color: #ffffff; border: 1px solid #e6e8ef; border-radius: 16px; overflow: hidden;">
                    <tr>
                        <td style="background-color: #e85a1c; background-image: linear-gradient(135deg, #ffa066 0%, #e85a1c 100%); padding: 32px; text-align: center;">
                            <span style="color: #ffffff; font-size: 22px; font-weight: 700;">Your {_escape(stats.get("month_name"))}{brand}</span>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 28px 32px 18px 32px; color: #172033; font-size: 15px; line-height: 1.6;">
                            Hi {_escape(name)},<br><br>
                            You created <strong>{stats.get("posts", 0)} {"post" if stats.get("posts") == 1 else "posts"}</strong> in {_escape(stats.get("month_name"))}.
                            {_escape(stats.get("comparison"))}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 0 32px 16px 32px;">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="8" style="border-collapse: separate; margin: 0 -8px; width: calc(100% + 16px);">
                                <tr>{tiles}
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 0 32px 22px 32px; color: #475569; font-size: 14px; line-height: 1.6;">
                            {("Written for " + platforms + ". ") if platforms else ""}{goal_line}
                            <a href="{_escape(calendar_url)}" style="color: #c2410c; font-weight: 700; text-decoration: none;">Open your calendar &rarr;</a>
                        </td>
                    </tr>{next_heading}{tips_row}{ideas_html}
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f8f9fc; border-top: 1px solid #e6e8ef; color: #667085; font-size: 12px; line-height: 1.6;">
                            You get this recap once a month because you have an AVIR AI account.
                            <a href="{_escape(settings_url)}" style="color: #c2410c; text-decoration: none;">Email settings</a> &middot;
                            <a href="{_escape(unsubscribe_url)}" style="color: #c2410c; text-decoration: none;">Unsubscribe</a>
                            <div style="margin-top: 8px; color: #9ca3af; font-size: 11px;">AVIR AI is a product of <a href="https://stradit.com/" style="color: #c2410c; text-decoration: none; font-weight: 600;">StradIT</a>.</div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


def _build_invitation_html(
    name: str | None, inviter_name: str | None, accept_url: str, message: str | None, expires_days: int
) -> str:
    """Admin -> Invitations. Same look as the password-reset email (brand
    gradient header, white card, purple button)."""
    first_name = (name or "").strip().split(" ")[0]
    greeting = f"Hi {_escape(first_name)}," if first_name else "Hi there,"
    inviter = _escape(inviter_name) if inviter_name else "The AVIR AI team"
    message_block = (
        f"""
                    <tr>
                        <td style="padding: 0 32px 8px 32px;">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                                   style="background-color: #fff4ec; border-left: 3px solid #e85a1c; border-radius: 8px;">
                                <tr>
                                    <td style="padding: 14px 16px; color: #3b3355; font-size: 14px; line-height: 1.6;">
                                        <div style="font-size: 12px; font-weight: 700; color: #c2410c; margin-bottom: 4px;">A note from {inviter}</div>
                                        {_escape(message)}
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>"""
        if message
        else ""
    )
    return f"""<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background-color: #f8f9fc; font-family: 'Segoe UI', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8f9fc; padding: 32px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                       style="background-color: #ffffff; border: 1px solid #e6e8ef; border-radius: 16px; overflow: hidden;">
                    <tr>
                        <td style="background-color: #e85a1c; background-image: linear-gradient(135deg, #ffa066 0%, #e85a1c 100%); padding: 32px; text-align: center;">
                            <div style="color: #ffffff; font-size: 13px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; opacity: 0.85;">AVIR AI</div>
                            <div style="color: #ffffff; font-size: 22px; font-weight: 700; margin-top: 6px;">You're invited to AVIR AI</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 32px 32px 16px 32px; color: #172033; font-size: 15px; line-height: 1.6;">
                            {greeting}<br><br>
                            <strong>{inviter}</strong> has invited you to join <strong>AVIR AI</strong> - the AI studio that turns
                            one brief into on-brand posts, captions, hashtags and images for every social channel.
                        </td>
                    </tr>{message_block}
                    <tr>
                        <td style="padding: 8px 32px 0 32px; color: #475467; font-size: 14px; line-height: 1.7;">
                            With your account you can:
                            <ul style="margin: 8px 0 0 0; padding-left: 20px;">
                                <li>Create posts for LinkedIn, Instagram and Facebook in minutes</li>
                                <li>Keep every caption and image on-brand automatically</li>
                                <li>Send content for approval before it goes live</li>
                            </ul>
                        </td>
                    </tr>
                    <tr>
                        <td align="center" style="padding: 28px 32px 32px 32px;">
                            <a href="{_escape(accept_url)}"
                               style="display: inline-block; background-color: #c2410c; color: #ffffff; font-size: 15px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 6px;">
                                Accept invitation
                            </a>
                            <div style="color: #667085; font-size: 12px; margin-top: 12px;">This invitation expires in {expires_days} days and can only be used once.</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f8f9fc; border-top: 1px solid #e6e8ef; color: #667085; font-size: 12px; line-height: 1.6;">
                            Button not working? Copy this link into your browser:<br>
                            <a href="{_escape(accept_url)}" style="color: #c2410c; word-break: break-all;">{_escape(accept_url)}</a><br><br>
                            If you weren't expecting this invitation, you can safely ignore this email.
                            <div style="margin-top: 8px; color: #9ca3af; font-size: 11px;">AVIR AI is a product of <a href="https://stradit.com/" style="color: #c2410c; text-decoration: none; font-weight: 600;">StradIT</a></div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


def _build_sales_lead_html(user_name: str, user_email: str, company_name: str, phone: str | None, message: str | None) -> str:
    phone_row = (
        f"""<tr><td style="padding: 4px 0; color: #6b7280; font-size: 13px;"><strong style="color: #374151;">Phone:</strong> {_escape(phone)}</td></tr>"""
        if phone
        else ""
    )
    message_block = (
        f"""
                    <tr>
                        <td style="padding: 20px 32px 32px 32px;">
                            <div style="font-size: 13px; font-weight: 700; color: #c2410c; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 8px;">Message</div>
                            <div style="background-color: #f9fafb; border-left: 3px solid #c2410c; border-radius: 8px; padding: 16px; font-size: 14px; line-height: 1.6; color: #1f2937; white-space: pre-wrap;">{_escape(message)}</div>
                        </td>
                    </tr>
        """
        if message
        else ""
    )
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: 'Segoe UI', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f3f4f6; padding: 32px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="600" cellpadding="0" cellspacing="0"
                       style="background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.06);">
                    <tr>
                        <td style="background-color: #e85a1c; height: 5px; line-height: 5px; font-size: 0;">&nbsp;</td>
                    </tr>
                    <tr>
                        <td style="background-color: #e85a1c; background-image: linear-gradient(135deg, #ffa066 0%, #e85a1c 100%); padding: 26px 32px;">
                            <span style="color: #ffffff; font-size: 20px; font-weight: 700;">New Enterprise Lead</span>
                            <div style="color: rgba(255,255,255,0.85); font-size: 13px; margin-top: 2px;">AVIR AI &mdash; Onboarding</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 24px 32px 8px 32px;">
                            <table role="presentation" cellpadding="0" cellspacing="0">
                                <tr><td style="padding: 4px 0; color: #6b7280; font-size: 13px;"><strong style="color: #374151;">Company:</strong> {_escape(company_name)}</td></tr>
                                <tr><td style="padding: 4px 0; color: #6b7280; font-size: 13px;"><strong style="color: #374151;">Contact:</strong> {_escape(user_name)} ({_escape(user_email)})</td></tr>
                                {phone_row}
                            </table>
                        </td>
                    </tr>
                    {message_block}
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f9fafb; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 12px;">
                            Submitted from the Enterprise onboarding step.
                            <div style="margin-top: 8px; color: #9ca3af; font-size: 11px;">AVIR AI is a product of <a href="https://stradit.com/" style="color: #c2410c; text-decoration: none; font-weight: 600;">StradIT</a></div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


# What the email being sent was built from, for the email history (Admin ->
# Emails). Set by the code that triggers an email, read by EmailService._deliver:
#   with email_context(kind="festival_idea", details={...}, triggered_by="admin:3"):
#       EmailService().send_nudge_email(...)
_email_context: contextvars.ContextVar[dict | None] = contextvars.ContextVar("email_context", default=None)


@contextlib.contextmanager
def email_context(kind: str | None = None, details: dict | None = None, triggered_by: str | None = None,
                  user_id: int | None = None):
    previous = _email_context.get() or {}
    token = _email_context.set({**previous, **{k: v for k, v in {
        "kind": kind, "details": details, "triggered_by": triggered_by, "user_id": user_id}.items() if v is not None}})
    try:
        yield
    finally:
        _email_context.reset(token)


# ── Multi-platform post approval (Studio Chat "Send for approval") ──────────

_PLATFORM_NAMES = {"linkedin": "LinkedIn", "instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}
_PLATFORM_COLORS = {"linkedin": "#0a66c2", "instagram": "#d62976", "facebook": "#1877f2", "youtube": "#dc2626"}
_EMAIL_SLIDES = 6  # slides shown per carousel in the email; the review page has all of them


def _thumbnail(path: str, max_side: int = 560) -> bytes | None:
    """A small JPEG of an image for the email body (a 10-slide carousel at full size would be several MB)."""
    import io

    from PIL import Image

    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((max_side, max_side))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=82)
            return out.getvalue()
    except Exception:  # noqa: BLE001 - a broken image is left out, the rest of the email still works
        return None


def _bundle_media_html(item: dict, cids: dict) -> str:
    images = [u for u in item.get("images") or [] if u in cids]
    if not images:
        return ""
    if item.get("media") != "carousel":
        return (f'<img src="cid:{cids[images[0]]}" alt="Post image" width="536" '
                f'style="width: 100%; max-width: 536px; border-radius: 10px; border: 1px solid #e5e7eb; display: block;">')
    shown, more = images[:_EMAIL_SLIDES], len(images) - _EMAIL_SLIDES
    titles = item.get("slide_titles") or []
    cells = []
    for i, url in enumerate(shown):
        cells.append(
            f'<td width="33%" valign="top" style="padding: 4px;">'
            f'<img src="cid:{cids[url]}" alt="Slide {i + 1}" width="170" style="width: 100%; border-radius: 8px; border: 1px solid #e5e7eb; display: block;">'
            f'<div style="font-size: 11px; color: #6b7280; padding-top: 4px;"><strong style="color: #374151;">{i + 1}/{len(images)}</strong> '
            f"{_escape(titles[i] if i < len(titles) else '')}</div></td>"
        )
    rows = "".join("<tr>" + "".join(cells[r:r + 3]) + "</tr>" for r in range(0, len(cells), 3))
    note = (f'<div style="font-size: 12px; color: #6b7280; padding: 6px 4px 0;">+ {more} more slide{"s" if more != 1 else ""} '
            "on the review page</div>") if more > 0 else ""
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>{note}'


def _build_bundle_request_html(approval_url: str, story: str | None, items: list[dict], company: str | None) -> tuple[str, list]:
    """(html, [(cid, jpeg bytes)]) - one section per platform."""
    cids, inline = {}, []
    for item in items:
        for url in (item.get("images") or [])[: (_EMAIL_SLIDES if item.get("media") == "carousel" else 1)]:
            if url in cids:
                continue
            local = _resolve_local_path(url)
            data = _thumbnail(local) if local else None
            if data:
                cids[url] = f"img_{len(cids)}"
                inline.append((cids[url], data))

    sections = []
    for item in items:
        platform = item.get("platform") or ""
        name = _PLATFORM_NAMES.get(platform, platform.capitalize())
        color = _PLATFORM_COLORS.get(platform, "#374151")
        count = len(item.get("images") or [])
        kind = (
            f"Carousel &middot; {count} slides" + (
                " &middot; " + ("Document (PDF)" if item.get("linkedin_format") == "document" else "Multi-image post")
                if platform == "linkedin" else "")
            if item.get("media") == "carousel" else ("Single image" if count else "Text only")
        )
        tags = " ".join(item.get("hashtags") or [])
        flags = (item.get("compliance") or {}).get("flags") or []
        compliance = (
            f'<div style="margin-top: 10px; font-size: 12px; color: #b45309; background-color: #fffbeb; border-radius: 8px; padding: 8px 10px;">'
            f"&#9888; {len(flags)} compliance note{'s' if len(flags) != 1 else ''} to check on the review page</div>"
        ) if flags else ""
        pdf = ('<div style="margin-top: 8px; font-size: 12px; color: #374151;">&#128206; The PDF LinkedIn will show is attached.</div>'
               if item.get("pdf_url") else "")
        caption_html = _escape(item.get("caption") or "").replace("\n", "<br>") or "<em>No caption.</em>"
        sections.append(f"""
                    <tr>
                        <td style="padding: 18px 32px 6px 32px;">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border: 1px solid #e5e7eb; border-radius: 12px;">
                                <tr><td style="padding: 14px 16px 8px 16px;">
                                    <span style="display: inline-block; background-color: {color}; color: #ffffff; font-size: 12px; font-weight: 700; padding: 4px 12px; border-radius: 999px;">{_escape(name)}</span>
                                    <span style="font-size: 12px; color: #6b7280; padding-left: 8px;">{kind}</span>
                                </td></tr>
                                <tr><td style="padding: 4px 12px 8px 12px;">{_bundle_media_html(item, cids)}</td></tr>
                                <tr><td style="padding: 4px 16px 16px 16px; font-size: 14px; line-height: 1.6; color: #1f2937;">
                                    {caption_html}
                                    {f'<div style="margin-top: 8px; color: #1d4ed8; font-size: 13px; font-weight: 600;">{_escape(tags)}</div>' if tags else ''}
                                    {pdf}{compliance}
                                </td></tr>
                            </table>
                        </td>
                    </tr>""")

    platforms = ", ".join(_PLATFORM_NAMES.get(i.get("platform"), i.get("platform", "")) for i in items)
    story_html = _escape((story or "")[:600]).replace("\n", "<br>")
    html = f"""\
<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: 'Segoe UI', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f3f4f6; padding: 32px 0;">
        <tr>
            <td align="center">
                <table role="presentation" width="600" cellpadding="0" cellspacing="0"
                       style="background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.06);">
                    <tr>
                        <td style="background-color: #e85a1c; background-image: linear-gradient(135deg, #ffa066 0%, #e85a1c 100%); padding: 26px 32px;">
                            <span style="color: #ffffff; font-size: 20px; font-weight: 700;">Review requested</span>
                            <div style="color: rgba(255,255,255,0.9); font-size: 13px; margin-top: 4px;">
                                {_escape(company or "A post")} for {len(items)} platform{"s" if len(items) != 1 else ""}: {_escape(platforms)}
                            </div>
                        </td>
                    </tr>
                    {f'<tr><td style="padding: 20px 32px 0 32px; font-size: 13px; color: #6b7280;"><strong style="color: #374151;">Brief:</strong> {story_html}</td></tr>' if story_html else ''}
                    {"".join(sections)}
                    <tr>
                        <td align="center" style="padding: 24px 32px 32px 32px;">
                            <a href="{_escape(approval_url)}"
                               style="display: inline-block; background-color: #c2410c; color: #ffffff; font-size: 15px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 999px;">
                                Review &amp; decide
                            </a>
                            <div style="color: #9ca3af; font-size: 12px; margin-top: 12px;">Approve every platform at once, or each one separately with comments. Sign-in required.</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f9fafb; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 12px;">
                            Sent from AVIR AI Studio Chat's approval workflow.
                            <div style="margin-top: 8px; color: #9ca3af; font-size: 11px;">AVIR AI is a product of <a href="https://stradit.com/" style="color: #c2410c; text-decoration: none; font-weight: 600;">StradIT</a></div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""
    return html, inline


def _build_approval_decision_html(name: str, status: str, items: list[dict], comments: str | None,
                                  decided_by: str | None, review_url: str) -> str:
    headline = {"approved": "Your post was approved", "rejected": "Changes were requested",
                "partial": "Your post was partly approved"}.get(status, "Your post was reviewed")
    rows = "".join(
        f"""<tr><td style="padding: 8px 0; border-bottom: 1px solid #f3f4f6; font-size: 14px;">
                <strong>{_escape(_PLATFORM_NAMES.get(i.get("platform"), i.get("platform", "")))}</strong>
                <span style="float: right; font-size: 12px; font-weight: 700; color: {'#047857' if i.get('decision') == 'approved' else '#b45309'};">
                    {'&#10003; Approved' if i.get('decision') == 'approved' else '&#9998; Changes requested'}</span>
                {f'<div style="color: #4b5563; font-size: 13px; margin-top: 4px;">&ldquo;{_escape(i["comment"])}&rdquo;</div>' if i.get("comment") else ''}
            </td></tr>"""
        for i in items
    )
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: 'Segoe UI', Arial, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #f3f4f6; padding: 32px 0;">
        <tr><td align="center">
            <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 12px; overflow: hidden;">
                <tr><td style="background-color: #e85a1c; background-image: linear-gradient(135deg, #ffa066 0%, #e85a1c 100%); padding: 24px 32px;">
                    <span style="color: #ffffff; font-size: 20px; font-weight: 700;">{headline}</span>
                    <div style="color: rgba(255,255,255,0.9); font-size: 13px; margin-top: 4px;">Hi {_escape(name)}{f", {_escape(decided_by)} reviewed it" if decided_by else ""}.</div>
                </td></tr>
                <tr><td style="padding: 20px 32px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>
                    {f'<div style="margin-top: 14px; font-size: 13px; color: #374151;"><strong>Comments:</strong> {_escape(comments)}</div>' if comments else ''}
                </td></tr>
                <tr><td align="center" style="padding: 8px 32px 28px 32px;">
                    <a href="{_escape(review_url)}" style="display: inline-block; background-color: #c2410c; color: #ffffff; font-size: 15px; font-weight: 700; text-decoration: none; padding: 12px 28px; border-radius: 999px;">Open the review</a>
                </td></tr>
                <tr><td style="padding: 14px 32px; background-color: #f9fafb; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 11px;">
                    AVIR AI is a product of <a href="https://stradit.com/" style="color: #c2410c; text-decoration: none; font-weight: 600;">StradIT</a></td></tr>
            </table>
        </td></tr>
    </table>
</body>
</html>
"""


class EmailService:
    def __init__(self):
        self.host = Config.SMTP_HOST
        self.port = Config.SMTP_PORT
        self.username = Config.SMTP_USERNAME
        self.password = Config.SMTP_PASSWORD
        self.from_email = Config.SMTP_FROM_EMAIL
        self.enabled = bool(self.host and self.username and self.password)

    def _deliver(self, msg, recipients: list[str], kind: str) -> None:
        """Sends over SMTP and records the attempt (sent / failed + error) in
        the email history. Failures are re-raised for the caller to handle."""
        context = _email_context.get() or {}
        entry = {"kind": context.get("kind") or kind, "subject": str(msg["Subject"] or ""),
                 "details": context.get("details"), "triggered_by": context.get("triggered_by"),
                 "user_id": context.get("user_id")}
        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.sendmail(self.from_email, recipients, msg.as_string())
        except Exception as err:
            _log_attempt(recipients, entry, "failed", str(err))
            raise
        _log_attempt(recipients, entry, "sent")

    def send_approval_notification(
        self,
        story: str | None,
        platform: str | None,
        competitors: list[str] | None = None,
        caption: str | None = None,
        asset_type: str | None = None,
        image_path: str | None = None,
        image_paths: list[str] | None = None,
        slide_titles: list[str] | None = None,
        to_email: str | None = None,
    ) -> dict:
        """image_paths (plural) sends a multi-slide carousel - each slide is
        embedded inline in order plus attached separately for download.
        image_path (singular) remains supported for the single-image case."""
        if not self.enabled:
            raise RuntimeError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env."
            )

        recipient = to_email or Config.APPROVAL_NOTIFY_EMAIL
        if not recipient:
            raise RuntimeError("No recipient configured. Set APPROVAL_NOTIFY_EMAIL in .env.")

        paths = image_paths if image_paths else ([image_path] if image_path else [])
        slides = _load_image_slides(paths)

        msg = MIMEMultipart("mixed")
        # Competitor names are internal-only strategic context, not public-facing
        # metadata - keep them out of the subject line (they still appear in the
        # "Source competitors" line inside the body, which is fine).
        msg["Subject"] = f"Content Approved: {(platform or 'content').capitalize()} Post - {(asset_type or 'content').capitalize()}"
        msg["From"] = self.from_email
        msg["To"] = recipient

        html = _build_html(story, platform, competitors, caption, asset_type, len(slides), slide_titles)
        _attach_slides(msg, html, slides)

        self._deliver(msg, [recipient], "approval_notification")

        return {"success": True, "recipient": recipient, "image_attached": len(slides) > 0, "slide_count": len(slides)}

    def send_approval_request(
        self,
        approval_url: str,
        story: str | None,
        platform: str | None,
        competitors: list[str] | None = None,
        caption: str | None = None,
        asset_type: str | None = None,
        image_paths: list[str] | None = None,
        to_email: str | None = None,
    ) -> dict:
        """Sends a review request (distinct from send_approval_notification's
        after-the-fact "FYI, this was approved" email) - the reviewer must
        open approval_url and sign in to actually accept or reject."""
        if not self.enabled:
            raise RuntimeError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env."
            )

        recipient = to_email or Config.APPROVAL_NOTIFY_EMAIL
        if not recipient:
            raise RuntimeError("No recipient configured. Set APPROVAL_NOTIFY_EMAIL in .env.")

        slides = _load_image_slides(image_paths or [])

        msg = MIMEMultipart("mixed")
        msg["Subject"] = f"Review Requested: {(platform or 'content').capitalize()} Post - {(asset_type or 'content').capitalize()}"
        msg["From"] = self.from_email
        msg["To"] = recipient

        html = _build_request_html(approval_url, story, platform, competitors, caption, asset_type, len(slides))
        _attach_slides(msg, html, slides)

        self._deliver(msg, [recipient], "approval_request")

        return {"success": True, "recipient": recipient}

    def send_approval_bundle_request(self, approval_url: str, story: str | None, items: list[dict],
                                     company: str | None, to_email: str) -> dict:
        """One review email for a whole post: a section per platform (image or
        carousel slides, caption, hashtags); a LinkedIn document carousel's PDF attached."""
        if not self.enabled:
            raise RuntimeError("SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env.")
        from email.mime.application import MIMEApplication

        html, inline = _build_bundle_request_html(approval_url, story, items, company)
        msg = MIMEMultipart("mixed")
        names = ", ".join(_PLATFORM_NAMES.get(i.get("platform"), i.get("platform", "")) for i in items)
        msg["Subject"] = f"Review requested: {company + ' - ' if company else ''}{names} post"
        msg["From"] = self.from_email
        msg["To"] = to_email
        related = MIMEMultipart("related")
        related.attach(MIMEText(html, "html"))
        for cid, data in inline:
            part = MIMEImage(data, _subtype="jpeg")
            part.add_header("Content-ID", f"<{cid}>")
            part.add_header("Content-Disposition", "inline", filename=f"{cid}.jpg")
            related.attach(part)
        msg.attach(related)
        for item in items:
            local = _resolve_local_path(item.get("pdf_url")) if item.get("pdf_url") else None
            if local:
                with open(local, "rb") as f:
                    pdf = MIMEApplication(f.read(), _subtype="pdf")
                pdf.add_header("Content-Disposition", "attachment", filename="linkedin-carousel.pdf")
                msg.attach(pdf)
        self._deliver(msg, [to_email], "approval_request")
        return {"success": True, "recipient": to_email, "images": len(inline)}

    def send_approval_decision_email(self, to_email: str, name: str, status: str, items: list[dict],
                                     comments: str | None, decided_by: str | None, review_url: str) -> dict:
        """Tells the person who asked for approval what the reviewer decided, per platform."""
        if not self.enabled:
            raise RuntimeError("SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env.")
        msg = MIMEMultipart("mixed")
        msg["Subject"] = {"approved": "Approved: your post is ready to publish",
                          "rejected": "Changes requested on your post",
                          "partial": "Your post was partly approved"}.get(status, "Your post was reviewed") + " - AVIR AI"
        msg["From"] = self.from_email
        msg["To"] = to_email
        msg.attach(MIMEText(_build_approval_decision_html(name, status, items, comments, decided_by, review_url), "html"))
        self._deliver(msg, [to_email], "approval_decision")
        return {"success": True, "recipient": to_email}

    def send_welcome_verification_email(self, to_email: str, name: str, verify_url: str) -> dict:
        """Sent right after registration - see auth/routes.py's register()."""
        if not self.enabled:
            raise RuntimeError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env."
            )

        msg = MIMEMultipart("mixed")
        msg["Subject"] = "Verify your email - AVIR AI"
        msg["From"] = self.from_email
        msg["To"] = to_email
        msg.attach(MIMEText(_build_verification_html(name, verify_url), "html"))

        self._deliver(msg, [to_email], "verification")

        return {"success": True, "recipient": to_email}

    def send_password_reset_email(self, to_email: str, name: str, reset_url: str, ttl_minutes: int) -> dict:
        """Sent by auth/routes.py's forgot_password()."""
        if not self.enabled:
            raise RuntimeError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env."
            )

        msg = MIMEMultipart("mixed")
        msg["Subject"] = "Reset your password - AVIR AI"
        msg["From"] = self.from_email
        msg["To"] = to_email
        msg.attach(MIMEText(_build_password_reset_html(name, reset_url, ttl_minutes), "html"))

        self._deliver(msg, [to_email], "password_reset")

        return {"success": True, "recipient": to_email}

    def send_credit_decision_email(
        self, to_email: str, name: str, approved: bool, requested_amount: float,
        credit_limit: float | None, dashboard_url: str,
    ) -> dict:
        """Sent when an admin approves or rejects a credit extension request
        (api/routes.py admin_approve_request / admin_reject_request)."""
        if not self.enabled:
            raise RuntimeError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env."
            )

        msg = MIMEMultipart("mixed")
        msg["Subject"] = (
            "Your credit request was approved - AVIR AI" if approved else "Update on your credit request - AVIR AI"
        )
        msg["From"] = self.from_email
        msg["To"] = to_email
        msg.attach(MIMEText(
            _build_credit_decision_html(name, approved, requested_amount, credit_limit, dashboard_url), "html"
        ))

        self._deliver(msg, [to_email], "credit_decision")

        return {"success": True, "recipient": to_email}

    def send_weekly_ideas_email(
        self, to_email: str, name: str, company_name: str | None, ideas: list[dict],
        dashboard_url: str, settings_url: str, unsubscribe_url: str,
    ) -> dict:
        """The weekly ideas email (services/idea_digest_service.py)."""
        if not self.enabled:
            raise RuntimeError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env."
            )

        msg = MIMEMultipart("mixed")
        msg["Subject"] = f"{len(ideas)} post ideas for this week - AVIR AI"
        msg["From"] = self.from_email
        msg["To"] = to_email
        # Lets mail apps show their own one-click unsubscribe button
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.attach(MIMEText(
            _build_weekly_ideas_html(name, company_name, ideas, dashboard_url, settings_url, unsubscribe_url), "html"
        ))

        self._deliver(msg, [to_email], "weekly_ideas")

        return {"success": True, "recipient": to_email}

    def send_nudge_email(
        self, to_email: str, name: str, headline: str, intro: str, idea: dict, settings_url: str, unsubscribe_url: str
    ) -> dict:
        """A timely nudge with one idea (services/nudge_service.py)."""
        if not self.enabled:
            raise RuntimeError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env."
            )

        msg = MIMEMultipart("mixed")
        msg["Subject"] = f"{headline} - AVIR AI"
        msg["From"] = self.from_email
        msg["To"] = to_email
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.attach(MIMEText(_build_nudge_html(name, headline, intro, idea, settings_url, unsubscribe_url), "html"))

        self._deliver(msg, [to_email], "nudge")

        return {"success": True, "recipient": to_email}

    def send_monthly_recap_email(
        self, to_email: str, name: str, company_name: str | None, stats: dict, tips: list[str], ideas: list[dict],
        calendar_url: str, settings_url: str, unsubscribe_url: str,
    ) -> dict:
        """The monthly recap (services/recap_service.py)."""
        if not self.enabled:
            raise RuntimeError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env."
            )

        msg = MIMEMultipart("mixed")
        msg["Subject"] = f"Your {stats.get('month_name')} recap - AVIR AI"
        msg["From"] = self.from_email
        msg["To"] = to_email
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.attach(MIMEText(
            _build_monthly_recap_html(name, company_name, stats, tips, ideas, calendar_url, settings_url, unsubscribe_url),
            "html",
        ))

        self._deliver(msg, [to_email], "monthly_recap")

        return {"success": True, "recipient": to_email}

    def send_invitation_email(
        self,
        to_email: str,
        name: str | None,
        inviter_name: str | None,
        accept_url: str,
        message: str | None = None,
        expires_days: int = 7,
    ) -> dict:
        """Sent from Admin -> Invitations (api/routes.py /api/admin/invitations)."""
        if not self.enabled:
            raise RuntimeError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env."
            )

        from email.utils import formataddr, parseaddr

        msg = MIMEMultipart("mixed")
        inviter = inviter_name or "AVIR AI"
        msg["Subject"] = f"{inviter} invited you to AVIR AI"
        # Inbox shows "Janak via AVIR AI" as the sender (the address stays ours)
        sender = f"{inviter_name} via AVIR AI" if inviter_name else "AVIR AI"
        msg["From"] = formataddr((sender, parseaddr(self.from_email)[1] or self.from_email))
        msg["To"] = to_email
        msg.attach(
            MIMEText(_build_invitation_html(name, inviter_name, accept_url, message, expires_days), "html")
        )

        self._deliver(msg, [to_email], "invitation")

        return {"success": True, "recipient": to_email}

    def send_sales_lead_notification(self, user_name: str, user_email: str, lead: dict) -> dict:
        """Sent when an Enterprise signup submits the Contact Sales form - see
        api/routes.py's /api/onboarding/contact-sales."""
        if not self.enabled:
            raise RuntimeError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env."
            )

        recipient = Config.SALES_EMAIL
        if not recipient:
            raise RuntimeError("No recipient configured. Set SALES_EMAIL in .env.")

        msg = MIMEMultipart("mixed")
        msg["Subject"] = f"New Enterprise Lead: {lead.get('company_name', 'Unknown Company')}"
        msg["From"] = self.from_email
        msg["To"] = recipient
        html = _build_sales_lead_html(
            user_name, user_email, lead.get("company_name", ""), lead.get("phone"), lead.get("message")
        )
        msg.attach(MIMEText(html, "html"))

        self._deliver(msg, [recipient], "sales_lead")

        return {"success": True, "recipient": recipient}


def _log_attempt(recipients: list[str], entry: dict, status: str, error: str | None = None) -> None:
    try:
        from db import log_email

        for to_email in recipients:
            log_email(to_email, entry["kind"], entry["subject"], status, error=error, details=entry["details"],
                      triggered_by=entry["triggered_by"], user_id=entry["user_id"])
    except Exception:  # noqa: BLE001, S110 - no database (tests, CLI): the email itself still counts
        pass
