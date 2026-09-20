"""Approval-notification emails: sent when a generated asset is approved,
with the story/strategy context and the generated image attached. Plain
smtplib/email (stdlib) over STARTTLS - no new dependency needed."""

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
    return candidate if os.path.exists(candidate) else None


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
                        <td style="background-color: #4338ca; height: 5px; line-height: 5px; font-size: 0;">&nbsp;</td>
                    </tr>
                    <tr>
                        <td style="background-color: #4f46e5; background-image: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%); padding: 26px 32px;">
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
                                            VortexSocial AI &mdash; Analysis Dashboard
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
                                        <span style="display: inline-block; background-color: #eef2ff; color: #4338ca; font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 999px;">{_escape(platform_label)}</span>
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
                            <div style="font-size: 13px; font-weight: 700; color: #4338ca; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 8px;">Generated Caption</div>
                            <div style="background-color: #f9fafb; border-left: 3px solid #4f46e5; border-radius: 8px; padding: 16px; font-size: 14px; line-height: 1.6; color: #1f2937; white-space: pre-wrap;">{caption_html}</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 20px 32px 32px 32px;">
                            <div style="font-size: 13px; font-weight: 700; color: #4338ca; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 8px;">Story / Strategy Context</div>
                            <div style="background-color: #f9fafb; border-left: 3px solid #d1d5db; border-radius: 8px; padding: 16px; font-size: 13px; line-height: 1.6; color: #4b5563; white-space: pre-wrap; max-height: 400px; overflow: hidden;">{story_html}</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f9fafb; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 12px;">
                            This is an automated notification from the Analysis Dashboard's approval workflow.
                            {"Attached copies of the generated image(s) are included below." if slide_count > 0 else ""}
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
                        <td style="background-color: #4338ca; height: 5px; line-height: 5px; font-size: 0;">&nbsp;</td>
                    </tr>
                    <tr>
                        <td style="background-color: #4f46e5; background-image: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%); padding: 26px 32px;">
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
                                            VortexSocial AI &mdash; Analysis Dashboard
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
                                        <span style="display: inline-block; background-color: #eef2ff; color: #4338ca; font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 999px;">{_escape(platform_label)}</span>
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
                            <div style="font-size: 13px; font-weight: 700; color: #4338ca; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 8px;">Generated Caption</div>
                            <div style="background-color: #f9fafb; border-left: 3px solid #4f46e5; border-radius: 8px; padding: 16px; font-size: 14px; line-height: 1.6; color: #1f2937; white-space: pre-wrap;">{caption_html}</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 20px 32px 8px 32px;">
                            <div style="font-size: 13px; font-weight: 700; color: #4338ca; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 8px;">Story / Strategy Context</div>
                            <div style="background-color: #f9fafb; border-left: 3px solid #d1d5db; border-radius: 8px; padding: 16px; font-size: 13px; line-height: 1.6; color: #4b5563; white-space: pre-wrap; max-height: 400px; overflow: hidden;">{story_html}</div>
                        </td>
                    </tr>
                    <tr>
                        <td align="center" style="padding: 24px 32px 32px 32px;">
                            <a href="{_escape(approval_url)}"
                               style="display: inline-block; background-color: #4f46e5; color: #ffffff; font-size: 15px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 999px;">
                                Review &amp; Decide
                            </a>
                            <div style="color: #9ca3af; font-size: 12px; margin-top: 12px;">Opens the Analysis Dashboard - sign-in required.</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f9fafb; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 12px;">
                            This is an automated review request from the Analysis Dashboard's approval workflow.
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
                        <td style="background-color: #4338ca; height: 5px; line-height: 5px; font-size: 0;">&nbsp;</td>
                    </tr>
                    <tr>
                        <td style="background-color: #4f46e5; background-image: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%); padding: 32px; text-align: center;">
                            <span style="color: #ffffff; font-size: 22px; font-weight: 700;">Welcome to VortexSocial AI</span>
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
                               style="display: inline-block; background-color: #4f46e5; color: #ffffff; font-size: 15px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 999px;">
                                Verify my email
                            </a>
                            <div style="color: #9ca3af; font-size: 12px; margin-top: 12px;">This link expires in 24 hours.</div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 16px 32px; background-color: #f9fafb; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 12px;">
                            If you didn't create this account, you can safely ignore this email.
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
                            <div style="font-size: 13px; font-weight: 700; color: #4338ca; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 8px;">Message</div>
                            <div style="background-color: #f9fafb; border-left: 3px solid #4f46e5; border-radius: 8px; padding: 16px; font-size: 14px; line-height: 1.6; color: #1f2937; white-space: pre-wrap;">{_escape(message)}</div>
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
                        <td style="background-color: #4338ca; height: 5px; line-height: 5px; font-size: 0;">&nbsp;</td>
                    </tr>
                    <tr>
                        <td style="background-color: #4f46e5; background-image: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%); padding: 26px 32px;">
                            <span style="color: #ffffff; font-size: 20px; font-weight: 700;">New Enterprise Lead</span>
                            <div style="color: rgba(255,255,255,0.85); font-size: 13px; margin-top: 2px;">VortexSocial AI &mdash; Onboarding</div>
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
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
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

        with smtplib.SMTP(self.host, self.port, timeout=30) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.sendmail(self.from_email, [recipient], msg.as_string())

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

        with smtplib.SMTP(self.host, self.port, timeout=30) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.sendmail(self.from_email, [recipient], msg.as_string())

        return {"success": True, "recipient": recipient}

    def send_welcome_verification_email(self, to_email: str, name: str, verify_url: str) -> dict:
        """Sent right after registration - see auth/routes.py's register()."""
        if not self.enabled:
            raise RuntimeError(
                "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD in .env."
            )

        msg = MIMEMultipart("mixed")
        msg["Subject"] = "Verify your email - VortexSocial AI"
        msg["From"] = self.from_email
        msg["To"] = to_email
        msg.attach(MIMEText(_build_verification_html(name, verify_url), "html"))

        with smtplib.SMTP(self.host, self.port, timeout=30) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.sendmail(self.from_email, [to_email], msg.as_string())

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

        with smtplib.SMTP(self.host, self.port, timeout=30) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.sendmail(self.from_email, [recipient], msg.as_string())

        return {"success": True, "recipient": recipient}
