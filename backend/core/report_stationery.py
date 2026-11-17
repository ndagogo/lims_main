"""Place a centre's uploaded header and footer on a finished result PDF."""
import io
import os

from django.conf import settings


def _abs(rel):
    rel = (rel or '').replace('\\', '/').lstrip('/')
    if not rel:
        return ''
    path = os.path.join(settings.MEDIA_ROOT, rel)
    return path if os.path.isfile(path) else ''


def _is_pdf(path):
    return path.lower().endswith('.pdf')


def image_file(fmt, key):
    path = _abs((fmt or {}).get(key))
    if not path or _is_pdf(path):
        return ''
    return path


def pdf_file(fmt, key):
    path = _abs((fmt or {}).get(key))
    if path and _is_pdf(path):
        return path
    return ''


def draw_fitted_image(pdf, path, x, bottom, max_width, max_height):
    from reportlab.lib.utils import ImageReader
    try:
        pdf.drawImage(
            ImageReader(path), x, bottom,
            width=max_width, height=max_height,
            preserveAspectRatio=True, mask='auto', anchor='c',
        )
        return True
    except Exception:
        return False


def apply_pdf_stationery(pdf_bytes, fmt):
    """Underlay one-page PDF header/footer artwork. Images are drawn by the report itself."""
    header = pdf_file(fmt, 'header_file')
    footer = pdf_file(fmt, 'footer_file')
    if not header and not footer:
        return pdf_bytes
    try:
        from pypdf import PageObject, PdfReader, PdfWriter, Transformation
    except ImportError:
        return pdf_bytes
    try:
        report = PdfReader(io.BytesIO(pdf_bytes))
        header_src = PdfReader(header).pages[0] if header else None
        footer_src = PdfReader(footer).pages[0] if footer else None
        writer = PdfWriter()
        for page in report.pages:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            base = PageObject.create_blank_page(width=width, height=height)
            if header_src is not None:
                base.merge_transformed_page(header_src, _transform(header_src, width, height, 'top'))
            if footer_src is not None:
                base.merge_transformed_page(footer_src, _transform(footer_src, width, height, 'bottom'))
            base.merge_page(page)
            writer.add_page(base)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception:
        return pdf_bytes


def _transform(src, width, height, anchor):
    from pypdf import Transformation
    src_w = float(src.mediabox.width) or 1
    src_h = float(src.mediabox.height) or 1
    full_page = (src_h / src_w) > 1.05 and src_h > height * 0.7
    if full_page:
        scale = min(width / src_w, height / src_h)
        return Transformation().scale(scale, scale)
    scale = width / src_w
    target_h = min(src_h * scale, height * 0.22)
    scale = target_h / src_h
    y = (height - (src_h * scale)) if anchor == 'top' else 0
    return Transformation().scale(scale, scale).translate(0, y)
